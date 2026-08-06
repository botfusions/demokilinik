"""Randevu hatırlatmaları ve giden mesaj kilitleri.

Buraya kadar sistem yalnızca **gelen** mesaja cevap veriyordu; bu modül ilk kez
kendiliğinden mesaj gönderiyor. WhatsApp'ın hesap kapatma sebebi tam olarak budur,
o yüzden burası projenin en kısıtlı yeri:

  1. **Toplu gönderim diye bir şey yok.** Bu modülde alıcı listesi alan tek bir
     fonksiyon bile yoktur. Her mesaj tek bir randevuya bağlıdır; randevusu olmayan
     bir numaraya buradan mesaj gidemez.
  2. **Her randevu için en çok iki mesaj** (24 saat önce + 1 saat kala), ikisi de
     `UNIQUE(randevu_id, tur)` ile bir kereye kilitli.
  3. **Tavanlar**: saatlik ve günlük giden mesaj sınırı, gönderimler arası bekleme.
     Tavan dolduğunda gönderim durur, hatırlatma bir sonraki tura kalır.
  4. **Sessiz saat**: gece mesaj gitmez, sabaha ertelenir.

Bu sınırları gevşetmek isteyen biri önce şunu cevaplamalı: numara kapanırsa
kliniğin WhatsApp'la ulaştığı bütün hastalar ne olacak?
"""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("hatirlatma")

# Tur aralığı 5 dakika: "1 saat kala" hassasiyeti için yeterli, WhatsApp'ı
# yormayacak kadar seyrek.
ARALIK_SN = int(os.environ.get("HATIRLATMA_ARALIK_SN", "300"))

SAATLIK_TAVAN = int(os.environ.get("GIDEN_SAATLIK_TAVAN", "20"))
GUNLUK_TAVAN = int(os.environ.get("GIDEN_GUNLUK_TAVAN", "100"))
GONDERIM_ARASI_SN = float(os.environ.get("GIDEN_ARALIK_SN", "8"))
SESSIZ_BASLANGIC = time.fromisoformat(os.environ.get("SESSIZ_SAAT_BAS", "21:00"))
SESSIZ_BITIS = time.fromisoformat(os.environ.get("SESSIZ_SAAT_BIT", "09:00"))


class TavanAsildi(Exception):
    """Saatlik/günlük giden mesaj tavanı doldu."""


def sessiz_saatte_mi(an: datetime) -> bool:
    """Gece yarısını kapsayan aralık (21:00–09:00) doğru değerlendirilir."""
    t = an.time()
    if SESSIZ_BASLANGIC <= SESSIZ_BITIS:
        return SESSIZ_BASLANGIC <= t < SESSIZ_BITIS
    return t >= SESSIZ_BASLANGIC or t < SESSIZ_BITIS


def sessiz_saat_sonrasi(an: datetime) -> datetime:
    """Sessiz saate düşen bir zamanı ilk uygun ana taşır."""
    if not sessiz_saatte_mi(an):
        return an
    hedef = an.replace(hour=SESSIZ_BITIS.hour, minute=SESSIZ_BITIS.minute,
                       second=0, microsecond=0)
    return hedef if hedef > an else hedef + timedelta(days=1)


# ── planlama ────────────────────────────────────────────────

def hatirlatma_planla(conn: psycopg.Connection, randevu_id: int) -> list[str]:
    """Randevu için 24 saat önce ve 1 saat kala hatırlatmalarını planlar.

    Geçmişte kalan bir zaman planlanmaz — randevuya 3 saat kala açılan bir kayıt
    için "24 saat önce" mesajı atılmaz, sadece 1 saatlik olan planlanır.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT baslangic, durum FROM randevular WHERE id = %s", (randevu_id,))
        r = cur.fetchone()

    if not r or r["durum"] == "iptal":
        return []

    simdi = datetime.now(tz=r["baslangic"].tzinfo)
    planlanan = []

    for tur, fark in (("24s", timedelta(hours=24)), ("1s", timedelta(hours=1))):
        an = r["baslangic"] - fark
        if an <= simdi:
            continue
        an = sessiz_saat_sonrasi(an)
        if an >= r["baslangic"]:      # erteleme randevuyu geçtiyse anlamsız
            continue

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hatirlatmalar (randevu_id, tur, planlanan)
                VALUES (%s, %s, %s) ON CONFLICT (randevu_id, tur) DO NOTHING
                """,
                (randevu_id, tur, an),
            )
            if cur.rowcount:
                planlanan.append(tur)

    conn.commit()
    return planlanan


def hatirlatmalari_iptal_et(conn: psycopg.Connection, randevu_id: int) -> None:
    """Randevu iptal edilince gönderilmemiş hatırlatmalar düşer.

    Bu olmadan iptal edilmiş randevu için hastaya hatırlatma gider — hem rahatsız
    edici hem de kliniğin karışık göründüğü bir hata.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM hatirlatmalar WHERE randevu_id = %s AND gonderildi IS NULL",
            (randevu_id,),
        )
    conn.commit()


# ── tavanlar ────────────────────────────────────────────────

def giden_sayisi(conn: psycopg.Connection, saat: int = 1) -> int:
    """Son N saatte WhatsApp'tan giden mesajlar — hatırlatma + ajan cevapları.

    Yalnız `kanal='whatsapp'` sayılır. Tavanın amacı WhatsApp numarasının
    kapanmasını önlemek; Instagram cevapları o numaradan çıkmıyor. Kanal filtresi
    olmasaydı yoğun bir Instagram günü saatlik tavanı doldurur ve gerçek randevu
    hatırlatmalarını durdururdu.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM gorusmeler
             WHERE yon = 'giden' AND kanal = 'whatsapp'
               AND olusturma >= now() - make_interval(hours => %s)
            """,
            (saat,),
        )
        return cur.fetchone()[0]


def tavan_kontrol(conn: psycopg.Connection) -> None:
    """Tavan dolduysa TavanAsildi fırlatır. Gönderim öncesi çağrılır."""
    if (s := giden_sayisi(conn, 1)) >= SAATLIK_TAVAN:
        raise TavanAsildi(f"saatlik tavan doldu ({s}/{SAATLIK_TAVAN})")
    if (g := giden_sayisi(conn, 24)) >= GUNLUK_TAVAN:
        raise TavanAsildi(f"günlük tavan doldu ({g}/{GUNLUK_TAVAN})")


# ── mesaj metni ─────────────────────────────────────────────

def metin_uret(r: dict, tur: str) -> str:
    ad = (r.get("ad") or "").split()[0] if r.get("ad") else None
    hitap = f"Merhaba {ad}," if ad else "Merhaba,"
    hekim = f" {r['doktor_ad']} ile" if r.get("doktor_ad") else ""
    saat = r["baslangic"].strftime("%H:%M")

    if tur == "24s":
        tarih = r["baslangic"].strftime("%d.%m.%Y")
        return (
            f"{hitap} {tarih} saat {saat}'de{hekim} {r['hizmet']} randevunuz var. "
            f"Geleceğinizi teyit etmek için 'evet', iptal için 'iptal' yazabilirsiniz."
        )

    return (
        f"{hitap} saat {saat} randevunuza 1 saat kaldı{hekim}. Bekliyoruz. "
        f"Gelemeyecekseniz 'iptal' yazmanız yeterli."
    )


# ── gönderim ────────────────────────────────────────────────

def bekleyenler(conn: psycopg.Connection, limit: int = 10) -> list[dict]:
    """Zamanı gelmiş, henüz gönderilmemiş hatırlatmalar.

    `limit` bilinçli olarak küçük: bir turda az sayıda mesaj çıkar, kalanlar
    sonraki tura kalır. Bir anda yüzlerce mesaj çıkmasının yolu yok.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT h.id, h.tur, h.randevu_id,
                   r.baslangic, r.hizmet, r.durum,
                   k.telefon, k.ad, d.ad AS doktor_ad
              FROM hatirlatmalar h
              JOIN randevular r ON r.id = h.randevu_id
              JOIN kisiler k ON k.id = r.kisi_id
              LEFT JOIN doktorlar d ON d.id = r.doktor_id
             WHERE h.gonderildi IS NULL
               AND h.planlanan <= now()
               AND r.durum <> 'iptal'
               AND r.baslangic > now()
             ORDER BY h.planlanan
             LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def _isaretle(conn: psycopg.Connection, hatirlatma_id: int,
              wa_id: str | None = None, hata: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE hatirlatmalar SET gonderildi = now(), wa_message_id = %s, hata = %s WHERE id = %s",
            (wa_id, hata, hatirlatma_id),
        )
    conn.commit()


def tur_calistir(conn: psycopg.Connection, gonder_fn=None, bekle_fn=None) -> int:
    """Bir tur: zamanı gelen hatırlatmaları tek tek gönderir. Gönderilen sayısını döner.

    Tavan dolduğunda tur biter, kalanlar sonraki tura kalır — kuyruğu boşaltmak
    için tavan aşılmaz.
    """
    from app.crm import gorusme_ekle

    if gonder_fn is None:
        from app.openwa import mesaj_gonder as gonder_fn
    if bekle_fn is None:
        import time as _t
        bekle_fn = _t.sleep

    gonderilen = 0
    for h in bekleyenler(conn):
        if sessiz_saatte_mi(datetime.now()):
            log.info("Sessiz saat — hatırlatma ertelendi")
            break

        try:
            tavan_kontrol(conn)
        except TavanAsildi as e:
            log.warning("Hatırlatma durdu: %s", e)
            break

        metin = metin_uret(h, h["tur"])
        try:
            # TEK alıcı. Bu satırın liste alan bir sürümü bilinçli olarak yoktur.
            wa_id = gonder_fn(h["telefon"], metin)
        except Exception as e:
            log.error("Hatırlatma gönderilemedi (%s): %s", h["telefon"], e)
            _isaretle(conn, h["id"], hata=str(e)[:400])
            continue

        with conn.cursor() as cur:
            cur.execute("SELECT kisi_id FROM randevular WHERE id = %s", (h["randevu_id"],))
            kisi_id = cur.fetchone()[0]
        gorusme_ekle(conn, kisi_id, "giden", metin, wa_message_id=wa_id)

        _isaretle(conn, h["id"], wa_id=wa_id)
        gonderilen += 1

        if GONDERIM_ARASI_SN:
            bekle_fn(GONDERIM_ARASI_SN)   # arka arkaya yığın gönderim görüntüsü vermez

    return gonderilen


async def nobetci(baglan_fn) -> None:
    """FastAPI'nin kendi döngüsünde çalışır."""
    while True:
        try:
            conn = baglan_fn()
            try:
                if (n := await asyncio.to_thread(tur_calistir, conn)):
                    log.info("%d hatırlatma gönderildi", n)
            finally:
                conn.close()
        except Exception as e:
            log.error("Hatırlatma nöbetçisi hatası: %s", e)

        await asyncio.sleep(ARALIK_SN)
