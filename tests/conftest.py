"""Ortak test altyapısı.

Hiçbir test LLM çağırmaz. Postgres'e ihtiyaç duyan testler DATABASE_URL yoksa atlanır,
böylece `pytest` docker ayakta olmadan da anlamlı bir sonuç verir.
"""

import os
import pytest

os.environ.setdefault("CALISMA_GUNLERI", "1,2,3,4,5,6")
os.environ.setdefault("CALISMA_SAATLERI", "09:00-18:00")
os.environ.setdefault("WEBHOOK_SECRET", "test-gizli")
os.environ.setdefault("PANEL_PAROLA", "test-parola")
os.environ.setdefault("COOKIE_SECRET", "test-cookie-secret")
os.environ.setdefault("IC_API_ANAHTARI", "test-ic-anahtar")
os.environ.setdefault("OPENWA_URL", "http://localhost:2785")
os.environ.setdefault("OPENWA_SESSION", "test")


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
