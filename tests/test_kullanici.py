"""Kullanıcılar, paroları, roller ve işlem izi.

Panelde hasta verisi var. Buradaki bir gevşeklik "randevuyu kim değiştirdi"
sorusunu cevapsız bırakır ya da yetkisiz birini içeri alır.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.kullanici import (
    KullaniciVar,
    ParolaZayif,
    ilk_admin_kur,
    islem_kayitlari,
    islem_yaz,
    kullanici_dogrula,
    kullanici_durum_yaz,
    kullanici_ekle,
    kullanicilar_listele,
    parola_degistir,
    parola_dogrula,
    parola_hash,
)


# ── parola ──────────────────────────────────────────────────

def test_dogru_parola_gecer():
    h = parola_hash("gizliparola1")
    assert parola_dogrula("gizliparola1", h) is True


def test_yanlis_parola_gecmez():
    h = parola_hash("gizliparola1")
    assert parola_dogrula("gizliparola2", h) is False


def test_ayni_parola_farkli_hash_uretir():
    """Tuz olmadan iki aynı parola aynı hash'i verir ve tablo sızarsa toplu kırılır."""
    assert parola_hash("gizliparola1") != parola_hash("gizliparola1")


def test_parola_duz_metin_saklanmaz():
    h = parola_hash("gizliparola1")
    assert "gizliparola1" not in h
    assert h.startswith("scrypt$")


@pytest.mark.parametrize("bozuk", ["", "duzmetin", "scrypt$bozuk", "md5$1$1$aa$bb", "a$b$c$d$e$f"])
def test_bozuk_hash_cokmez_reddeder(bozuk):
    assert parola_dogrula("herhangi", bozuk) is False


def test_kisa_parola_reddedilir():
    with pytest.raises(ParolaZayif):
        parola_hash("kisa")


# ── kullanıcı ───────────────────────────────────────────────

def test_kullanici_acilir_ve_giris_yapar(conn):
    kullanici_ekle(conn, "ayse", "resepsiyon123", "personel", "Ayşe Yılmaz")
    k = kullanici_dogrula(conn, "ayse", "resepsiyon123")

    assert k is not None
    assert k["rol"] == "personel"
    assert k["ad"] == "Ayşe Yılmaz"


def test_kullanici_adi_buyuk_kucuk_harf_duyarsiz(conn):
    kullanici_ekle(conn, "Ayse", "resepsiyon123")
    assert kullanici_dogrula(conn, "AYSE", "resepsiyon123") is not None


def test_ayni_kullanici_adi_iki_kez_acilmaz(conn):
    kullanici_ekle(conn, "ayse", "resepsiyon123")
    with pytest.raises(KullaniciVar):
        kullanici_ekle(conn, "ayse", "baskaparola1")


def test_zayif_parolayla_kullanici_acilmaz(conn):
    with pytest.raises(ParolaZayif):
        kullanici_ekle(conn, "ayse", "1234")
    assert kullanicilar_listele(conn) == []


def test_yanlis_parolayla_giris_yok(conn):
    kullanici_ekle(conn, "ayse", "resepsiyon123")
    assert kullanici_dogrula(conn, "ayse", "yanlisparola") is None


def test_olmayan_kullanici(conn):
    assert kullanici_dogrula(conn, "hayalet", "herhangi123") is None


def test_pasif_kullanici_dogru_parolayla_da_giremez(conn):
    kid = kullanici_ekle(conn, "ayse", "resepsiyon123")
    kullanici_durum_yaz(conn, kid, False)
    assert kullanici_dogrula(conn, "ayse", "resepsiyon123") is None


def test_parola_degisince_eskisi_gecersiz(conn):
    kullanici_ekle(conn, "ayse", "resepsiyon123")
    kid = kullanicilar_listele(conn)[0]["id"]
    parola_degistir(conn, kid, "yeniparola456")

    assert kullanici_dogrula(conn, "ayse", "resepsiyon123") is None
    assert kullanici_dogrula(conn, "ayse", "yeniparola456") is not None


def test_son_yonetici_pasiflestirilemez(conn):
    """Aksi halde panele kimse giremez ve kurtarmak için DB'ye girmek gerekir."""
    kid = kullanici_ekle(conn, "admin", "yoneticiparola", "admin")
    with pytest.raises(ValueError, match="Son yönetici"):
        kullanici_durum_yaz(conn, kid, False)


def test_ikinci_yonetici_varken_pasiflestirilebilir(conn):
    a = kullanici_ekle(conn, "admin", "yoneticiparola", "admin")
    kullanici_ekle(conn, "admin2", "yoneticiparola", "admin")
    kullanici_durum_yaz(conn, a, False)   # patlamamalı
    assert kullanici_dogrula(conn, "admin", "yoneticiparola") is None


def test_son_personel_pasiflestirilebilir(conn):
    """Kural yalnız yöneticiler için — personel serbestçe kapatılabilir."""
    kullanici_ekle(conn, "admin", "yoneticiparola", "admin")
    kid = kullanici_ekle(conn, "ayse", "resepsiyon123")
    kullanici_durum_yaz(conn, kid, False)


def test_ilk_admin_bir_kez_kurulur(conn, monkeypatch):
    monkeypatch.setenv("PANEL_PAROLA", "kurulumparolasi")

    assert ilk_admin_kur(conn) == "admin"
    assert ilk_admin_kur(conn) is None, "ikinci çağrı yeni admin açmamalı"
    assert len(kullanicilar_listele(conn)) == 1


def test_zayif_panel_parolasiyla_admin_kurulmaz(conn, monkeypatch):
    monkeypatch.setenv("PANEL_PAROLA", "kisa")
    assert ilk_admin_kur(conn) is None


# ── işlem izi ───────────────────────────────────────────────

def test_islem_kaydi_kimi_yazar(conn):
    kid = kullanici_ekle(conn, "ayse", "resepsiyon123")
    k = kullanici_dogrula(conn, "ayse", "resepsiyon123")
    islem_yaz(conn, k, "randevu iptal etti", "#42")

    kayit = islem_kayitlari(conn)[0]
    assert kayit["kullanici_adi"] == "ayse"
    assert kayit["kullanici_id"] == kid
    assert kayit["eylem"] == "randevu iptal etti"
    assert kayit["detay"] == "#42"


def test_kayitlar_yeniden_eskiye(conn):
    k = {"id": None, "kullanici_adi": "test"}
    for i in range(3):
        islem_yaz(conn, k, f"eylem-{i}")

    assert [x["eylem"] for x in islem_kayitlari(conn)] == ["eylem-2", "eylem-1", "eylem-0"]


# ── panel yetkilendirmesi ───────────────────────────────────

@pytest.fixture
def admin_istemci(conn):
    kullanici_ekle(conn, "admin", "yoneticiparola", "admin")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "admin", "parola": "yoneticiparola"})
    return c


@pytest.fixture
def personel_istemci(conn):
    kullanici_ekle(conn, "admin", "yoneticiparola", "admin")   # son-admin kuralı için
    kullanici_ekle(conn, "ayse", "resepsiyon123", "personel")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "ayse", "parola": "resepsiyon123"})
    return c


def test_yanlis_parola_iceri_almaz(conn):
    kullanici_ekle(conn, "ayse", "resepsiyon123")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "ayse", "parola": "yanlis"})
    assert c.get("/").status_code in (302, 303, 307)


@pytest.mark.parametrize("yol", ["/", "/randevular", "/hastalar", "/doktorlar"])
def test_personel_gunluk_sayfalari_gorur(personel_istemci, yol):
    assert personel_istemci.get(yol).status_code == 200


def test_personel_kullanicilari_goremez(personel_istemci):
    assert personel_istemci.get("/kullanicilar").status_code == 403


def test_personel_bilgi_tabanini_goremez(personel_istemci):
    """Ajanın söyleyeceği fiyat/garanti bilgisini değiştirmek yönetici işi."""
    assert personel_istemci.get("/bilgi").status_code == 403


def test_personel_doktor_ekleyemez(personel_istemci, conn):
    r = personel_istemci.post("/doktorlar", data={"ad": "Dr. Sahte"})
    assert r.status_code == 403

    from app.crm import doktorlar_listele
    assert doktorlar_listele(conn) == []


def test_personel_kullanici_acamaz(personel_istemci, conn):
    r = personel_istemci.post("/kullanicilar", data={
        "kullanici_adi": "sahte", "parola": "sahteparola", "rol": "admin"})
    assert r.status_code == 403
    assert not any(k["kullanici_adi"] == "sahte" for k in kullanicilar_listele(conn))


def test_yonetici_kullanici_acabilir(admin_istemci, conn):
    admin_istemci.post("/kullanicilar", data={
        "kullanici_adi": "yeni", "parola": "yeniparola123", "rol": "personel"})
    assert kullanici_dogrula(conn, "yeni", "yeniparola123") is not None


def test_pasiflestirilen_kullanicinin_oturumu_kapanir(personel_istemci, conn):
    """Açık sekmesi olan biri pasifleştirilince bir sonraki istekte düşmeli."""
    assert personel_istemci.get("/").status_code == 200

    kid = next(k["id"] for k in kullanicilar_listele(conn) if k["kullanici_adi"] == "ayse")
    kullanici_durum_yaz(conn, kid, False)

    assert personel_istemci.get("/").status_code in (302, 303, 307)


def test_panel_islemi_ize_dusuyor(admin_istemci, conn):
    admin_istemci.post("/bilgi", data={
        "baslik": "İmplant", "icerik": "25.000 TL", "kategori": "fiyatlar"})

    kayitlar = islem_kayitlari(conn)
    assert any(k["eylem"] == "bilgi ekledi" and k["kullanici_adi"] == "admin" for k in kayitlar)


def test_giris_ize_dusuyor(admin_istemci, conn):
    assert any(k["eylem"] == "giriş" for k in islem_kayitlari(conn))


def test_ic_api_panel_cookiesiyle_acilmaz(admin_istemci):
    """Yönetici oturumu bile ajanın iç API'sini açmamalı — iki ayrı yetki."""
    r = admin_istemci.get("/api/doktorlar")
    assert r.status_code == 401
