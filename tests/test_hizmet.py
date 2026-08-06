"""Fiyat listesi ve kampanyalar.

En kritik iki şey: ajanın söylediği fiyatın doğru olması ve kampanyanın
mesaj göndermemesi.
"""

from datetime import date, timedelta

import pytest

from app.hizmet import (
    HizmetVar,
    fiyat_guncelle,
    fiyat_metni,
    hizmet_ekle,
    hizmetler_listele,
    indirimli_fiyat,
    kampanya_durum_yaz,
    kampanya_ekle,
    kampanyalar_listele,
)
from app.kb import hermes_md_uret

DUN = date.today() - timedelta(days=1)
YARIN = date.today() + timedelta(days=1)


@pytest.fixture
def temizlik(conn):
    return hizmet_ekle(conn, "Diş taşı temizliği", 1000)


def _hizmet(conn, hid):
    return [h for h in hizmetler_listele(conn) if h["id"] == hid][0]


# ── fiyat ───────────────────────────────────────────────────

def test_ayni_ad_ikinci_kez_eklenemez(conn, temizlik):
    with pytest.raises(HizmetVar):
        hizmet_ekle(conn, "diş taşı temizliği", 1200)     # büyük/küçük harf farkı da


def test_fiyat_guncelleme_oncekini_saklar(conn, temizlik):
    assert fiyat_guncelle(conn, temizlik, 1200) is True
    h = _hizmet(conn, temizlik)
    assert (h["fiyat"], h["onceki_fiyat"]) == (1200, 1000)


def test_ayni_fiyat_guncelleme_sayilmaz(conn, temizlik):
    """Değişmeyen fiyat 'güncellendi' görünmemeli — panelde yanlış tarih olurdu."""
    assert fiyat_guncelle(conn, temizlik, 1000) is False
    assert _hizmet(conn, temizlik)["onceki_fiyat"] is None


# ── kampanya seçimi ─────────────────────────────────────────

def test_kampanyasiz_fiyat(conn, temizlik):
    fiyat, k = indirimli_fiyat(_hizmet(conn, temizlik), [])
    assert (fiyat, k) == (1000, None)


def test_hizmete_ozel_kampanya(conn, temizlik):
    kampanya_ekle(conn, "Yaz", 20, temizlik, YARIN)
    fiyat, k = indirimli_fiyat(_hizmet(conn, temizlik), kampanyalar_listele(conn))
    assert fiyat == 800 and k["ad"] == "Yaz"


def test_suresi_gecmis_kampanya_uygulanmaz(conn, temizlik):
    kampanya_ekle(conn, "Bitmiş", 50, temizlik, DUN)
    fiyat, k = indirimli_fiyat(_hizmet(conn, temizlik), kampanyalar_listele(conn))
    assert (fiyat, k) == (1000, None)


def test_kapali_kampanya_uygulanmaz(conn, temizlik):
    kid = kampanya_ekle(conn, "Yaz", 20, temizlik, YARIN)
    kampanya_durum_yaz(conn, kid, False)
    fiyat, k = indirimli_fiyat(_hizmet(conn, temizlik), kampanyalar_listele(conn))
    assert (fiyat, k) == (1000, None)


def test_hizmete_ozel_genele_baskin(conn, temizlik):
    """Klinik bir hizmete özel indirim tanımladıysa kastı odur — daha yüksek olsa bile."""
    kampanya_ekle(conn, "Genel", 40, None, None)
    kampanya_ekle(conn, "Sadece temizlik", 10, temizlik, None)
    fiyat, k = indirimli_fiyat(_hizmet(conn, temizlik), kampanyalar_listele(conn))
    assert k["ad"] == "Sadece temizlik" and fiyat == 900


def test_ayni_duzeyde_yuksek_indirim_secilir(conn, temizlik):
    kampanya_ekle(conn, "Az", 10, temizlik, None)
    kampanya_ekle(conn, "Çok", 30, temizlik, None)
    _, k = indirimli_fiyat(_hizmet(conn, temizlik), kampanyalar_listele(conn))
    assert k["ad"] == "Çok"


def test_secim_kararli(conn, temizlik):
    """Ajan aynı soruya iki kez farklı fiyat söylememeli."""
    kampanya_ekle(conn, "A", 20, temizlik, None)
    kampanya_ekle(conn, "B", 20, temizlik, None)
    h, ks = _hizmet(conn, temizlik), kampanyalar_listele(conn)
    assert indirimli_fiyat(h, ks)[1]["ad"] == indirimli_fiyat(h, ks)[1]["ad"] == "A"


def test_genel_kampanya_tum_hizmetlere(conn, temizlik):
    implant = hizmet_ekle(conn, "İmplant", 20000)
    kampanya_ekle(conn, "Genel", 25, None, None)
    ks = kampanyalar_listele(conn)
    assert indirimli_fiyat(_hizmet(conn, implant), ks)[0] == 15000
    assert indirimli_fiyat(_hizmet(conn, temizlik), ks)[0] == 750


# ── ajana giden metin ───────────────────────────────────────

def test_fiyat_metni_turkce_bicim(conn):
    hid = hizmet_ekle(conn, "İmplant", 25000)
    assert fiyat_metni(_hizmet(conn, hid), []) == "25.000 TL."


def test_fiyat_metni_kampanyali(conn, temizlik):
    kampanya_ekle(conn, "Yaz Kampanyası", 20, temizlik, date(2026, 8, 31))
    metin = fiyat_metni(_hizmet(conn, temizlik), kampanyalar_listele(conn, yalniz_gecerli=True))
    assert "1.000 TL" in metin and "%20" in metin and "800 TL" in metin
    assert "31.08.2026" in metin


def test_suresiz_kampanyada_tarih_yazilmaz(conn, temizlik):
    kampanya_ekle(conn, "Süresiz", 10, temizlik, None)
    metin = fiyat_metni(_hizmet(conn, temizlik), kampanyalar_listele(conn, yalniz_gecerli=True))
    assert "tarihine kadar" not in metin


def test_hermes_md_fiyatlari_iceriyor(conn, temizlik):
    kampanya_ekle(conn, "Yaz", 20, temizlik, YARIN)
    md = hermes_md_uret(conn)
    assert "## fiyatlar" in md
    assert "Diş taşı temizliği" in md and "800 TL" in md


def test_pasif_hizmet_ajana_gitmez(conn, temizlik):
    from app.hizmet import hizmet_durum_yaz

    hizmet_durum_yaz(conn, temizlik, False)
    assert "Diş taşı temizliği" not in hermes_md_uret(conn)


def test_kampanya_kapaninca_indirim_kayboluyor(conn, temizlik):
    kid = kampanya_ekle(conn, "Yaz", 20, temizlik, YARIN)
    assert "800 TL" in hermes_md_uret(conn)
    kampanya_durum_yaz(conn, kid, False)
    assert "800 TL" not in hermes_md_uret(conn)


def test_bos_bilgi_tabani_uyarisi_fiyat_varken_cikmaz(conn, temizlik):
    """Fiyat girilmişse ajan 'hiçbir şey bilmiyorum' uyarısını almamalı."""
    assert "Bilgi tabanı henüz boş" not in hermes_md_uret(conn)


# ── kampanya gönderim yapmaz ────────────────────────────────

def test_kampanya_gonderim_yapmaz():
    """Kampanya bir duyuru aracı DEĞİLDİR.

    `test_toplu_gonderim_fonksiyonu_yok`'un kardeşi: kampanya özelliği eklendi
    diye toplu mesaj kapısı açılmasın. Bkz. README § Toplu mesaj yasağı.
    """
    import inspect

    from app import hizmet

    kaynak = inspect.getsource(hizmet)
    for yasak in ("mesaj_gonder", "openwa", "instagram", "def toplu", "def duyur"):
        assert yasak not in kaynak, f"hizmet.py'de gönderim izi: {yasak}"


def test_kampanya_giden_mesaj_uretmiyor(conn, temizlik):
    """Kampanya eklemek hiçbir görüşme kaydı yaratmamalı."""
    kampanya_ekle(conn, "Yaz", 20, temizlik, YARIN)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gorusmeler WHERE yon = 'giden'")
        assert cur.fetchone()[0] == 0
