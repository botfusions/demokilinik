"""Haftalık takvim: hafta aralığı, hekim rengi, iptal edilenlerin gizlenmesi."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.crm import doktor_ekle, randevu_iptal, randevu_olustur, randevular_araliginda


@pytest.fixture
def girisli(conn):
    from app.kullanici import kullanici_ekle

    kullanici_ekle(conn, "admin", "test-parola", "admin")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "admin", "parola": "test-parola"})
    return c


def _is_gunu(gun_sonrasi: int = 1) -> datetime:
    """Çalışma günü ve saati içinde bir zaman — randevu kuralları buna bakıyor."""
    g = datetime.now() + timedelta(days=gun_sonrasi)
    while g.isoweekday() == 7:
        g += timedelta(days=1)
    return g.replace(hour=10, minute=0, second=0, microsecond=0)


# ── hafta başı ──────────────────────────────────────────────

def test_hafta_basi_pazartesi():
    from app.main import hafta_basi

    assert hafta_basi(date(2026, 8, 6)).isoweekday() == 1     # Perşembe → Pzt
    assert hafta_basi(date(2026, 8, 3)) == date(2026, 8, 3)   # Pazartesi kendisi
    assert hafta_basi(date(2026, 8, 9)) == date(2026, 8, 3)   # Pazar → aynı hafta


# ── aralık sorgusu ──────────────────────────────────────────

def test_aralik_disi_randevu_gelmez(conn, kisi_id):
    bas = _is_gunu(1)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    conn.commit()

    gun = bas.date()
    icinde = randevular_araliginda(conn, gun, gun + timedelta(days=1))
    disinda = randevular_araliginda(conn, gun + timedelta(days=5), gun + timedelta(days=6))
    assert len(icinde) == 1 and disinda == []


def test_iptal_takvimde_gorunmez(conn, kisi_id):
    """İptal edilmiş randevu blok kaplarsa o saat dolu sanılır."""
    bas = _is_gunu(1)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    conn.commit()
    gun = bas.date()
    assert len(randevular_araliginda(conn, gun, gun + timedelta(days=1))) == 1

    randevu_iptal(conn, rid)
    assert randevular_araliginda(conn, gun, gun + timedelta(days=1)) == []


# ── hekim renkleri ──────────────────────────────────────────

def test_renk_kararli_ve_hekim_basina_ayri(conn, girisli):
    doktor_ekle(conn, "Dt. A")
    doktor_ekle(conn, "Dt. B")
    conn.commit()

    from app.main import DOKTOR_RENKLERI

    ilk = girisli.get("/takvim").text
    ikinci = girisli.get("/takvim").text
    assert DOKTOR_RENKLERI[0] in ilk and DOKTOR_RENKLERI[1] in ilk
    assert ilk == ikinci          # her açılışta aynı hekim aynı renkte


def test_sayfa_acilir_ve_randevuyu_gosterir(conn, girisli, kisi_id):
    bas = _is_gunu(1)
    randevu_olustur(conn, kisi_id, "Diş taşı temizliği", bas, bas + timedelta(minutes=30))
    conn.commit()

    r = girisli.get(f"/takvim?hafta={bas.date().isoformat()}")
    assert r.status_code == 200
    assert "Diş taşı temizliği" in r.text


def test_composio_yokken_bagli_degil_yazar(girisli, monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    assert "Google Takvim bağlı değil" in girisli.get("/takvim").text


# ── yetki ───────────────────────────────────────────────────

@pytest.fixture
def personel_istemci(conn):
    from app.kullanici import kullanici_ekle

    kullanici_ekle(conn, "ayse", "test-parola", "personel")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "ayse", "parola": "test-parola"})
    return c


def test_takvimi_personel_de_gorur(personel_istemci):
    assert personel_istemci.get("/takvim").status_code == 200


@pytest.mark.parametrize("yol,veri", [
    ("/hizmet", {"ad": "x", "fiyat": "100"}),
    ("/hizmet/fiyatlar", {"fiyat_1": "200"}),
    ("/kampanya", {"ad": "x", "indirim": "10"}),
    ("/kampanya/1/durum", {"aktif": "0"}),
])
def test_personel_fiyat_kampanya_degistiremez(personel_istemci, yol, veri):
    assert personel_istemci.post(yol, data=veri).status_code == 403


def test_personel_yonetici_sayfasini_goremez(personel_istemci):
    assert personel_istemci.get("/yonetici").status_code == 403
