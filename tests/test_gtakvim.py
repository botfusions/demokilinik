"""Google Takvim köprüsü — Composio'ya gerçek çağrı yapılmaz, `_cagir` sahtelenir.

Denetlenen sınırlar:
- anahtar yoksa takvim tamamen sessiz (randevu akışı etkilenmez)
- hastanın serbest metin notu etkinliğe SIZMAZ (KVKK, bildirim.py ile aynı kural)
- takvim hatası randevuyu düşürmez
"""

from datetime import datetime, timedelta

import pytest

from app import crm, gtakvim


@pytest.fixture
def acik(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "sahte")
    monkeypatch.setenv("TAKVIM_KULLANICI", "pg-test")


def test_anahtar_yoksa_kapali(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("TAKVIM_KULLANICI", raising=False)
    assert gtakvim.yapilandirildi_mi() is False


def test_sure_saat_ve_dakikaya_bolunur():
    """Araç dakikayı 0-59 ile sınırlıyor; 90 dk → 1 saat 30 dk."""
    bas = datetime(2026, 8, 12, 14, 0)
    assert gtakvim._sure(bas, bas + timedelta(minutes=90)) == (1, 30)
    assert gtakvim._sure(bas, bas + timedelta(minutes=30)) == (0, 30)
    assert gtakvim._sure(bas, bas + timedelta(hours=2)) == (2, 0)


def test_davetli_ve_zaman_bicimi(acik, monkeypatch):
    yakalanan = {}
    monkeypatch.setattr(gtakvim, "_cagir",
                        lambda arac, **a: yakalanan.update(a) or {"id": "evt_1"})

    bas = datetime(2026, 8, 12, 14, 0)
    eid = gtakvim.etkinlik_olustur("İmplant — Ayşe", bas, bas + timedelta(minutes=30),
                                   "Hasta: Ayşe", davetli="hekim@ornek.com")

    assert eid == "evt_1"
    assert yakalanan["attendees"] == ["hekim@ornek.com"]
    # Araç naive zaman istiyor: offset/Z olmamalı
    assert yakalanan["start_datetime"] == "2026-08-12T14:00:00"
    assert "+" not in yakalanan["start_datetime"] and "Z" not in yakalanan["start_datetime"]


def test_hasta_notu_etkinlige_sizmaz(acik, conn, monkeypatch):
    """KVKK: şikayet/semptom içerebilen serbest metin takvimde görünmemeli."""
    yakalanan = {}
    monkeypatch.setattr(gtakvim, "_cagir",
                        lambda arac, **a: yakalanan.update(a) or {"id": "evt_2"})

    kid = crm.kisi_upsert(conn, "905321112233", "Ayşe Yılmaz")
    bas = datetime.now().astimezone().replace(microsecond=0) + timedelta(days=1, hours=2)
    bas = bas.replace(hour=11, minute=0, second=0)
    rid = crm.randevu_olustur(conn, kid, "İmplant", bas, bas + timedelta(minutes=30),
                              notlar="Sol alt azı dişte iltihap şüphesi")

    metin = str(yakalanan)
    assert "iltihap" not in metin
    assert "Ayşe Yılmaz" in metin

    with conn.cursor() as cur:
        cur.execute("SELECT google_event_id FROM randevular WHERE id = %s", (rid,))
        assert cur.fetchone()[0] == "evt_2"


def test_takvim_patlarsa_randevu_yine_acilir(acik, conn, monkeypatch):
    def patla(arac, **a):
        raise gtakvim.TakvimHatasi("Composio'ya ulaşılamadı")

    monkeypatch.setattr(gtakvim, "_cagir", patla)

    kid = crm.kisi_upsert(conn, "905321112244", "Veli Kaya")
    bas = (datetime.now().astimezone() + timedelta(days=1)).replace(
        hour=11, minute=0, second=0, microsecond=0)
    rid = crm.randevu_olustur(conn, kid, "Kontrol", bas, bas + timedelta(minutes=30))

    assert rid > 0        # randevu açıldı, takvim hatası yutuldu
