"""Hermes'siz cevap yolunun kapıları.

Buradaki testlerin çoğu "Hermes'e devredilmeli" diyor. Sebep asimetri:
bilgi sorusu Hermes'e giderse yalnız pahalıya mal olur, cevap doğrudur.
Randevu isteği hafif yola düşerse ajan randevu açamaz ve hastayı çevirir.
Şüphe varsa pahalı ve doğru olanı seçiyoruz.
"""

import pytest

from app import hafif


def gecmis(*mesajlar) -> list[dict]:
    return [{"yon": "gelen", "mesaj": m} for m in mesajlar]


# ── Kapı 1: mesajdaki randevu sinyali ────────────────────────────

@pytest.mark.parametrize("mesaj", [
    "Randevu almak istiyorum",
    "randevumu iptal edin",
    "İptal etmek istiyorum",          # büyük İ — Python lower() burada tuzaklı
    "Yarına erteleyebilir miyiz?",
    "Müsait misiniz?",
    "Dişim çok ağrıyor",
    "Acil gelmem lazım",
    "Dolgum düştü",
    "Onaylıyorum",
])
def test_randevu_isteyen_mesaj_hermese_devredilir(mesaj, monkeypatch):
    monkeypatch.setattr(hafif.httpx, "post", _patlat)
    assert hafif.cevap_dene([], mesaj) is None


def test_buyuk_i_harfi_sinyali_kacirmaz():
    """'İptal'.lower() → 'i̇ptal' (birleşik noktalı i). Düz `in` araması
    kaçırırdı ve iptal isteği hafif yola düşerdi."""
    assert hafif._randevu_sinyali("İPTAL ETMEK İSTİYORUM")
    assert hafif._randevu_sinyali("Iptal")


# ── Kapı 2: konuşma bağlamı ──────────────────────────────────────

def test_randevu_konusmasinin_ortasi_hermeste_kalir(monkeypatch):
    """'14:00 olsun' tek başına sinyal taşımıyor, bağlam taşıyor."""
    monkeypatch.setattr(hafif.httpx, "post", _patlat)
    assert hafif.cevap_dene(gecmis("Randevu almak istiyorum"), "14:00 olsun") is None


def test_calisma_saati_sorusu_hafif_yola_duser(monkeypatch):
    """'saat' bilerek sinyal listesinde değil — kaçırmak istemediğimiz
    soruların çoğu bu kelimeyi içeriyor."""
    _sahte_api(monkeypatch, "Hafta içi ve cumartesi 09:00-18:00 açığız.")
    sonuc = hafif.cevap_dene([], "Çalışma saatleriniz nedir?")
    assert sonuc is not None and "09:00" in sonuc[0]


# ── Kapı 3: modelin kendi devretme işareti ───────────────────────

def test_model_devret_derse_hermese_gider(monkeypatch):
    _sahte_api(monkeypatch, "[RANDEVU]")
    assert hafif.cevap_dene([], "Yarın gelmek istiyorum") is None


def test_bos_cevap_hermese_gider(monkeypatch):
    _sahte_api(monkeypatch, "   ")
    assert hafif.cevap_dene([], "İmplant ne kadar sürer?") is None


# ── Sessiz geri düşüş ────────────────────────────────────────────

def test_api_hatasi_sessizce_hermese_duser(monkeypatch):
    monkeypatch.setattr(hafif.httpx, "post", _patlat)
    assert hafif.cevap_dene([], "Adresiniz nerede?") is None


def test_anahtar_yoksa_devre_disi(monkeypatch):
    monkeypatch.setenv("AJAN_PROVIDER", "zai")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    assert hafif.cevap_dene([], "Adresiniz nerede?") is None


def test_kapatma_anahtari(monkeypatch):
    _sahte_api(monkeypatch, "cevap")
    monkeypatch.setenv("HAFIF_YOL", "0")   # _sahte_api bunu siliyor, sonra kur
    assert hafif.cevap_dene([], "Adresiniz nerede?") is None


def test_instagram_bu_yoldan_gecmez(monkeypatch):
    """Instagram'ın kanal sınırı ajan.py'deki prompt'ta yazılı. Aynı sınırı
    iki yerde tutmamak için bu kanal hafif yola hiç girmiyor."""
    _sahte_api(monkeypatch, "cevap")
    assert hafif.cevap_dene([], "Fiyatlarınız nedir?", kanal="instagram") is None


# ── Prompt içeriği ───────────────────────────────────────────────

def test_prompt_kimlik_ve_bilgi_tabani_icerir(monkeypatch):
    """Hafif yol Hermes'i atlıyor ama SOUL'daki sınırları atlamamalı —
    teşhis yasağı ve konu kilidi burada da geçerli."""
    yakalanan = {}
    _sahte_api(monkeypatch, "cevap", yakala=yakalanan)
    hafif.cevap_dene([], "Adresiniz nerede?")

    sistem = yakalanan["json"]["messages"][0]["content"]
    assert "Teşhis koymazsın" in sistem       # SOUL.md
    assert "Klinik bilgileri" in sistem       # .hermes.md eklendi
    assert hafif.DEVRET in sistem             # devretme talimatı


def test_gecmis_rol_sirasi(monkeypatch):
    yakalanan = {}
    _sahte_api(monkeypatch, "cevap", yakala=yakalanan)
    hafif.cevap_dene(
        [{"yon": "gelen", "mesaj": "Merhaba"}, {"yon": "giden", "mesaj": "Buyurun"}],
        "Adresiniz nerede?",
    )

    roller = [m["role"] for m in yakalanan["json"]["messages"]]
    assert roller == ["system", "user", "assistant", "user"]


def test_fiyat_tanimsizsa_maliyet_uydurulmaz(monkeypatch):
    monkeypatch.delenv("AJAN_1M_GIRIS_USD", raising=False)
    monkeypatch.delenv("AJAN_1M_CIKIS_USD", raising=False)
    _sahte_api(monkeypatch, "cevap")
    assert hafif.cevap_dene([], "Adresiniz nerede?")[1] is None


def test_fiyat_tanimliysa_maliyet_hesaplanir(monkeypatch):
    monkeypatch.setenv("AJAN_1M_GIRIS_USD", "1.0")
    monkeypatch.setenv("AJAN_1M_CIKIS_USD", "2.0")
    _sahte_api(monkeypatch, "cevap", kullanim={"prompt_tokens": 1_000_000,
                                               "completion_tokens": 500_000})
    assert hafif.cevap_dene([], "Adresiniz nerede?")[1] == 2.0


# ── Yardımcılar ──────────────────────────────────────────────────

def _patlat(*a, **kw):
    raise hafif.httpx.ConnectError("bağlantı yok")


def _sahte_api(monkeypatch, cevap: str, yakala: dict | None = None,
               kullanim: dict | None = None):
    monkeypatch.setenv("AJAN_PROVIDER", "zai")
    monkeypatch.setenv("ZAI_API_KEY", "sahte")
    monkeypatch.setenv("AJAN_MODEL", "sahte-model")
    monkeypatch.delenv("HAFIF_YOL", raising=False)

    class SahteYanit:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": cevap}}],
                    "usage": kullanim or {}}

    def sahte_post(url, **kw):
        if yakala is not None:
            yakala.update(url=url, **kw)
        return SahteYanit()

    monkeypatch.setattr(hafif.httpx, "post", sahte_post)


# ── istek_govdesi: opsiyonel effort (reasoning modelleri) ─────────
def test_istek_govdesi_effort_yoksa_payloada_girmez(monkeypatch):
    monkeypatch.delenv("AJAN_EFFORT", raising=False)
    g = hafif.istek_govdesi("m", [{"role": "user", "content": "x"}])
    assert "reasoning_effort" not in g
    assert g["temperature"] == 0.3


def test_istek_govdesi_effort_doluysa_eklenir(monkeypatch):
    """OpenAI reasoning modeli: ad `reasoning_effort`, temperature yasak.

    İkisi de yanlışken canlıda her çağrı 400 döndü, ajan hiç cevap veremedi.
    """
    monkeypatch.setenv("AJAN_EFFORT", "high")
    g = hafif.istek_govdesi("m", [{"role": "user", "content": "x"}], tools=None)
    assert g["reasoning_effort"] == "high"
    assert "effort" not in g
    assert "temperature" not in g
    assert g["tools"] is None


def test_istek_govdesi_araclarla_reasoning_kapanir(monkeypatch):
    """gpt-5.6-luna araç + reasoning'i kabul etmiyor, açıkça 'none' şart.

    Parametreyi atlamak da 400 döndürüyor — canlıda ölçüldü.
    """
    monkeypatch.setenv("AJAN_EFFORT", "high")
    g = hafif.istek_govdesi("m", [{"role": "user", "content": "x"}],
                            tools=[{"type": "function"}])
    assert g["reasoning_effort"] == "none"
