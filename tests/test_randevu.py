"""Randevu kuralları. Bu dosya ajanın hastaya yanlış saat vermesini engelleyen tek yer."""

from datetime import datetime, timedelta

import pytest

from app.crm import (
    CalismaSaatiDisi,
    GecmisTarih,
    RandevuCakismasi,
    calisma_saati_icinde,
    randevu_iptal,
    randevu_olustur,
    randevular_listele,
)


def _yarin(saat, dakika=0):
    """Yarının belirtilen saati. Pazar'a düşerse pazartesiye kaydır (CALISMA_GUNLERI=1-6)."""
    g = datetime.now().replace(hour=saat, minute=dakika, second=0, microsecond=0)
    g += timedelta(days=1)
    if g.isoweekday() == 7:
        g += timedelta(days=1)
    return g


def test_normal_randevu_olusur(conn, kisi_id):
    bas = _yarin(10)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    assert rid is not None


def test_cakisan_saat_reddedilir(conn, kisi_id):
    bas = _yarin(10)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=60))

    with pytest.raises(RandevuCakismasi):
        randevu_olustur(
            conn, kisi_id, "Dolgu",
            bas + timedelta(minutes=30), bas + timedelta(minutes=90),
        )


def test_sinir_temasi_cakisma_degildir(conn, kisi_id):
    """10:00-11:00 varken 11:00-12:00 kabul edilmeli."""
    bas = _yarin(10)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=60))

    rid = randevu_olustur(
        conn, kisi_id, "Dolgu",
        bas + timedelta(minutes=60), bas + timedelta(minutes=120),
    )
    assert rid is not None


def test_saran_randevu_da_cakisir(conn, kisi_id):
    """Mevcut randevuyu tamamen içine alan bir talep de reddedilmeli."""
    bas = _yarin(11)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))

    with pytest.raises(RandevuCakismasi):
        randevu_olustur(
            conn, kisi_id, "Uzun işlem",
            bas - timedelta(minutes=30), bas + timedelta(minutes=90),
        )


def test_gecmis_tarih_reddedilir(conn, kisi_id):
    bas = datetime.now() - timedelta(days=1)
    with pytest.raises(GecmisTarih):
        randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))


def test_calisma_saati_disi_reddedilir(conn, kisi_id):
    bas = _yarin(22)  # CALISMA_SAATLERI=09:00-18:00
    with pytest.raises(CalismaSaatiDisi):
        randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))


def test_kapanistan_tasan_randevu_reddedilir(conn, kisi_id):
    """17:45 başlayıp 18:15'te biten randevu kapanışı aşıyor."""
    bas = _yarin(17, 45)
    with pytest.raises(CalismaSaatiDisi):
        randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))


def test_iptal_edilen_saat_yeniden_acilir(conn, kisi_id):
    bas = _yarin(14)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    randevu_iptal(conn, rid)

    yeni = randevu_olustur(conn, kisi_id, "Dolgu", bas, bas + timedelta(minutes=30))
    assert yeni is not None


def test_listeleme_saate_gore_artan(conn, kisi_id):
    for saat in (15, 9, 12):
        bas = _yarin(saat)
        randevu_olustur(conn, kisi_id, f"İş-{saat}", bas, bas + timedelta(minutes=30))

    liste = randevular_listele(conn, gun=_yarin(9).date())
    saatler = [r["baslangic"].hour for r in liste]
    assert saatler == sorted(saatler)


def test_calisma_saati_icinde_yardimcisi():
    ici = _yarin(10)
    assert calisma_saati_icinde(ici, ici + timedelta(minutes=30)) is True

    disi = _yarin(7)
    assert calisma_saati_icinde(disi, disi + timedelta(minutes=30)) is False


def test_pazar_gunu_reddedilir(conn, kisi_id):
    """CALISMA_GUNLERI=1..6 — pazar kapalı."""
    g = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    while g.isoweekday() != 7:
        g += timedelta(days=1)

    with pytest.raises(CalismaSaatiDisi):
        randevu_olustur(conn, kisi_id, "Kontrol", g, g + timedelta(minutes=30))


def test_saat_kaymasi_yok(conn, kisi_id):
    """Saatsiz gelen zaman klinik saatinde kaydedilmeli.

    Regresyon: bağlantı UTC'ye düşerse ajanın yazdığı 14:00 randevusu
    panelde 17:00 görünüyordu.
    """
    bas = _yarin(14)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))

    r = randevular_listele(conn, gun=bas.date())[0]
    assert r["baslangic"].strftime("%H:%M") == "14:00"
    assert r["bitis"].strftime("%H:%M") == "14:30"
