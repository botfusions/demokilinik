"""Doktora yeni randevu bildirimi — WhatsApp, sabit kural (panelden ayarlanmaz).

Kural sabit ve basit: yeni randevu açılınca, doktorun telefonu kayıtlıysa,
kendisine WhatsApp'tan haber gider. Kullanıcının kendi kararı: karmaşık bir
"bildirim tercihleri" ekranı YAGNI — standart akış (yeni randevu → bildir)
her klinik için yeterli. `.env`'de `DOKTORA_BILDIRIM=0` ile tamamen kapatılır.

**KVKK — veri minimizasyonu.** Mesaj metni doktorun telefonunun kilit
ekranında görünebilir. Bu yüzden hastanın serbest metin notu (`randevular.notlar`,
şikayet/semptom içerebilir) buraya hiç girmez — yalnız randevuyu yönetmek için
gerekli asgari bilgi: hizmet adı, tarih/saat, hastanın adı ve telefonu.
"""

import logging
import os

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)


def _acik_mi() -> bool:
    return os.environ.get("DOKTORA_BILDIRIM", "1") != "0"


def _randevu_ozeti(conn: psycopg.Connection, randevu_id: int) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.hizmet, r.baslangic, d.telefon AS doktor_telefon, d.ad AS doktor_ad,
                   k.ad AS hasta_ad, k.telefon AS hasta_telefon
              FROM randevular r
              JOIN kisiler k ON k.id = r.kisi_id
              LEFT JOIN doktorlar d ON d.id = r.doktor_id
             WHERE r.id = %s
            """,
            (randevu_id,),
        )
        return cur.fetchone()


def _mesaj_metni(r: dict) -> str:
    hasta = r["hasta_ad"] or "İsimsiz hasta"
    return (
        f"Yeni randevu: {r['hizmet']}\n"
        f"{r['baslangic']:%d.%m.%Y %H:%M}\n"
        f"Hasta: {hasta} ({r['hasta_telefon']})"
    )


def yeni_randevu_bildir(conn: psycopg.Connection, randevu_id: int, gonder_fn=None) -> bool:
    """Doktoruna WhatsApp'tan haber verir. Gönderilirse True.

    Doktor tanımsızsa, telefonu kayıtlı değilse ya da bildirim kapalıysa
    sessizce atlar — randevu akışını bu adım hiçbir zaman durdurmamalı,
    hata olursa yalnız loglanır."""
    if not _acik_mi():
        return False

    r = _randevu_ozeti(conn, randevu_id)
    if not r or not r.get("doktor_telefon"):
        return False

    if gonder_fn is None:
        from app.openwa import mesaj_gonder as gonder_fn

    try:
        gonder_fn(r["doktor_telefon"], _mesaj_metni(r))
    except Exception as e:
        log.warning("Doktora randevu bildirimi gönderilemedi (#%s): %s", randevu_id, e)
        return False
    return True
