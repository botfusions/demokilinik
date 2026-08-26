"""Unipile taşıması (Instagram DM, HANDOFF madde 8).

Hiçbir test Unipile'ın gerçek ucuna çıkmaz: ajan ve gönderim enjekte edilir.
Sınanan şeyler: olay ayıklama, webhook kimliği, tekrar teslimat kilidi ve
kapsam kilidinin (bu kanaldan randevu çıkmaz) taşımayla gevşememesi.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.unipile as unipile
from app.crm import gorusme_gecmisi, kisi_bul

HESAP = "hesap-1"
CHAT = "chat-1"
GONDEREN = "17841400000000001"
KENDI = "37551172832"


@pytest.fixture(autouse=True)
def unipile_env(monkeypatch):
    monkeypatch.setenv("UNIPILE_URL", "https://api60.test:1")
    monkeypatch.setenv("UNIPILE_ANAHTAR", "test-anahtar")
    monkeypatch.setenv("UNIPILE_HESAP", HESAP)
    monkeypatch.setenv("UNIPILE_WEBHOOK_ANAHTAR", "test-gizli")
    monkeypatch.setenv("INSTAGRAM_HESAP_ID", KENDI)


def _olay(metin="fiyat ne kadar", mid="m1", gonderen=GONDEREN, name="message_received"):
    return {"name": name, "account_id": HESAP, "chat_id": CHAT,
            "message": {"id": mid, "sender_id": gonderen, "text": metin,
                        "chat_id": CHAT, "is_sender": 0}}


# ── yapılandırma kapısı ──────────────────────────────────────

def test_yapilandirildi_mi_uc_env_ister(monkeypatch):
    monkeypatch.delenv("UNIPILE_URL", raising=False)
    assert unipile.yapilandirildi_mi() is False
    monkeypatch.setenv("UNIPILE_URL", "https://x")
    assert unipile.yapilandirildi_mi() is True


# ── hesap durumu (kalp atışının kaynağı) ─────────────────────

def test_hesap_durumu_sources_alanindan_okunur(monkeypatch):
    monkeypatch.setattr(unipile, "_istek",
                        lambda y, yol: {"sources": [{"status": "OK"}]})
    assert unipile.hesap_durumu() == (True, None)


def test_hesap_durumu_dusukse_bozuk(monkeypatch):
    monkeypatch.setattr(unipile, "_istek",
                        lambda y, yol: {"sources": [{"status": "CREDENTIALS"}]})
    basarili, hata = unipile.hesap_durumu()
    assert basarili is False and "CREDENTIALS" in hata


def test_hesap_durumu_sources_bossa_bozuk(monkeypatch):
    monkeypatch.setattr(unipile, "_istek", lambda y, yol: {})
    basarili, hata = unipile.hesap_durumu()
    assert basarili is False and "okunamadı" in hata


# ── olay ayıklama ────────────────────────────────────────────

def test_mesaj_olayi_ayiklanir():
    m = unipile.olay_ayikla(_olay())
    assert m == {"igsid": GONDEREN, "chat_id": CHAT, "mesaj_id": "ig:m1",
                 "metin": "fiyat ne kadar", "ad": None}


def test_gonderen_attendee_sozlugu_gelse_de_igsid_duser():
    # sender_id attendee objesi olarak geldiğinde ig:{'attendee_id':...}
    # dökümü yerine provider id'si düşmeli (canlıda kisiler.telefon'u kirletti)
    att = {"attendee_id": "k1Bv", "attendee_provider_id": "18086807756252833",
           "attendee_name": "Reseptaai"}
    m = unipile.olay_ayikla(_olay(gonderen=att))
    assert m["igsid"] == "18086807756252833"


def test_mesaj_json_dizesi_olarak_gelirse_cozulur():
    import json
    o = _olay()
    o["message"] = json.dumps(o["message"])
    # zarf tam obje değilse message_id ile geri çekilir
    o["message_id"] = "m1"
    getirilen = {"id": "m1", "sender_id": GONDEREN, "text": "fiyat ne kadar",
                 "chat_id": CHAT, "is_sender": 0}
    m = unipile.olay_ayikla(o, getir=lambda mid: getirilen)
    assert m is not None and m["mesaj_id"] == "ig:m1"


def test_zarfta_duz_metin_varsa_id_yoksa_yoksayilir():
    o = _olay()
    o["message"] = "düz metin"          # message_id yok — çekme yolu da yok
    assert unipile.olay_ayikla(o, getir=lambda mid: {}) is None


def test_kendi_mesajimiz_is_sender_ile_yoksayilir():
    o = _olay()
    o["message"]["is_sender"] = 1       # tam obje geldi ama bizim gönderdiğimiz
    assert unipile.olay_ayikla(o, getir=lambda mid: {}) is None


def test_zarf_eksikse_mesaj_id_ile_geriden_cekilir():
    o = _olay()
    o["message"] = "Merhaba dolgu hakkında bilgi istiyorum"
    o["message_id"] = "m9"
    getirilen = {"id": "m9", "sender_id": GONDEREN,
                 "text": "Merhaba dolgu hakkında bilgi istiyorum",
                 "chat_id": CHAT, "is_sender": 0}
    m = unipile.olay_ayikla(o, getir=lambda mid: getirilen)
    assert m is not None and m["mesaj_id"] == "ig:m9"
    assert m["metin"] == "Merhaba dolgu hakkında bilgi istiyorum"


def test_baska_olay_yoksayilir():
    assert unipile.olay_ayikla(_olay(name="message_read")) is None


def test_baska_hesap_yoksayilir(monkeypatch):
    monkeypatch.setenv("UNIPILE_HESAP", "baska-hesap")
    assert unipile.olay_ayikla(_olay()) is None


def test_kendi_mesajimiz_yoksayilir():
    assert unipile.olay_ayikla(_olay(gonderen=KENDI)) is None


def test_bos_metin_yoksayilir():
    assert unipile.olay_ayikla(_olay(metin="  ")) is None


# ── webhook ucu: kimlik, kayıt, tekrar teslimat, kapsam ──────

@pytest.fixture
def istemci(conn, monkeypatch):
    gonderilenler = []
    monkeypatch.setattr(
        "app.ajan.cevap_uret",
        lambda gecmis, mesaj, **kw: ("Test yanıtı", {"prompt_tokens": 10, "completion_tokens": 5}),
    )
    monkeypatch.setattr(
        unipile, "mesaj_gonder",
        lambda chat_id, metin: gonderilenler.append((chat_id, metin)) or "igmid.OUT",
    )
    monkeypatch.setattr("app.instagram.okundu_isaretle", lambda _: None)
    c = TestClient(main.app)
    c.gonderilenler = gonderilenler
    return c


def _gonder(c, olay, anahtar="test-gizli"):
    return c.post("/webhook/unipile", json=olay,
                  headers={"X-Unipile-Anahtar": anahtar})


def test_gecerli_olay_mesaj_kaydeder_ve_cevaplar(istemci, conn):
    y = _gonder(istemci, _olay())
    assert y.status_code == 200 and y.json()["durum"] == "alindi"

    # Cevap CHAT'e gider (unipile adreslemesi), kişi ig:<gonderen> olarak açılır
    assert istemci.gonderilenler == [(CHAT, "Test yanıtı")]

    kisi = kisi_bul(conn, f"ig:{GONDEREN}")
    assert kisi is not None
    gecmis = gorusme_gecmisi(conn, kisi["id"])
    assert [g["yon"] for g in gecmis] == ["gelen", "giden"]
    assert all(g["kanal"] == "instagram" for g in gecmis)


def test_yanlis_anahtar_401(istemci):
    y = _gonder(istemci, _olay(), anahtar="baska")
    assert y.status_code == 401


def test_yoksayilan_olay_200_donuyor(istemci):
    y = _gonder(istemci, _olay(name="message_read"))
    assert y.json()["durum"] == "yoksayildi"
    assert istemci.gonderilenler == []


def test_ayni_mesaj_ikinci_kez_cevaplanmaz(istemci):
    _gonder(istemci, _olay(mid="m1"))
    _gonder(istemci, _olay(mid="m1"))
    assert len(istemci.gonderilenler) == 1


def test_bu_kanaldan_randevu_olusmaz(istemci, conn):
    _gonder(istemci, _olay(metin="yarına randevu istiyorum", mid="m2"))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM randevular")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM hatirlatmalar")
        assert cur.fetchone()[0] == 0
