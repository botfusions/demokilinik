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


def _test_veritabani_url() -> str | None:
    """Testler ASLA canlı veritabanına dokunmaz.

    Fixture TRUNCATE atıyor; DATABASE_URL'e yönelseydi bir `pytest` komutu
    kliniğin hasta kayıtlarını silerdi. Bu yüzden ayrı bir `_test` veritabanı
    kullanılır ve yoksa testler atlanır — sessizce canlıya düşmez.
    """
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]

    canli = os.environ.get("DATABASE_URL")
    if not canli:
        return None

    temel, _, ad = canli.rpartition("/")
    ad_yalin = ad.split("?", 1)[0]
    if ad_yalin.endswith("_test"):
        return canli
    return f"{temel}/{ad_yalin}_test"


@pytest.fixture
def conn():
    """Boş şemalı bir bağlantı. Her test kendi işlemini geri alır."""
    url = _test_veritabani_url()
    if not url:
        pytest.skip("DATABASE_URL yok — `docker compose up -d` ve .env gerekli")

    os.environ["DATABASE_URL"] = url  # uygulama kodu da test veritabanına baksın

    from app.db import baglan, sema_kur

    try:
        c = baglan()
    except Exception as e:
        pytest.skip(f"Test veritabanı yok ({url}): {e}\n"
                    f"Oluştur: docker compose exec postgres createdb -U klinik klinik_crm_test")
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
