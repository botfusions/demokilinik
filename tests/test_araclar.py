"""Ajanın araç tablosu — sınırları burada denetleniyor.

Kritik olan iki şey: araç listesinin genişlememesi (kabuk yetkisi geri
gelmesin) ve hataların ajana hata olarak görünmesi (409'u başarı sanıp
"randevunuz hazır" derse iki hasta aynı saate düşer).
"""

import inspect
import json

import pytest

from app import araclar


def test_arac_listesi_dar_kalir():
    """Ajanın yapabilecekleri bu 7 araçla sınırlı.

    Liste büyürse bilinçli olmalı: her yeni araç hem token maliyeti hem de
    ajanın prompt'una sızacak bir talimatın kullanabileceği yeni bir yetki.
    """
    assert set(araclar.ARACLAR) == {
        "doktorlari_getir", "gun_uygunlugu", "en_erken_musait",
        "randevu_olustur", "hasta_randevulari", "randevu_onayla", "randevu_iptal",
    }


def test_hicbir_arac_kabuk_ya_da_dosya_acmaz():
    """Araçlar yalnız /api/* uçlarına gider — kabuk, dosya, rastgele URL yok."""
    for _, (metot, yol, *_) in araclar.ARACLAR.items():
        assert metot in ("GET", "POST")
        assert yol.startswith("/api/")


def test_araclar_modulunde_kabuk_yetkisi_yok():
    """Hermes çıkınca protokol katmanı kalktı; kalan tek koruma bu — modül
    kaynağında subprocess/dosya-açma/rastgele kod çalıştırma izi olmamalı."""
    kaynak = inspect.getsource(araclar)
    for yasakli in ("subprocess", "os.system", "eval(", "exec(", "open("):
        assert yasakli not in kaynak, f"{yasakli} araclar.py'ye sızmış"


def test_sema_kucuk_kalir():
    """Şema her istekte input maliyetine giriyor. `terminal` toolset'i 4.819 B'ydi;
    onun yerine geçen bu araçlar toplamda daha ucuz olmalı, yoksa göç anlamsız."""
    boyut = len(json.dumps(araclar.arac_listesi_openai(), ensure_ascii=False))
    assert boyut < 4819, f"araç şemaları {boyut} B — terminal'den pahalı"


def test_yol_parametresi_argumandan_doldurulur(monkeypatch):
    cagrildi = {}

    class SahteYanit:
        status_code = 200
        text = '{"durum":"iptal"}'

    def sahte_request(metot, url, **kw):
        cagrildi.update(metot=metot, url=url, json=kw.get("json"))
        return SahteYanit()

    monkeypatch.setattr(araclar.httpx, "request", sahte_request)
    metin, hata = araclar.arac_calistir("randevu_iptal", {"randevu_id": 42, "telefon": "905321112233"})

    assert not hata
    assert cagrildi["url"].endswith("/api/randevu/42/iptal")
    # randevu_id yola gitti, gövdede kalmadı; telefon gövdede — yetki kontrolü ona bakıyor
    assert cagrildi["json"] == {"telefon": "905321112233"}


def test_bos_doktor_id_gonderilmez(monkeypatch):
    """Hasta "farketmez" dediğinde doktor_id hiç gitmemeli.

    None olarak gönderilseydi API onu "hekim seçilmedi" değil "geçersiz hekim"
    diye görebilir, ya da dağıtım mantığı atlanabilirdi.
    """
    gonderilen = {}

    class SahteYanit:
        status_code = 200
        text = "{}"

    monkeypatch.setattr(araclar.httpx, "request",
                        lambda metot, url, **kw: (gonderilen.update(kw.get("json") or {}), SahteYanit())[1])
    araclar.arac_calistir("randevu_olustur", {
        "telefon": "905321112233", "hizmet": "İmplant",
        "baslangic": "2026-08-07T14:00:00", "bitis": "2026-08-07T14:30:00",
        "doktor_id": None,
    })

    assert "doktor_id" not in gonderilen


@pytest.mark.parametrize("kod,beklenen", [(409, "409"), (422, "422"), (403, "403")])
def test_api_hatasi_ajana_hata_olarak_doner(monkeypatch, kod, beklenen):
    """En pahalı hata: ajanın 409'u başarı sanması."""
    class SahteYanit:
        status_code = kod
        text = '{"detail":"O saat dolu"}'

        @staticmethod
        def json():
            return {"detail": "O saat dolu"}

    monkeypatch.setattr(araclar.httpx, "request", lambda *a, **kw: SahteYanit())
    metin, hata = araclar.arac_calistir("randevu_olustur", {"telefon": "9053", "hizmet": "x",
                                                            "baslangic": "a", "bitis": "b"})
    assert hata is True
    assert beklenen in metin


def test_klinik_ulasilamazsa_uydurmaz(monkeypatch):
    """Bağlantı koptuğunda ajan hata görmeli — sessiz boş cevap alıp
    randevuyu hafızasından uydurmamalı."""
    def patlat(*a, **kw):
        raise araclar.httpx.ConnectError("bağlantı yok")

    monkeypatch.setattr(araclar.httpx, "request", patlat)
    metin, hata = araclar.arac_calistir("doktorlari_getir", {})
    assert hata is True
    assert "ulaşılamadı" in metin


def test_bilinmeyen_arac_reddedilir():
    metin, hata = araclar.arac_calistir("dosya_sil", {})
    assert hata is True


def test_arac_listesi_openai_semasi():
    """tools=[...] parametresine doğrudan verilecek biçim."""
    liste = araclar.arac_listesi_openai()
    assert len(liste) == 7
    for arac in liste:
        assert arac["type"] == "function"
        assert {"name", "description", "parameters"} <= set(arac["function"])
