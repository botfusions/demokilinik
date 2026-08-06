"""Klinik MCP sunucusu — ajanın elindeki araçların sınırları.

Bu testler ajanın CRM'e nasıl eriştiğini denetliyor. Kritik olan iki şey:
araç listesinin genişlememesi (kabuk yetkisi geri gelmesin) ve hataların
ajana hata olarak görünmesi (409'u başarı sanıp "randevunuz hazır" derse
iki hasta aynı saate düşer).
"""

import importlib.util
import json
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("klinik_mcp", KOK / "scripts" / "klinik-mcp.py")
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)


def test_arac_listesi_dar_kalir():
    """Ajanın yapabilecekleri bu 7 araçla sınırlı.

    Liste büyürse bilinçli olmalı: her yeni araç hem token maliyeti hem de
    ajanın prompt'una sızacak bir talimatın kullanabileceği yeni bir yetki.
    """
    assert set(mcp.ARACLAR) == {
        "doktorlari_getir", "gun_uygunlugu", "en_erken_musait",
        "randevu_olustur", "hasta_randevulari", "randevu_onayla", "randevu_iptal",
    }


def test_hicbir_arac_kabuk_ya_da_dosya_acmaz():
    """Araçlar yalnız /api/* uçlarına gider — kabuk, dosya, rastgele URL yok."""
    for _, (metot, yol, *_) in mcp.ARACLAR.items():
        assert metot in ("GET", "POST")
        assert yol.startswith("/api/")


def test_sema_kucuk_kalir():
    """Şema her istekte input maliyetine giriyor. `terminal` toolset'i 4.819 B'ydi;
    onun yerine geçen bu araçlar toplamda daha ucuz olmalı, yoksa göç anlamsız."""
    boyut = len(json.dumps(mcp.arac_listesi(), ensure_ascii=False))
    assert boyut < 4819, f"araç şemaları {boyut} B — terminal'den pahalı"


def test_yol_parametresi_argumandan_doldurulur(monkeypatch):
    cagrildi = {}

    class SahteYanit:
        status_code = 200
        text = '{"durum":"iptal"}'

    def sahte_request(metot, url, **kw):
        cagrildi.update(metot=metot, url=url, json=kw.get("json"))
        return SahteYanit()

    monkeypatch.setattr(mcp.httpx, "request", sahte_request)
    metin, hata = mcp.arac_calistir("randevu_iptal", {"randevu_id": 42, "telefon": "905321112233"})

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

    monkeypatch.setattr(mcp.httpx, "request",
                        lambda metot, url, **kw: (gonderilen.update(kw.get("json") or {}), SahteYanit())[1])
    mcp.arac_calistir("randevu_olustur", {
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

    monkeypatch.setattr(mcp.httpx, "request", lambda *a, **kw: SahteYanit())
    metin, hata = mcp.arac_calistir("randevu_olustur", {"telefon": "9053", "hizmet": "x",
                                                       "baslangic": "a", "bitis": "b"})
    assert hata is True
    assert beklenen in metin


def test_klinik_ulasilamazsa_uydurmaz(monkeypatch):
    """Bağlantı koptuğunda ajan hata görmeli — sessiz boş cevap alıp
    randevuyu hafızasından uydurmamalı."""
    def patlat(*a, **kw):
        raise mcp.httpx.ConnectError("bağlantı yok")

    monkeypatch.setattr(mcp.httpx, "request", patlat)
    metin, hata = mcp.arac_calistir("doktorlari_getir", {})
    assert hata is True
    assert "ulaşılamadı" in metin


def test_bilinmeyen_arac_reddedilir():
    metin, hata = mcp.arac_calistir("dosya_sil", {})
    assert hata is True


def test_protokol_akisi():
    """initialize → tools/list → tools/call. Bildirimlere cevap yazılmamalı;
    yazılırsa Hermes'in JSON-RPC ayrıştırıcısı bozulur."""
    ilk = mcp.istek_isle({"method": "initialize", "id": 1,
                          "params": {"protocolVersion": "2025-11-25"}})
    assert ilk["protocolVersion"] == "2025-11-25"   # istemcinin sürümü yansıtılır
    assert "tools" in ilk["capabilities"]

    assert mcp.istek_isle({"method": "notifications/initialized"}) is None

    araclar = mcp.istek_isle({"method": "tools/list", "id": 2})["tools"]
    assert len(araclar) == 7
    assert all({"name", "description", "inputSchema"} <= set(a) for a in araclar)


def test_skilllerde_curl_kalmadi():
    """Skill'ler artık kabuk komutu tarif etmemeli.

    Kalsaydı ajan `terminal` toolset'ini arar, bulamaz ve hastaya
    "sistem hatası" derdi — ya da birileri terminal'i geri açardı.
    """
    for skill in (KOK / "hermes-home" / "skills").rglob("SKILL.md"):
        metin = skill.read_text()
        assert "curl" not in metin, f"{skill.name} hâlâ curl tarif ediyor"
        assert "localhost:8000" not in metin, f"{skill.name} hâlâ ham URL içeriyor"


def test_terminal_toolseti_kapali():
    """Kabuk yetkisi geri açılırsa bu test düşer — bilinçli olmayan bir
    geri alma sessizce geçmesin."""
    config = (KOK / "hermes-home" / "config.yaml").read_text()
    kapalilar = config.split("disabled_toolsets:")[1].split("\n\n")[0]
    for toolset in ("terminal", "computer_use", "file", "delegation"):
        assert f"- {toolset}" in kapalilar, f"{toolset} artık kapalı değil"
