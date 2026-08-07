"""Doktora yeni randevu bildirimi — tek alıcı, KVKK'ya uygun asgari içerik."""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app import bildirim
from app.crm import doktor_ekle, kisi_upsert, randevu_olustur


@pytest.fixture
def yarin():
    y = datetime.now(timezone.utc) + timedelta(days=1)
    return y.replace(hour=14, minute=0, second=0, microsecond=0)


@pytest.fixture
def randevu_id(conn, yarin):
    did = doktor_ekle(conn, "Dr. Deniz Kaya", "İmplant", telefon="905321110000")
    kid = kisi_upsert(conn, "905321119999", "Mehmet K.")
    return randevu_olustur(conn, kid, "İmplant görüşmesi", yarin, yarin + timedelta(minutes=30),
                           notlar="Alt çenede eksik diş olduğunu belirtti.", doktor_id=did)


def test_doktor_telefonu_yoksa_gonderilmez(conn, yarin):
    did = doktor_ekle(conn, "Dr. Selin Arslan", "Ortodonti")   # telefon yok
    kid = kisi_upsert(conn, "905321118888")
    rid = randevu_olustur(conn, kid, "Ortodonti", yarin, yarin + timedelta(minutes=30), doktor_id=did)

    cagrildi = []
    assert bildirim.yeni_randevu_bildir(conn, rid, gonder_fn=lambda *a: cagrildi.append(a)) is False
    assert cagrildi == []


def test_kapaliysa_gonderilmez(conn, randevu_id, monkeypatch):
    monkeypatch.setenv("DOKTORA_BILDIRIM", "0")
    cagrildi = []
    assert bildirim.yeni_randevu_bildir(conn, randevu_id, gonder_fn=lambda *a: cagrildi.append(a)) is False
    assert cagrildi == []


def test_dogru_telefona_gider(conn, randevu_id):
    cagrildi = []
    sonuc = bildirim.yeni_randevu_bildir(conn, randevu_id, gonder_fn=lambda tel, metin: cagrildi.append((tel, metin)))
    assert sonuc is True
    assert cagrildi[0][0] == "905321110000"


def test_mesaj_notlar_icermez(conn, randevu_id):
    """KVKK — hastanın serbest metin şikayeti kilit ekranına sızmamalı."""
    cagrildi = []
    bildirim.yeni_randevu_bildir(conn, randevu_id, gonder_fn=lambda tel, metin: cagrildi.append(metin))
    metin = cagrildi[0]
    assert "eksik diş" not in metin
    assert "İmplant görüşmesi" in metin
    assert "Mehmet K." in metin


def test_gonderim_hatasi_sessizce_yutulur(conn, randevu_id):
    def patlat(*a):
        raise ConnectionError("wa kapalı")

    assert bildirim.yeni_randevu_bildir(conn, randevu_id, gonder_fn=patlat) is False


def test_toplu_gonderim_fonksiyonu_yok():
    """Alıcı listesi alan bir fonksiyon eklenirse bu test kırılır — kasıtlı."""
    for ad, nesne in inspect.getmembers(bildirim, inspect.isfunction):
        if nesne.__module__ != bildirim.__name__:
            continue
        imza = str(inspect.signature(nesne)).lower()
        assert "telefonlar" not in imza and "alicilar" not in imza, (
            f"bildirim.{ad} toplu alıcı alıyor — toplu mesaj yasağı ihlali"
        )
        assert "bulk" not in ad.lower() and "toplu" not in ad.lower(), (
            f"bildirim.{ad} toplu gönderim ima ediyor"
        )
