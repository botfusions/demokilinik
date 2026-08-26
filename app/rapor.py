"""Haftalık kullanım raporu — Telegram'a mesaj adedi + token, $ yok.

Fiyat hiç hesaplanmaz, gösterilmez: müşterinin görebileceği hiçbir yerde
(panel şablonları) bu sayılar yok, yalnızca Telegram'dan operatöre gider.
app/saglik.py'deki nöbetçiyle aynı desen — ayrı bir cron/servis gerekmez,
uygulama zaten sürekli ayaktayken bununla birlikte çalışır.
"""

import asyncio
import logging
import os

from app import crm, saglik

log = logging.getLogger("rapor")

ARALIK_SN = int(os.environ.get("RAPOR_ARALIK_SN", "604800"))  # 7 gün


def _mesaj(ozet: dict) -> str:
    m = (
        f"[{saglik.KLINIK_ADI}] Haftalık kullanım özeti\n\n"
        f"Gönderilen mesaj (WhatsApp): {ozet['giden_mesaj']}\n"
        f"Token: {ozet['giris_token'] + ozet['cikis_token']} "
        f"(giriş {ozet['giris_token']}, çıkış {ozet['cikis_token']})"
    )
    if ozet.get("devir"):
        d = ozet["devir"]
        m += "\nDevir: " + str(sum(d.values())) + " (" + ", ".join(f"{n} {c}" for n, c in d.items()) + ")"
    return m


async def nobetci(baglan_fn) -> None:
    """FastAPI'nin kendi döngüsünde çalışır — ayrı scheduler paketi yok."""
    while True:
        try:
            conn = baglan_fn()
            try:
                saglik.uyari_telegram_gonder(_mesaj(crm.kullanim_ozeti(conn, ARALIK_SN)))
            finally:
                conn.close()
        except Exception as e:
            log.error("Rapor nöbetçisi hatası: %s", e)
        await asyncio.sleep(ARALIK_SN)


if __name__ == "__main__":
    # self-check: mesaj formatı token toplamını ve devir dökümünü doğru gösteriyor mu.
    m = _mesaj({"giden_mesaj": 12, "giris_token": 4000, "cikis_token": 1500,
                "devir": {"Hasta personel talep etti": 3, "Personel devraldı": 1}})
    assert "Gönderilen mesaj (WhatsApp): 12" in m
    assert "Token: 5500" in m
    assert "giriş 4000, çıkış 1500" in m
    assert "Devir: 4 (Hasta personel talep etti 3, Personel devraldı 1)" in m
    assert "Devir" not in _mesaj({"giden_mesaj": 1, "giris_token": 0, "cikis_token": 0, "devir": {}})
    print("rapor self-check OK")
