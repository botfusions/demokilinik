"""Ortak test altyapısı.

Hiçbir test LLM çağırmaz. Postgres'e ihtiyaç duyan testler DATABASE_URL yoksa atlanır,
böylece `pytest` docker ayakta olmadan da anlamlı bir sonuç verir.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# DATABASE_URL gibi ortama özgü ayarlar .env'den gelir...
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ...ama sırlar ve kurallar test sabitidir. Gerçek .env değerleri testleri
# sessizce bozmasın diye burada kesin olarak eziliyor.
os.environ["CALISMA_GUNLERI"] = "1,2,3,4,5,6"
os.environ["CALISMA_SAATLERI"] = "09:00-18:00"
os.environ["WEBHOOK_SECRET"] = "test-gizli"
os.environ["PANEL_PAROLA"] = "test-parola"
os.environ["COOKIE_SECRET"] = "test-cookie-secret"
os.environ["IC_API_ANAHTARI"] = "test-ic-anahtar"
os.environ["OPENWA_SESSION"] = "test"
os.environ["SAGLIK_NOBETCISI"] = "0"   # testlerde arka plan nöbetçisi çalışmaz


@pytest.fixture
def conn():
    """Boş şemalı bir bağlantı. Her test kendi işlemini geri alır."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL yok — `docker compose up -d` ve .env gerekli")

    from app.db import baglan, sema_kur

    c = baglan()
    sema_kur(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE gorusmeler, randevular, kisiler, bilgi_tabani, "
            "baglanti_saglik RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def kisi_id(conn):
    from app.crm import kisi_upsert

    return kisi_upsert(conn, "905321112233", "Test Hasta")
