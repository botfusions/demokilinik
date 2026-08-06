"""Hizmet fiyatları ve kampanyalar.

**Kampanya bir gönderim aracı değildir.** Bu modülde alıcı listesi alan, mesaj
gönderen ya da WhatsApp'a dokunan tek bir fonksiyon yoktur ve olmayacaktır.
Kampanya yalnızca şunu belirler: hasta fiyat sorduğunda ajan hangi indirimi
söyleyecek. Duyuru göndermek toplu mesajdır ve mimari olarak yasaktır
(bkz. README § Toplu mesaj yasağı). `test_kampanya_gonderim_yapmaz` bunu her
koşuda denetler.

Fiyatın tek kaynağı `hizmetler` tablosudur. `bilgi_tabani`'nda "fiyatlar"
kategorisi yoktur — aynı hizmet iki yerde farklı fiyatla yazılsaydı ajan
hangisini söyleyeceğini bilemezdi.
"""

from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row


class HizmetVar(Exception):
    """Bu adla bir hizmet zaten kayıtlı."""


# ── hizmetler ───────────────────────────────────────────────

def hizmet_ekle(conn: psycopg.Connection, ad: str, fiyat: float) -> int:
    ad = ad.strip()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM hizmetler WHERE lower(ad) = lower(%s)", (ad,))
        if cur.fetchone():
            raise HizmetVar(f"'{ad}' zaten kayıtlı")
        cur.execute(
            "INSERT INTO hizmetler (ad, fiyat) VALUES (%s, %s) RETURNING id", (ad, fiyat)
        )
        hid = cur.fetchone()[0]
    conn.commit()
    return hid


def fiyat_guncelle(conn: psycopg.Connection, hizmet_id: int, yeni: float) -> bool:
    """Fiyatı değiştirir. Değişiklik olduysa True.

    Eski fiyat `onceki_fiyat`'a taşınır — panelin "% değişim" sütunu ve fiyatın
    ne zaman değiştiği bundan okunuyor. Aynı fiyat tekrar kaydedilirse
    `guncelleme` boş yere ilerlemesin diye dokunulmuyor.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE hizmetler
               SET onceki_fiyat = fiyat, fiyat = %s, guncelleme = now()
             WHERE id = %s AND fiyat <> %s
            """,
            (yeni, hizmet_id, yeni),
        )
        degisti = cur.rowcount > 0
    conn.commit()
    return degisti


def hizmet_durum_yaz(conn: psycopg.Connection, hizmet_id: int, aktif: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE hizmetler SET aktif = %s WHERE id = %s", (aktif, hizmet_id))
    conn.commit()


def hizmetler_listele(conn: psycopg.Connection, yalniz_aktif: bool = False) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM hizmetler WHERE (%s = false OR aktif) ORDER BY ad",
            (yalniz_aktif,),
        )
        return cur.fetchall()


# ── kampanyalar ─────────────────────────────────────────────

def kampanya_ekle(conn: psycopg.Connection, ad: str, indirim_yuzde: int,
                  hizmet_id: int | None = None, bitis: date | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kampanyalar (ad, indirim_yuzde, hizmet_id, bitis)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (ad.strip(), indirim_yuzde, hizmet_id, bitis),
        )
        kid = cur.fetchone()[0]
    conn.commit()
    return kid


def kampanya_durum_yaz(conn: psycopg.Connection, kampanya_id: int, aktif: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE kampanyalar SET aktif = %s WHERE id = %s", (aktif, kampanya_id))
    conn.commit()


def kampanya_sil(conn: psycopg.Connection, kampanya_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM kampanyalar WHERE id = %s", (kampanya_id,))
    conn.commit()


def kampanyalar_listele(conn: psycopg.Connection, yalniz_gecerli: bool = False) -> list[dict]:
    """`yalniz_gecerli`: aktif ve süresi dolmamış olanlar — ajana giden küme."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT k.*, h.ad AS hizmet_ad
              FROM kampanyalar k
              LEFT JOIN hizmetler h ON h.id = k.hizmet_id
             WHERE (%s = false OR (k.aktif AND (k.bitis IS NULL OR k.bitis >= current_date)))
             ORDER BY k.id
            """,
            (yalniz_gecerli,),
        )
        return cur.fetchall()


# ── indirim hesabı ──────────────────────────────────────────

def indirimli_fiyat(hizmet: dict, kampanyalar: list[dict],
                    bugun: date | None = None) -> tuple[Decimal, dict | None]:
    """(ödenecek fiyat, uygulanan kampanya | None).

    Kurallar, hepsi kararlı olsun diye açıkça sıralı:
      1. Pasif ya da süresi geçmiş kampanya uygulanmaz.
      2. Hizmete özel kampanya, "tüm hizmetler" kampanyasına baskındır —
         klinik bir hizmete özel indirim tanımladıysa kastı odur.
      3. Aynı düzeyde birden çok aday varsa indirimi yüksek olan seçilir;
         eşitlikte küçük id (önce tanımlanan). Ajan aynı soruya iki kez farklı
         fiyat söylememeli.
    """
    bugun = bugun or date.today()
    fiyat = Decimal(str(hizmet["fiyat"]))

    uygun = [
        k for k in kampanyalar
        if k.get("aktif", True)
        and (k.get("bitis") is None or k["bitis"] >= bugun)
        and k.get("hizmet_id") in (None, hizmet["id"])
    ]
    if not uygun:
        return fiyat, None

    ozel = [k for k in uygun if k.get("hizmet_id") is not None]
    aday = ozel or uygun
    kampanya = min(aday, key=lambda k: (-k["indirim_yuzde"], k["id"]))

    indirimli = (fiyat * (100 - kampanya["indirim_yuzde"]) / 100).quantize(Decimal("1"))
    return indirimli, kampanya


def fiyat_metni(hizmet: dict, kampanyalar: list[dict], bugun: date | None = None) -> str:
    """Ajanın hastaya söyleyeceği fiyat satırı. `.hermes.md`'ye bu yazılır."""
    liste = Decimal(str(hizmet["fiyat"]))
    indirimli, kampanya = indirimli_fiyat(hizmet, kampanyalar, bugun)

    if kampanya is None:
        return f"{_tl(liste)} TL."

    kuyruk = f" ({kampanya['bitis'].strftime('%d.%m.%Y')} tarihine kadar)" if kampanya["bitis"] else ""
    return (f"{_tl(liste)} TL. {kampanya['ad']} ile %{kampanya['indirim_yuzde']} "
            f"indirimli {_tl(indirimli)} TL{kuyruk}.")


def _tl(deger: Decimal) -> str:
    """1500 → '1.500', 1500.50 → '1.500,50' — Türkçe biçim."""
    tam = deger.quantize(Decimal("0.01"))
    tamsayi, _, kurus = f"{tam:.2f}".partition(".")
    basamakli = f"{int(tamsayi):,}".replace(",", ".")
    return basamakli if kurus == "00" else f"{basamakli},{kurus}"
