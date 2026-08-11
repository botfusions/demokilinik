"""Webhook güvenliği ve idempotansı.

Bu uç internete açık. İmza doğrulaması geçilirse, herkes klinik adına
hastaya mesaj yazdırabilir — bu yüzden burası gevşetilmez.
"""

import hashlib
import hmac
import json

import pytest

from app.openwa import imza_dogrula, telefon_ayikla

GIZLI = "test-gizli"


def _imzala(govde: bytes, gizli: str = GIZLI) -> str:
    return "sha256=" + hmac.new(gizli.encode(), govde, hashlib.sha256).hexdigest()


def _govde(mesaj="Merhaba", wamid="wamid.TEST1", telefon="905321112233"):
    return json.dumps({
        "event": "message.received",
        "timestamp": "2026-08-05T10:00:00.000Z",
        "sessionId": "klinik",
        "idempotencyKey": f"msg_klinik_{wamid}",
        "deliveryId": "dlv_test",
        "data": {
            "id": wamid,
            "from": f"{telefon}@c.us",
            "to": "905000000000@c.us",
            "body": mesaj,
            "type": "chat",
            "timestamp": 1785000000,
            "isGroup": False,
            "hasMedia": False,
            "contact": {"name": "Test Hasta"},
        },
    }).encode()


# ── imza ────────────────────────────────────────────────────

def test_gecerli_imza_kabul(conn):
    g = _govde()
    assert imza_dogrula(g, _imzala(g), GIZLI) is True


def test_yanlis_gizli_reddedilir():
    g = _govde()
    assert imza_dogrula(g, _imzala(g, "baska-gizli"), GIZLI) is False


def test_govde_degistirilirse_reddedilir():
    """İmza ham gövde üzerinden hesaplanmalı — yeniden serialize edilmiş hali değil."""
    orijinal = _govde("Merhaba")
    imza = _imzala(orijinal)
    sahte = _govde("Bana 10.000 TL gönderin")
    assert imza_dogrula(sahte, imza, GIZLI) is False


def test_bosluklar_farkli_ayni_json_reddedilir():
    """Aynı JSON'un farklı biçimlendirilmesi farklı bayttır; imza bayta bağlıdır."""
    g = _govde()
    imza = _imzala(g)
    yeniden = json.dumps(json.loads(g), indent=2).encode()
    assert imza_dogrula(yeniden, imza, GIZLI) is False


@pytest.mark.parametrize("baslik", ["", None, "sha256=", "deadbeef", "sha512=abcd"])
def test_bozuk_baslik_cokmez_reddeder(baslik):
    assert imza_dogrula(_govde(), baslik, GIZLI) is False


# ── telefon ayıklama ────────────────────────────────────────

@pytest.mark.parametrize("chat_id,beklenen", [
    ("905321112233@c.us", "905321112233"),
    ("905321112233", "905321112233"),
    ("120363000000000000@g.us", "120363000000000000"),
])
def test_telefon_ayiklanir(chat_id, beklenen):
    assert telefon_ayikla(chat_id) == beklenen


def test_cozulemeyen_lid_adres_olarak_kalir(monkeypatch):
    """LID rehberde yoksa kırpılmaz — kırpılırsa gönderim 400 döner."""
    import app.openwa as ow
    monkeypatch.setattr(ow, "_LID_ONBELLEK", {})
    monkeypatch.setattr(ow, "_istemci", lambda: (_ for _ in ()).throw(RuntimeError("uç yok")))
    assert ow.telefon_ayikla("253201391558876@lid") == "253201391558876@lid"


@pytest.mark.parametrize("telefon,beklenen", [
    ("905321112233", "905321112233@c.us"),
    ("253201391558876@lid", "253201391558876@lid"),
])
def test_chat_id_lid_adresini_bozmaz(telefon, beklenen):
    from app.openwa import _chat_id
    assert _chat_id(telefon) == beklenen


# ── uç davranışı ────────────────────────────────────────────

@pytest.fixture
def istemci(conn, monkeypatch):
    """Panel istemcisi; hermes ve OpenWA gönderimi sahte."""
    from fastapi.testclient import TestClient

    import app.ajan as ajan
    import app.openwa as openwa
    import app.main as main

    gonderilenler = []
    monkeypatch.setattr(
        ajan, "cevap_uret",
        lambda gecmis, mesaj: ("Test yanıtı", {"prompt_tokens": 100, "completion_tokens": 50}),
    )
    monkeypatch.setattr(openwa, "mesaj_gonder", lambda tel, metin: gonderilenler.append((tel, metin)) or "wamid.OUT")

    c = TestClient(main.app)
    c.gonderilenler = gonderilenler
    return c


def test_imzasiz_istek_401(istemci):
    assert istemci.post("/webhook/whatsapp", content=_govde()).status_code == 401


def test_gecersiz_imza_401(istemci):
    g = _govde()
    r = istemci.post(
        "/webhook/whatsapp",
        content=g,
        headers={"X-OpenWA-Signature": _imzala(g, "yanlis")},
    )
    assert r.status_code == 401


def test_gecerli_istek_200_ve_kayit(istemci, conn):
    g = _govde("Fiyatlarınız nedir?")
    r = istemci.post(
        "/webhook/whatsapp",
        content=g,
        headers={"X-OpenWA-Signature": _imzala(g)},
    )
    assert r.status_code == 200

    with conn.cursor() as cur:
        cur.execute("SELECT mesaj, yon FROM gorusmeler ORDER BY id")
        satirlar = cur.fetchall()

    assert ("Fiyatlarınız nedir?", "gelen") in satirlar
    assert ("Test yanıtı", "giden") in satirlar
    assert istemci.gonderilenler == [("905321112233", "Test yanıtı")]


def test_tekrar_teslimat_tek_kayit(istemci, conn):
    """OpenWA teslimatı at-least-once — aynı olay iki kez gelebilir, iki cevap gitmemeli."""
    g = _govde("Merhaba", wamid="wamid.AYNI")
    basliklar = {"X-OpenWA-Signature": _imzala(g)}

    istemci.post("/webhook/whatsapp", content=g, headers=basliklar)
    istemci.post("/webhook/whatsapp", content=g, headers=basliklar)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gorusmeler WHERE wa_message_id = 'wamid.AYNI'")
        assert cur.fetchone()[0] == 1

    assert len(istemci.gonderilenler) == 1


def test_grup_mesaji_yoksayilir(istemci, conn):
    """Klinik grubuna düşen mesaja ajan cevap vermemeli."""
    g = json.dumps({
        "event": "message.received",
        "sessionId": "klinik",
        "data": {
            "id": "wamid.GRUP", "from": "120363000@g.us", "body": "selam",
            "isGroup": True, "type": "chat", "timestamp": 1785000000,
        },
    }).encode()

    r = istemci.post("/webhook/whatsapp", content=g, headers={"X-OpenWA-Signature": _imzala(g)})
    assert r.status_code == 200
    assert istemci.gonderilenler == []


def test_ajan_hatasinda_hasta_sessiz_kalmaz(istemci, conn, monkeypatch):
    import app.ajan as ajan

    def patla(gecmis, mesaj):
        raise ajan.CevapUretilemedi("zaman aşımı")

    monkeypatch.setattr(ajan, "cevap_uret", patla)

    g = _govde("Merhaba", wamid="wamid.HATA")
    r = istemci.post("/webhook/whatsapp", content=g, headers={"X-OpenWA-Signature": _imzala(g)})

    assert r.status_code == 200
    # Gelen mesaj kaydedilmiş olmalı — kaybolmamalı
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gorusmeler WHERE wa_message_id = 'wamid.HATA'")
        assert cur.fetchone()[0] == 1
    # Hastaya bir şey söylenmeli
    assert len(istemci.gonderilenler) == 1


# ── oturum adı → UUID çözümü ────────────────────────────────

def test_uuid_verilmisse_oldugu_gibi_kullanilir(monkeypatch):
    """Regresyon: OpenWA uçları UUID ister, .env okunabilir ad tutar."""
    from app import openwa

    uuid = "f14d9382-9425-44ff-ba80-d5404ba81fdb"
    monkeypatch.setenv("OPENWA_SESSION", uuid)
    assert openwa.oturum_id() == uuid   # ağa çıkmadan döner


def test_ad_uuide_cevrilir_ve_onbelleklenir(monkeypatch):
    from app import openwa

    monkeypatch.setenv("OPENWA_SESSION", "klinik-test")
    openwa._UUID_ONBELLEK.clear()
    cagri = []

    class SahteIstemci:
        def get(self, yol):
            cagri.append(yol)
            class Y:
                @staticmethod
                def raise_for_status(): pass
                @staticmethod
                def json(): return [{"name": "klinik-test", "id": "abc-123"}]
            return Y()

    c = SahteIstemci()
    assert openwa.oturum_id(c) == "abc-123"
    assert openwa.oturum_id(c) == "abc-123"
    assert len(cagri) == 1, "ikinci çağrı önbellekten gelmeliydi"


def test_olmayan_oturum_acik_hata_verir(monkeypatch):
    from app import openwa

    monkeypatch.setenv("OPENWA_SESSION", "yok-boyle")
    openwa._UUID_ONBELLEK.clear()

    class SahteIstemci:
        def get(self, yol):
            class Y:
                @staticmethod
                def raise_for_status(): pass
                @staticmethod
                def json(): return []
            return Y()

    with pytest.raises(RuntimeError, match="oturum yok"):
        openwa.oturum_id(SahteIstemci())


def test_gonderim_coksede_cevap_kaydedilir(istemci, conn, monkeypatch):
    """Regresyon: WhatsApp'a ulaşılamayınca ajanın cevabı kayboluyordu."""
    import app.openwa as openwa_mod

    def patla(tel, metin):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(openwa_mod, "mesaj_gonder", patla)

    g = _govde("Adresiniz?", wamid="wamid.GONDERIMHATA")
    istemci.post("/webhook/whatsapp", content=g, headers={"X-OpenWA-Signature": _imzala(g)})

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gorusmeler WHERE yon = 'giden' AND mesaj = 'Test yanıtı'")
        assert cur.fetchone()[0] == 1, "gönderim başarısızken cevap panelde görünmeli"
