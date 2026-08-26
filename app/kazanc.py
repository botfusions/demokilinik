"""Kayıp ve kurtarma raporu (PRD 26-08-2026 İK-6).

`rapor.py` haftalık Telegram token raporudur; burada ise kliniğe
gösterilecek, fiyatı gerekçelendiren ay bazlı tablo var. Üç blok:
kayıp, hatırlatma sonrası iptal, ajanın getirdiği.

Fiyatın tek kaynağı `hizmetler` tablosu; `randevular.hizmet` serbest
metin olduğu için eşleşme katlanarak (İ/ı→i) yapılır. Eşleşmeyen kayıt
"fiyatı bilinmiyor" grubunda sayılır, TL toplamına girmez, ortalama
fiyatla doldurulmaz.

Bu raporda korelasyon iddiası YOKTUR: "hatırlatma sayesinde X randevu
kurtarıldı" gibi bir cümle yazılamaz (PRD'nin açık yasağı).
"""

from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from app.crm import calisma_saati_icinde
from app.hafif import _turkce_kucult

DURUMLAR = ("bekliyor", "onayli", "geldi", "gelmedi", "iptal")


def ay_baslangic(yil: int, ay: int) -> datetime:
    if not (1 <= ay <= 12):
        raise ValueError(f"Ay 1-12 arası olmalı: {ay}")
    return datetime(yil, ay, 1)


def fiyat_haritasi(conn: psycopg.Connection) -> dict[str, object]:
    """Katlanmış hizmet adı → fiyat. Pasif hizmetler de dahil — geçmiş
    randevunun hizmeti pasifleşmiş olabilir, fiyatı yine bilinir."""
    with conn.cursor() as cur:
        cur.execute("SELECT ad, fiyat FROM hizmetler")
        return {_turkce_kucult(ad.strip()): fiyat for ad, fiyat in cur.fetchall()}


def erken_bosalan_sayisi(conn: psycopg.Connection, bas: datetime, son: datetime) -> int:
    """Hatırlatması GÖNDERİLMİŞ, ardından iptal edilmiş; iptali personel
    yapmamış (işlem kaydı yok) randevu sayısı.

    Bu bir vekil ölçümdür ve raporda olduğu gibi adlandırılır: "hatırlatma
    gönderildikten sonra hastanın iptal ettiği". İptal zamanı bir kolonda
    saklanmadığı için "hatırlatmaya cevaben iptal" ile "kendiliğinden iptal"
    ayrıştırılamaz; panel iptalleri `islem_kaydi`'ndan elenir. Kesin sayı
    istenirse `randevular.iptal_zamani` + iptal kaynağı kolonları gerekir.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM randevular r
             WHERE r.baslangic >= %s AND r.baslangic < %s
               AND r.durum = 'iptal'
               AND EXISTS (SELECT 1 FROM hatirlatmalar h
                            WHERE h.randevu_id = r.id AND h.gonderildi IS NOT NULL)
               AND NOT EXISTS (SELECT 1 FROM islem_kaydi i
                                WHERE i.eylem = 'randevu iptal etti'
                                  AND i.detay = '#' || r.id)
            """,
            (bas, son),
        )
        return cur.fetchone()[0]


def ay_ozeti(conn: psycopg.Connection, yil: int, ay: int) -> dict:
    """Ayın kayıp/kurtarma özeti. Boş ayda tüm sayılar sıfırdır."""
    bas = ay_baslangic(yil, ay)
    son = ay_baslangic(yil + 1, 1) if ay == 12 else ay_baslangic(yil, ay + 1)

    sayilar = {d: 0 for d in DURUMLAR}
    fiyatlar = fiyat_haritasi(conn)
    gelmedi_tl = Decimal("0")
    gelmedi_fiyatsiz = 0
    ajan = 0
    ajan_mesai_disi = 0

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT hizmet, durum, kaynak, baslangic, bitis, olusturma
              FROM randevular
             WHERE baslangic >= %s AND baslangic < %s
            """,
            (bas, son),
        )
        for r in cur.fetchall():
            sayilar[r["durum"]] = sayilar.get(r["durum"], 0) + 1

            fiyat = fiyatlar.get(_turkce_kucult(r["hizmet"].strip()))
            if r["durum"] == "gelmedi":
                if fiyat is None:
                    gelmedi_fiyatsiz += 1
                else:
                    gelmedi_tl += fiyat

            if r["kaynak"] == "ajan":
                ajan += 1
                # "Mesai dışı açılmış": randevu, açıldığı anda çalışma
                # penceresinin dışındaysa — hasta gece yazıp almıştır.
                if not calisma_saati_icinde(r["olusturma"], r["olusturma"]):
                    ajan_mesai_disi += 1

    gelen, gelmeyen = sayilar["geldi"], sayilar["gelmedi"]
    oran = gelmeyen / (gelen + gelmeyen) if (gelen + gelmeyen) else None

    return {
        "yil": yil,
        "ay": ay,
        "sayilar": sayilar,
        "toplam": sum(sayilar.values()),
        "gelmeme_orani": oran,
        "gelmeme_orani_metni": f"%{oran * 100:.0f}" if oran is not None else "—",
        "gelmedi_tl": gelmedi_tl,
        "gelmedi_fiyatsiz": gelmedi_fiyatsiz,
        "erken_bosalan": erken_bosalan_sayisi(conn, bas, son),
        "ajan": ajan,
        "ajan_mesai_disi": ajan_mesai_disi,
    }
