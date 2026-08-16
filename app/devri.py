"""İnsan devri — kanal yardımcıları + otomatik geri alma nöbetçisi.

PRD: 16-08-2026-PRD-insan-devri.md. K1: personel cevap vermezse asistan
sonsuza kadar susmuş olur — bu, botun cevap vermesinden daha kötü. Bu nöbetçi
2 saat cevapsız kalan devri düşürür, hastaya bilgi mesajı gönderir, asistan
geri döner. Sayaç `kisiler.insan_devri_at`'tan işler: panel mesaj gönderen
personel bu değeri now() ile tazeler (bkz. crm.devir_yaz).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from app import crm, openwa

log = logging.getLogger(__name__)

ARALIK_SN = 60   # dakika hassasiyeti yeterli; saniye başına tarama gereksiz

GUN_ADLARI = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]

AKTARIM_MESAJI = (
    "Sizi klinik personelimize aktarıyorum, en kısa sürede dönüş yapılacak."
)
GERI_ALMA_MESAJI = (
    "Personelimize şu an ulaşamadık, çalışma saatlerinde size dönecekler. "
    "Bu arada ben yardımcı olabilirim."
)


def _otomatik_saat() -> float:
    return float(os.environ.get("DEVRI_OTOMATIK_SAAT", "2"))


def gonder(kanal: str, telefon: str, metin: str) -> None:
    """Kanala göre gönderim — bugün yalnız WhatsApp (PRD sınır 2).

    Yeni kanal (Meta/Instagram) eklendiğinde çatal buraya, çağıranlara değil.
    """
    if kanal != "whatsapp":
        raise ValueError(f"Gönderim desteklenmiyor: kanal='{kanal}'")
    openwa.mesaj_gonder(telefon, metin)


def son_kanal(conn, kisi_id: int) -> str:
    """Kişinin son görüşmesinin kanalı — cevap oradan gitmeli."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT kanal FROM gorusmeler WHERE kisi_id = %s ORDER BY id DESC LIMIT 1",
            (kisi_id,),
        )
        satir = cur.fetchone()
    return satir[0] if satir else "whatsapp"


def aktarim_mesaji(simdi: datetime) -> str:
    """K2: mesai dışında gelen devir talebine dönüş zamanı eklenir.

    Çalışma penceresi `ayarlar` tablosundan (env'e basılıyor) okunur —
    yeni pencere yazılınca mesaj kendiliğinden doğru olur.
    """
    gunler, ac, _ = crm._calisma_penceresi()
    if simdi.isoweekday() in gunler and simdi.time() < ac:
        return f"{AKTARIM_MESAJI} (bugün {ac:%H:%M}'den itibaren)"

    for gun_ileri in range(1, 8):
        gun = simdi.date() + timedelta(days=gun_ileri)
        if gun.isoweekday() in gunler:
            return f"{AKTARIM_MESAJI} ({GUN_ADLARI[gun.weekday()]} {ac:%H:%M}'den itibaren)"
    return AKTARIM_MESAJI


def _turu_isle(conn, simdi: datetime) -> int:
    """Süresi dolmuş devirleri düşürür; gönderilen mesaj sayısını döner.

    Saat parametresi dışarıdan — test gece beklemek yerine saati sarar.
    """
    sinir = simdi - timedelta(hours=_otomatik_saat())
    gonderilen = 0
    for k in crm.devirdekiler(conn):
        if k["insan_devri_at"] > sinir:
            continue

        crm.devir_yaz(conn, k["id"], None)   # önce düşür: çift mesaj engellenir
        crm.gorusme_ekle(conn, k["id"], "giden", GERI_ALMA_MESAJI)
        try:
            gonder(son_kanal(conn, k["id"]), k["telefon"], GERI_ALMA_MESAJI)
        except Exception as e:   # ponytail: gönderim hatası düşürmeyi geri almaz
            log.warning("Geri alma mesajı gitmedi (%s): %s", k["telefon"], e)
        gonderilen += 1
    return gonderilen


async def nobetci(baglan_fn) -> None:
    """`hatirlatma.nobetci` deseni — doktor supervisor'ı çökerse yeniden doğurur."""
    while True:
        try:
            conn = baglan_fn()
            try:
                if (n := await asyncio.to_thread(_turu_isle, conn, datetime.now().astimezone())):
                    log.info("%d devir süresi dolmuş, asistan geri döndü", n)
            finally:
                conn.close()
        except Exception as e:
            log.error("Devir nöbetçisi hatası: %s", e)

        await asyncio.sleep(ARALIK_SN)
