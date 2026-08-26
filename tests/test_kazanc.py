"""Kayıp ve kurtarma raporu (PRD İK-6) — T8, T9, T10 + rol denetimi.

Hiçbiri LLM çağırmaz; DB yoksa conftest üzerinden atlanır.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.crm import randevu_durum_yaz, randevu_iptal, randevu_olustur
from app.hizmet import hizmet_ekle
from app.kazanc import ay_ozeti
from app.kullanici import islem_yaz, kullanici_ekle


def _yakin_saat():
    """İş gününe düşen, çalışma saatleri içinde bir gelecek saat."""
    g = datetime.now() + timedelta(days=2)
    if g.isoweekday() == 7:
        g += timedelta(days=1)
    return g.replace(hour=10, minute=0, second=0, microsecond=0)


@pytest.fixture
def yonetici_istemci(conn):
    kullanici_ekle(conn, "admin", "test-parola", "admin")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "admin", "parola": "test-parola"})
    return c


@pytest.fixture
def personel_istemci(conn):
    kullanici_ekle(conn, "ayse", "test-parola", "personel")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "ayse", "parola": "test-parola"})
    return c


# ── T8: harf duyarsız fiyat eşleşmesi ───────────────────────

def test_fiyat_harf_duyarsiz_eslesir(conn, kisi_id):
    hizmet_ekle(conn, "İmplant", 1500)
    bas = _yakin_saat()
    rid = randevu_olustur(conn, kisi_id, "implant", bas, bas + timedelta(minutes=30))
    randevu_durum_yaz(conn, rid, "gelmedi")

    o = ay_ozeti(conn, bas.year, bas.month)
    assert o["sayilar"]["gelmedi"] == 1
    assert o["gelmedi_tl"] == Decimal("1500")
    assert o["gelmedi_fiyatsiz"] == 0


# ── T9: eşleşmeyen hizmet ayrı sayılır ──────────────────────

def test_eslesmeyen_hizmet_tl_toplamina_girmez(conn, kisi_id):
    hizmet_ekle(conn, "Kontrol", 500)
    bas = _yakin_saat()
    rid1 = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    rid2 = randevu_olustur(
        conn, kisi_id, "Bilinmeyen işlem", bas + timedelta(minutes=30), bas + timedelta(minutes=60)
    )
    randevu_durum_yaz(conn, rid1, "gelmedi")
    randevu_durum_yaz(conn, rid2, "gelmedi")

    o = ay_ozeti(conn, bas.year, bas.month)
    assert o["sayilar"]["gelmedi"] == 2
    assert o["gelmedi_fiyatsiz"] == 1
    assert o["gelmedi_tl"] == Decimal("500")


# ── T10: boş klinikte sayfa sıfır gösterir ──────────────────

def test_bos_klinik_rapor_hatasiz(conn, yonetici_istemci):
    r = yonetici_istemci.get("/rapor")
    assert r.status_code == 200

    simdi = datetime.now()
    o = ay_ozeti(conn, simdi.year, simdi.month)
    assert o["toplam"] == 0
    assert all(s == 0 for s in o["sayilar"].values())
    assert o["gelmedi_tl"] == 0
    assert o["gelmedi_fiyatsiz"] == 0
    assert o["erken_bosalan"] == 0
    assert o["ajan"] == 0
    assert o["ajan_mesai_disi"] == 0
    assert o["gelmeme_orani"] is None
    assert o["gelmeme_orani_metni"] == "—"


# ── rol denetimi ────────────────────────────────────────────

def test_personel_raporu_goremez(personel_istemci):
    assert personel_istemci.get("/rapor").status_code == 403


def test_yonetici_raporu_gorur(yonetici_istemci):
    assert yonetici_istemci.get("/rapor").status_code == 200


# ── erken boşalan: hatırlatma gönderilmiş + hasta iptali ────

def test_erken_bosalan_sadece_hasta_iptallerini_sayar(conn, kisi_id):
    """Hatırlatması gönderilip hastadan iptal edilen sayılır; personel
    iptali (işlem kaydı var) sayılmaz."""
    bas = _yakin_saat()
    hasta = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    personel_r = randevu_olustur(
        conn, kisi_id, "Dolgu", bas + timedelta(minutes=30), bas + timedelta(minutes=60)
    )
    for rid in (hasta, personel_r):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan, gonderildi) "
                "VALUES (%s, '24s', %s, now())",
                (rid, bas - timedelta(days=1)),
            )
    conn.commit()

    randevu_iptal(conn, hasta)                                   # ajan/kural yolu — kayıt yok
    randevu_iptal(conn, personel_r)
    islem_yaz(conn, None, "randevu iptal etti", f"#{personel_r}")  # panel yolu

    o = ay_ozeti(conn, bas.year, bas.month)
    assert o["erken_bosalan"] == 1
