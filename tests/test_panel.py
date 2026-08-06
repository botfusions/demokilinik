"""Panel: kimlik doğrulama, bilgi girişi → .hermes.md, iç API koruması."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def istemci(conn):
    return TestClient(main.app, follow_redirects=False)


@pytest.fixture
def girisli(conn):
    """Yönetici oturumu — panel sayfalarının tamamına erişir."""
    from app.kullanici import kullanici_ekle

    kullanici_ekle(conn, "admin", "test-parola", "admin")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "admin", "parola": "test-parola"})
    return c


# ── kimlik doğrulama ────────────────────────────────────────

@pytest.mark.parametrize("yol", ["/", "/bilgi", "/randevular", "/hastalar"])
def test_parolasiz_erisim_girise_yonlendirir(istemci, yol):
    r = istemci.get(yol)
    assert r.status_code in (302, 303, 307)
    assert "/giris" in r.headers["location"]


def test_yanlis_parola_iceri_almaz(istemci, conn):
    from app.kullanici import kullanici_ekle

    kullanici_ekle(conn, "admin", "test-parola", "admin")
    istemci.post("/giris", data={"kullanici_adi": "admin", "parola": "yanlis"})
    assert istemci.get("/").status_code in (302, 303, 307)


def test_dogru_parola_iceri_alir(girisli):
    assert girisli.get("/").status_code == 200


def test_cikis_oturumu_kapatir(girisli):
    girisli.post("/cikis")
    assert girisli.get("/").status_code in (302, 303, 307)


# ── bilgi tabanı ────────────────────────────────────────────

def test_bilgi_ekleme_hermes_md_gunceller(girisli, conn, tmp_path, monkeypatch):
    yol = tmp_path / ".hermes.md"
    monkeypatch.setattr(main, "HERMES_MD", yol)

    girisli.post("/bilgi", data={
        "baslik": "İmplant",
        "icerik": "Tek diş implant 25.000 TL",
        "kategori": "fiyatlar",
    })

    assert yol.exists()
    assert "25.000 TL" in yol.read_text(encoding="utf-8")


def test_pasiflestirme_hermes_mdden_cikarir(girisli, conn, tmp_path, monkeypatch):
    yol = tmp_path / ".hermes.md"
    monkeypatch.setattr(main, "HERMES_MD", yol)

    from app.kb import bilgi_ekle
    bid = bilgi_ekle(conn, "Eski kampanya", "Yarı fiyat", "fiyatlar")
    conn.commit()

    girisli.post(f"/bilgi/{bid}/pasiflestir")
    assert "Yarı fiyat" not in yol.read_text(encoding="utf-8")


def test_bilgi_duzenleme_hermes_mdyi_gunceller(girisli, conn, tmp_path, monkeypatch):
    yol = tmp_path / ".hermes.md"
    monkeypatch.setattr(main, "HERMES_MD", yol)

    from app.kb import bilgi_ekle
    bid = bilgi_ekle(conn, "İmplant", "Tek diş implant 25.000 TL", "fiyatlar")
    conn.commit()

    girisli.post(f"/bilgi/{bid}/guncelle", data={
        "baslik": "İmplant",
        "icerik": "Tek diş implant 30.000 TL",
        "kategori": "fiyatlar",
    })

    metin = yol.read_text(encoding="utf-8")
    assert "30.000 TL" in metin
    assert "25.000 TL" not in metin      # eski fiyat ajana kalmamalı


def test_duzenleme_islem_kaydina_duser(girisli, conn):
    from app.kb import bilgi_ekle
    from app.kullanici import islem_kayitlari

    bid = bilgi_ekle(conn, "Adres", "Bağdat Cad. No:1", "adres")
    conn.commit()
    girisli.post(f"/bilgi/{bid}/guncelle",
                 data={"baslik": "Adres", "icerik": "Bağdat Cad. No:2", "kategori": "adres"})

    assert any("bilgi düzenledi" in k["eylem"] for k in islem_kayitlari(conn))


def test_pasif_kayit_duzenlenince_hermes_mdye_sizmaz(girisli, conn, tmp_path, monkeypatch):
    """Düzenleme pasifliği bozmamalı — kaldırılmış bir fiyat geri dönmemeli."""
    yol = tmp_path / ".hermes.md"
    monkeypatch.setattr(main, "HERMES_MD", yol)

    from app.kb import bilgi_ekle, bilgi_pasiflestir
    bid = bilgi_ekle(conn, "Kampanya", "Yarı fiyat", "fiyatlar")
    bilgi_pasiflestir(conn, bid)
    conn.commit()

    girisli.post(f"/bilgi/{bid}/guncelle",
                 data={"baslik": "Kampanya", "icerik": "Üçte bir fiyat", "kategori": "fiyatlar"})

    assert "Üçte bir fiyat" not in yol.read_text(encoding="utf-8")


# ── bilgi tabanı yalnız yöneticide ──────────────────────────

@pytest.fixture
def personel_istemci(conn):
    """Personel oturumu — bilgi tabanına erişemez."""
    from app.kullanici import kullanici_ekle

    kullanici_ekle(conn, "ayse", "test-parola", "personel")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "ayse", "parola": "test-parola"})
    return c


def test_personel_bilgi_sayfasini_goremez(personel_istemci):
    assert personel_istemci.get("/bilgi").status_code == 403


@pytest.mark.parametrize("yol,veri", [
    ("/bilgi", {"baslik": "x", "icerik": "y", "kategori": "genel"}),
    ("/bilgi/1/guncelle", {"baslik": "x", "icerik": "y", "kategori": "genel"}),
    ("/bilgi/1/pasiflestir", {}),
    ("/bilgi/1/aktiflestir", {}),
])
def test_personel_bilgi_degistiremez(personel_istemci, yol, veri):
    """Ajanın ağzından çıkacak bilgiyi değiştirmek yönetici işi."""
    assert personel_istemci.post(yol, data=veri).status_code == 403


def test_personel_kenar_cubugunda_yonetici_girisi_yok(personel_istemci):
    """Personelde kimlik kartı tıklanabilir olmamalı."""
    menu = personel_istemci.get("/").text
    assert 'href="/yonetici"' not in menu
    assert 'href="/bilgi"' not in menu


def test_yonetici_girisi_kimlik_kartinda(girisli):
    """Yönetici paneline giriş kenar çubuğunun altındaki kimlik kartından.

    Günlük menünün arasında durmuyor: buradaki sayfalar ajanın söylediğini ve
    kimin panele girebildiğini değiştiriyor.
    """
    menu = girisli.get("/").text
    assert 'href="/yonetici"' in menu
    assert "nav-group yonetim" not in menu     # menü ortasında grup yok


@pytest.mark.parametrize("yol", ["/yonetici", "/bilgi", "/kullanicilar"])
def test_yonetici_sayfalari_sekmelerle_bagli(girisli, yol):
    assert "yon-sekme" in girisli.get(yol).text


# ── randevu görünümü ────────────────────────────────────────

def test_randevu_listesi_saate_gore_sirali(girisli, conn, kisi_id):
    from app.crm import randevu_olustur

    gun = (datetime.now() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    if gun.isoweekday() == 7:
        gun += timedelta(days=1)

    for saat in (15, 9, 12):
        bas = gun.replace(hour=saat)
        randevu_olustur(conn, kisi_id, f"İş-{saat}", bas, bas + timedelta(minutes=30))
    conn.commit()

    metin = girisli.get("/randevular").text
    assert metin.index("İş-9") < metin.index("İş-12") < metin.index("İş-15")


# ── ajanın kullandığı iç API ────────────────────────────────

def test_ic_api_anahtarsiz_reddedilir(istemci, kisi_id):
    r = istemci.post("/api/randevu", json={
        "telefon": "905321112233", "hizmet": "Kontrol",
        "baslangic": "2026-12-01T10:00:00", "bitis": "2026-12-01T10:30:00",
    })
    assert r.status_code == 401


def test_ic_api_panel_cookiesi_ile_acilmaz(girisli, kisi_id):
    """Panel oturumu iç API'yi açmamalı — iki ayrı yetki."""
    r = girisli.post("/api/randevu", json={
        "telefon": "905321112233", "hizmet": "Kontrol",
        "baslangic": "2026-12-01T10:00:00", "bitis": "2026-12-01T10:30:00",
    })
    assert r.status_code == 401


def test_ic_api_dogru_anahtarla_randevu_yazar(istemci, conn, kisi_id):
    gun = datetime.now() + timedelta(days=2)
    if gun.isoweekday() == 7:
        gun += timedelta(days=1)
    bas = gun.replace(hour=10, minute=0, second=0, microsecond=0)

    r = istemci.post(
        "/api/randevu",
        json={
            "telefon": "905321112233", "hizmet": "Kontrol",
            "baslangic": bas.isoformat(), "bitis": (bas + timedelta(minutes=30)).isoformat(),
        },
        headers={"X-Ic-Anahtar": "test-ic-anahtar"},
    )
    assert r.status_code in (200, 201)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM randevular WHERE hizmet = 'Kontrol'")
        assert cur.fetchone()[0] == 1


def test_ic_api_cakismayi_bildirir(istemci, conn, kisi_id):
    """Ajan doluluk cevabını buradan öğrenir — 500 değil, anlaşılır bir hata dönmeli."""
    gun = datetime.now() + timedelta(days=2)
    if gun.isoweekday() == 7:
        gun += timedelta(days=1)
    bas = gun.replace(hour=11, minute=0, second=0, microsecond=0)
    govde = {
        "telefon": "905321112233", "hizmet": "Kontrol",
        "baslangic": bas.isoformat(), "bitis": (bas + timedelta(minutes=30)).isoformat(),
    }
    basliklar = {"X-Ic-Anahtar": "test-ic-anahtar"}

    istemci.post("/api/randevu", json=govde, headers=basliklar)
    r = istemci.post("/api/randevu", json=govde, headers=basliklar)

    assert r.status_code == 409
    assert "dolu" in r.text.lower() or "çakış" in r.text.lower()
