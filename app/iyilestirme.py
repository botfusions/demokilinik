"""İyileştirme önerileri — LLM'siz, salt-okunur sinyal taraması.

Ajan hiçbir şeyi kendi kendine değiştirmez ve burada da otomatik bir şey
yazılmaz: bu modül yalnız `gorusmeler` tablosunu okur, iki bilinen sinyali
tarar ve personelin gözden geçirip bilgi tabanını (ya da SOUL.md'yi) elle
güncellemesi için bir öneri listesi çıkarır. Yeni veri toplanmıyor — konuşma
geçmişi zaten kayıtlı, yeni bir izleme/etiketleme mekanizması eklenmedi.

İki sinyal, ikisi de var olan veriden ücretsiz çıkar:

1. **Bilgi tabanı boşluğu** — ajanın SOUL.md'de sabit yazan "personelimiz size
   dönecek" cümlesini söylediği her an, bilgi tabanında olmayan bir soru
   gelmiş demektir. En net sinyal: string eşleşmesi, tahmin yok.
2. **Tekrarlanan soru** — aynı hasta art arda birbirine çok benzer iki soru
   sorduysa, önceki cevap muhtemelen tatmin etmemiştir. `app/kural.py`'deki
   aynı `SequenceMatcher` yaklaşımı burada da kullanılıyor.
"""

import os
from difflib import SequenceMatcher

import psycopg
from psycopg.rows import dict_row

BOSLUK_ISARETI = "personelimiz size dönecek"

BENZERLIK_ESIGI = float(os.environ.get("IYILESTIRME_BENZERLIK_ESIGI", "0.75"))


def kb_bosluklari(conn: psycopg.Connection, gun: int = 30) -> list[dict]:
    """Ajanın devretme cümlesini söylediği sorular, sıklığa göre gruplu.

    Aynı soru birden çok kez sorulmuş olabilir; en çok tekrar eden boşluk
    en üstte görünür ki personel önce onu ele alsın.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT g.kisi_id, g.olusturma,
                   (SELECT mesaj FROM gorusmeler onceki
                     WHERE onceki.kisi_id = g.kisi_id AND onceki.yon = 'gelen'
                       AND onceki.olusturma < g.olusturma
                     ORDER BY onceki.olusturma DESC LIMIT 1) AS soru
              FROM gorusmeler g
             WHERE g.yon = 'giden' AND g.kanal = 'whatsapp'
               AND g.mesaj ILIKE %s
               AND g.olusturma >= now() - make_interval(days => %s)
             ORDER BY g.olusturma DESC
            """,
            (f"%{BOSLUK_ISARETI}%", gun),
        )
        satirlar = cur.fetchall()

    gruplar: dict[str, dict] = {}
    for r in satirlar:
        soru = (r["soru"] or "").strip()
        if not soru:
            continue
        anahtar = soru.lower()
        grup = gruplar.setdefault(anahtar, {"soru": soru, "adet": 0, "son_tarih": r["olusturma"]})
        grup["adet"] += 1

    return sorted(gruplar.values(), key=lambda g: g["adet"], reverse=True)


def tekrarlanan_sorular(conn: psycopg.Connection, gun: int = 30) -> list[dict]:
    """Aynı hastanın art arda çok benzer iki soru sorması — dolaylı
    memnuniyetsizlik işareti. Yalnızca aynı hastanın ardışık gelen mesajları
    karşılaştırılır; ikisi arasında ajan cevabı olması şart değil."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT kisi_id, mesaj, olusturma
              FROM gorusmeler
             WHERE yon = 'gelen' AND kanal = 'whatsapp'
               AND olusturma >= now() - make_interval(days => %s)
             ORDER BY kisi_id, olusturma
            """,
            (gun,),
        )
        satirlar = cur.fetchall()

    sonuc = []
    onceki = None
    for r in satirlar:
        if (onceki is not None and onceki["kisi_id"] == r["kisi_id"]
                and SequenceMatcher(None, onceki["mesaj"].lower(), r["mesaj"].lower()).ratio() >= BENZERLIK_ESIGI):
            sonuc.append({
                "kisi_id": r["kisi_id"],
                "ilk_soru": onceki["mesaj"],
                "tekrar_soru": r["mesaj"],
                "tarih": r["olusturma"],
            })
        onceki = r

    return sonuc
