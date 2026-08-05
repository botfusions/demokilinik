"""Panel grafiklerini besleyen istatistikler.

Yanlış bir grafik, kliniğin personel planını yanlış güne kurmasına yol açar —
sessizce yanlış olur, kimse fark etmez.
"""

from datetime import datetime, timedelta

from app.crm import (
    gun_bazli_doluluk,
    hizmet_dagilimi,
    ozet_sayilar,
    randevu_iptal,
    randevu_olustur,
    saat_bazli_doluluk,
)


def _yarin(saat, gun_ekle=1):
    g = datetime.now() + timedelta(days=gun_ekle)
    while g.isoweekday() == 7:
        g += timedelta(days=1)
    return g.replace(hour=saat, minute=0, second=0, microsecond=0)


def _randevu(conn, kisi_id, saat, gun_ekle=1, hizmet="Kontrol"):
    bas = _yarin(saat, gun_ekle)
    return randevu_olustur(conn, kisi_id, hizmet, bas, bas + timedelta(minutes=30)), bas


# ── gün bazlı ───────────────────────────────────────────────

def test_gun_grafigi_yedi_gun_dondurur(conn):
    gunler = gun_bazli_doluluk(conn)
    assert len(gunler) == 7
    assert [g["ad"] for g in gunler] == ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def test_randevusuz_gun_sifir_gosterir(conn):
    """Boş gün grafikten düşmemeli — 'o gün hiç yok' bilgisi de bilgidir."""
    assert all(g["adet"] == 0 for g in gun_bazli_doluluk(conn))


def test_randevu_dogru_gune_yazilir(conn, kisi_id):
    _, bas = _randevu(conn, kisi_id, 10)
    gunler = gun_bazli_doluluk(conn)
    hedef = next(g for g in gunler if g["gun"] == bas.isoweekday())

    assert hedef["adet"] == 1
    assert sum(g["adet"] for g in gunler) == 1


def test_iptal_edilen_randevu_grafige_girmez(conn, kisi_id):
    rid, _ = _randevu(conn, kisi_id, 10)
    randevu_iptal(conn, rid)
    assert sum(g["adet"] for g in gun_bazli_doluluk(conn)) == 0


def test_kapali_gun_isaretlenir(conn):
    """CALISMA_GUNLERI=1..6 — pazar kapalı olarak görünmeli."""
    gunler = {g["ad"]: g for g in gun_bazli_doluluk(conn)}
    assert gunler["Paz"]["acik"] is False
    assert gunler["Çar"]["acik"] is True


# ── saat bazlı ──────────────────────────────────────────────

def test_saat_grafigi_calisma_penceresini_kapsar(conn):
    """CALISMA_SAATLERI=09:00-18:00 → 09..17 arası dokuz sütun."""
    saatler = saat_bazli_doluluk(conn)
    assert [s["saat"] for s in saatler] == list(range(9, 18))


def test_randevu_dogru_saate_yazilir(conn, kisi_id):
    _randevu(conn, kisi_id, 14)
    saatler = {s["saat"]: s["adet"] for s in saat_bazli_doluluk(conn)}
    assert saatler[14] == 1
    assert saatler[15] == 0


# ── hizmet dağılımı ─────────────────────────────────────────

def test_hizmetler_coktan_aza_sirali(conn, kisi_id):
    _randevu(conn, kisi_id, 9, hizmet="Dolgu")
    _randevu(conn, kisi_id, 10, hizmet="Kontrol")
    _randevu(conn, kisi_id, 11, hizmet="Kontrol")

    dagilim = hizmet_dagilimi(conn)
    assert dagilim[0]["hizmet"] == "Kontrol"
    assert dagilim[0]["adet"] == 2
    assert [h["adet"] for h in dagilim] == sorted((h["adet"] for h in dagilim), reverse=True)


def test_limit_disi_hizmetler_diger_olur(conn, kisi_id):
    """Sekizinci hizmet yeni bir renk/satır açmamalı — 'Diğer'e katlanmalı."""
    for i, saat in enumerate(range(9, 17)):
        _randevu(conn, kisi_id, saat, hizmet=f"Hizmet-{i}")

    dagilim = hizmet_dagilimi(conn, limit=3)
    assert len(dagilim) == 4
    assert dagilim[-1]["hizmet"] == "Diğer"
    assert dagilim[-1]["adet"] == 5
    assert sum(h["adet"] for h in dagilim) == 8


def test_az_hizmette_diger_satiri_yok(conn, kisi_id):
    _randevu(conn, kisi_id, 9, hizmet="Dolgu")
    assert [h["hizmet"] for h in hizmet_dagilimi(conn, limit=3)] == ["Dolgu"]


# ── künye sayıları ──────────────────────────────────────────

def test_ozet_sayilari(conn, kisi_id):
    from app.crm import gorusme_ekle

    _randevu(conn, kisi_id, 10)
    iptal_id, _ = _randevu(conn, kisi_id, 11)
    randevu_iptal(conn, iptal_id)
    gorusme_ekle(conn, kisi_id, "gelen", "Merhaba")
    gorusme_ekle(conn, kisi_id, "giden", "Hoş geldiniz")

    s = ozet_sayilar(conn)
    assert s["toplam_hasta"] == 1
    assert s["bekleyen_randevu"] == 1        # iptal sayılmaz
    assert s["haftalik_mesaj"] == 1          # yalnız gelen mesajlar
