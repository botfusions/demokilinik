"""LLM'siz kural katmanı — bu katman tanımı gereği hiçbir LLM çağırmaz.

Testler mock LLM gerektirmez: `kb.bilgiler_listele` ve `araclar.arac_calistir`
monkeypatch edilir, gerçek DB/HTTP hiç devreye girmez.
"""

import json

from app import araclar, kb, kural


def _bilgi(baslik: str, icerik: str) -> dict:
    return {"baslik": baslik, "icerik": icerik, "kategori": "sss", "aktif": True}


def test_tam_esleyen_soru_llmsiz_cevaplanir(monkeypatch):
    monkeypatch.setattr(
        kb, "bilgiler_listele",
        lambda conn, yalniz_aktif=False: [_bilgi("Otopark var mı", "Evet, ücretsiz otoparkımız var.")],
    )
    assert kural.cevap_dene(None, "Otopark var mı") == "Evet, ücretsiz otoparkımız var."


def test_yazim_hatali_soru_esik_ustunde_eslesir(monkeypatch):
    monkeypatch.setattr(
        kb, "bilgiler_listele",
        lambda conn, yalniz_aktif=False: [_bilgi("Otopark var mı", "Evet, ücretsiz otoparkımız var.")],
    )
    assert kural.cevap_dene(None, "otopark varmı") == "Evet, ücretsiz otoparkımız var."


def test_alakasiz_soru_esik_altinda_none(monkeypatch):
    monkeypatch.setattr(
        kb, "bilgiler_listele",
        lambda conn, yalniz_aktif=False: [_bilgi("Otopark var mı", "Evet, ücretsiz otoparkımız var.")],
    )
    assert kural.cevap_dene(None, "implant ne kadar sürer") is None


def test_bos_bilgi_tabani_none(monkeypatch):
    monkeypatch.setattr(kb, "bilgiler_listele", lambda conn, yalniz_aktif=False: [])
    assert kural.cevap_dene(None, "otopark var mı") is None


def test_randevu_sinyali_tasiyan_mesaj_kural_katmanina_girmez(monkeypatch):
    """"İptal" sinyal listesinde — eşleşme skoru ne olursa olsun LLM'e düşmeli."""
    monkeypatch.setattr(
        kb, "bilgiler_listele",
        lambda conn, yalniz_aktif=False: [_bilgi("randevumu iptal etmek istiyorum", "cevap")],
    )
    assert kural.cevap_dene(None, "randevumu iptal etmek istiyorum") is None


def test_uzun_mesaj_atlanir(monkeypatch):
    monkeypatch.setattr(
        kb, "bilgiler_listele",
        lambda conn, yalniz_aktif=False: [_bilgi("Otopark var mı", "cevap")],
    )
    uzun = "Merhaba, otoparkınız var mı acaba, bir de çalışma saatleriniz ve adresiniz nedir " * 2
    assert kural.cevap_dene(None, uzun) is None


# ── hatirlatma_cevabi_dene ──────────────────────────────────


def _sahte_arac_calistir(cagrilar, randevu_sayisi=1):
    def _(ad, argumanlar):
        cagrilar.append((ad, argumanlar))
        if ad == "hasta_randevulari":
            randevular = [{"randevu_id": i + 1} for i in range(randevu_sayisi)]
            return json.dumps({"randevular": randevular}), False
        return "{}", False
    return _


def test_tek_bekleyen_randevu_evet_onaylanir(monkeypatch):
    cagrilar = []
    monkeypatch.setattr(araclar, "arac_calistir", _sahte_arac_calistir(cagrilar, 1))
    yanit = kural.hatirlatma_cevabi_dene("905321112233", "evet")
    assert yanit == "Teşekkürler, sizi bekliyoruz."
    assert ("randevu_onayla", {"randevu_id": 1, "telefon": "905321112233"}) in cagrilar


def test_tek_bekleyen_randevu_iptal_cagirir(monkeypatch):
    cagrilar = []
    monkeypatch.setattr(araclar, "arac_calistir", _sahte_arac_calistir(cagrilar, 1))
    yanit = kural.hatirlatma_cevabi_dene("905321112233", "iptal")
    assert yanit == "Randevunuzu iptal ettim. Yeniden almak isterseniz yazmanız yeterli."
    assert ("randevu_iptal", {"randevu_id": 1, "telefon": "905321112233"}) in cagrilar


def test_sifir_bekleyen_randevu_belirsiz_none(monkeypatch):
    cagrilar = []
    monkeypatch.setattr(araclar, "arac_calistir", _sahte_arac_calistir(cagrilar, 0))
    assert kural.hatirlatma_cevabi_dene("905321112233", "evet") is None


def test_iki_bekleyen_randevu_belirsiz_none(monkeypatch):
    cagrilar = []
    monkeypatch.setattr(araclar, "arac_calistir", _sahte_arac_calistir(cagrilar, 2))
    assert kural.hatirlatma_cevabi_dene("905321112233", "evet") is None


def test_belirsiz_kelime_none(monkeypatch):
    cagrilar = []
    monkeypatch.setattr(araclar, "arac_calistir", _sahte_arac_calistir(cagrilar, 1))
    assert kural.hatirlatma_cevabi_dene("905321112233", "bakacağım") is None
    assert cagrilar == []   # araç hiç çağrılmadı


def test_api_hatasinda_none(monkeypatch):
    monkeypatch.setattr(araclar, "arac_calistir", lambda ad, arg: ("HATA 500", True))
    assert kural.hatirlatma_cevabi_dene("905321112233", "evet") is None
