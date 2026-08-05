"""Doktor katmanı: doktor bazlı çakışma ve otomatik dağıtım.

En kritik davranış: iki doktor aynı saatte iki hastaya bakabilmeli, aynı doktor
bakamamalı. Bu ters dönerse ya klinik boş yere randevu reddeder ya da bir hekime
iki hasta gönderir.
"""

from datetime import datetime, timedelta

import pytest

from app.crm import (
    DoktorYok,
    RandevuCakismasi,
    doktor_durum_yaz,
    doktor_ekle,
    doktor_musait_mi,
    doktorlar_listele,
    en_bos_doktor,
    en_erken_uygun,
    hastanin_doktoru,
    kisi_upsert,
    randevu_iptal,
    randevu_olustur,
)


def _yarin(saat, gun_ekle=1):
    g = datetime.now() + timedelta(days=gun_ekle)
    while g.isoweekday() == 7:
        g += timedelta(days=1)
    return g.replace(hour=saat, minute=0, second=0, microsecond=0)


@pytest.fixture
def doktorlar(conn):
    return [
        doktor_ekle(conn, "Dr. Ayla Tuncer", "Ortodonti"),
        doktor_ekle(conn, "Dr. Kerem Aksoy", "İmplantoloji"),
    ]


# ── doktor bazlı çakışma ────────────────────────────────────

def test_iki_doktor_ayni_saatte_calisabilir(conn, kisi_id, doktorlar):
    a, b = doktorlar
    bas = _yarin(10)

    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)
    rid = randevu_olustur(conn, kisi_id, "Dolgu", bas, bas + timedelta(minutes=30), doktor_id=b)
    assert rid is not None


def test_ayni_doktor_ayni_saatte_iki_hastaya_bakamaz(conn, kisi_id, doktorlar):
    a, _ = doktorlar
    bas = _yarin(10)

    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=60), doktor_id=a)
    with pytest.raises(RandevuCakismasi):
        randevu_olustur(conn, kisi_id, "Dolgu", bas + timedelta(minutes=30),
                        bas + timedelta(minutes=90), doktor_id=a)


def test_cakisma_mesaji_doktoru_soyler(conn, kisi_id, doktorlar):
    a, _ = doktorlar
    bas = _yarin(11)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)

    with pytest.raises(RandevuCakismasi, match="Ayla"):
        randevu_olustur(conn, kisi_id, "Dolgu", bas, bas + timedelta(minutes=30), doktor_id=a)


def test_doktorsuz_randevular_kendi_arasinda_cakisir(conn, kisi_id):
    """Klinik tek hekimliyse (doktor tanımlı değil) eski davranış sürmeli."""
    bas = _yarin(10)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    with pytest.raises(RandevuCakismasi):
        randevu_olustur(conn, kisi_id, "Dolgu", bas, bas + timedelta(minutes=30))


def test_pasif_doktora_randevu_acilmaz(conn, kisi_id, doktorlar):
    a, _ = doktorlar
    doktor_durum_yaz(conn, a, False)
    bas = _yarin(10)

    with pytest.raises(DoktorYok):
        randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)


def test_olmayan_doktor_reddedilir(conn, kisi_id):
    bas = _yarin(10)
    with pytest.raises(DoktorYok):
        randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=9999)


# ── otomatik dağıtım ────────────────────────────────────────

def test_en_bos_doktor_yuku_dengeler(conn, kisi_id, doktorlar):
    """İlk hastalar farklı hekimlere dağılmalı, hepsi ilkine yığılmamalı."""
    a, b = doktorlar
    gun = _yarin(9)

    # a'ya iki randevu yaz
    for saat in (9, 10):
        bas = gun.replace(hour=saat)
        randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)

    bas = gun.replace(hour=14)
    secilen = en_bos_doktor(conn, bas, bas + timedelta(minutes=30))
    assert secilen["id"] == b, "o gün daha az yüklü hekim seçilmeliydi"


def test_en_bos_doktor_dolu_olani_atlar(conn, kisi_id, doktorlar):
    a, b = doktorlar
    bas = _yarin(10)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)

    assert en_bos_doktor(conn, bas, bas + timedelta(minutes=30))["id"] == b


def test_herkes_doluysa_none_doner(conn, kisi_id, doktorlar):
    bas = _yarin(10)
    for d in doktorlar:
        randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=d)

    assert en_bos_doktor(conn, bas, bas + timedelta(minutes=30)) is None


def test_secim_kararli(conn, kisi_id, doktorlar):
    """Aynı girdiye hep aynı doktor — ajan iki kez sorunca fikir değiştirmesin."""
    bas = _yarin(10)
    ilk = en_bos_doktor(conn, bas, bas + timedelta(minutes=30))
    for _ in range(3):
        assert en_bos_doktor(conn, bas, bas + timedelta(minutes=30))["id"] == ilk["id"]


def test_pasif_doktor_otomatik_secilmez(conn, doktorlar):
    a, b = doktorlar
    doktor_durum_yaz(conn, a, False)
    bas = _yarin(10)
    assert en_bos_doktor(conn, bas, bas + timedelta(minutes=30))["id"] == b


# ── hastanın geçmiş doktoru ─────────────────────────────────

def test_ilk_gelen_hastanin_doktoru_yok(conn, kisi_id):
    assert hastanin_doktoru(conn, kisi_id) is None


def test_son_gidilen_doktor_hatirlanir(conn, kisi_id, doktorlar):
    a, b = doktorlar
    g1 = _yarin(10, gun_ekle=1)
    g2 = _yarin(10, gun_ekle=2)

    randevu_olustur(conn, kisi_id, "Kontrol", g1, g1 + timedelta(minutes=30), doktor_id=a)
    randevu_olustur(conn, kisi_id, "Kontrol", g2, g2 + timedelta(minutes=30), doktor_id=b)

    assert hastanin_doktoru(conn, kisi_id)["id"] == b, "en son gidilen hekim dönmeli"


def test_iptal_edilen_randevu_doktor_gecmisi_saymaz(conn, kisi_id, doktorlar):
    a, _ = doktorlar
    bas = _yarin(10)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)
    randevu_iptal(conn, rid)

    assert hastanin_doktoru(conn, kisi_id) is None


# ── acil: en erken slot ─────────────────────────────────────

def test_en_erken_slot_bulunur(conn, doktorlar):
    slot = en_erken_uygun(conn, 30)
    assert slot is not None
    assert slot["doktor_id"] in doktorlar


def test_en_erken_dolu_saati_atlar(conn, kisi_id, doktorlar):
    """İlk slot her iki hekimde de doluysa sonraki slot önerilmeli."""
    slot = en_erken_uygun(conn, 30)
    for d in doktorlar:
        randevu_olustur(conn, kisi_id, "Kontrol", slot["baslangic"], slot["bitis"], doktor_id=d)

    yeni = en_erken_uygun(conn, 30)
    assert yeni["baslangic"] > slot["baslangic"]


def test_en_erken_belirli_doktor_icin(conn, kisi_id, doktorlar):
    a, _ = doktorlar
    slot = en_erken_uygun(conn, 30, doktor_id=a)
    assert slot["doktor_id"] == a


def test_doktor_musait_mi(conn, kisi_id, doktorlar):
    a, b = doktorlar
    bas = _yarin(10)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)

    assert doktor_musait_mi(conn, a, bas, bas + timedelta(minutes=30)) is False
    assert doktor_musait_mi(conn, b, bas, bas + timedelta(minutes=30)) is True
    # Sınır teması müsaitliği bozmaz
    assert doktor_musait_mi(conn, a, bas + timedelta(minutes=30),
                            bas + timedelta(minutes=60)) is True


def test_listeleme_gelecek_randevu_sayar(conn, kisi_id, doktorlar):
    a, _ = doktorlar
    bas = _yarin(10)
    randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30), doktor_id=a)

    d = next(x for x in doktorlar_listele(conn) if x["id"] == a)
    assert d["gelecek_randevu"] == 1
