#!/usr/bin/env python
"""`bilgi_tabani`'ndaki 'fiyatlar' kayıtlarını `hizmetler` tablosuna taşır.

Fiyatın tek kaynağı artık `hizmetler`. Bu script bir kez çalıştırılır, tekrar
çalıştırılabilir (taşınmış kayıt ikinci kez taşınmaz).

Sayı ayıklanamayan kayıt **silinmez**: kategorisi 'genel'e çekilir ve raporda
listelenir. Otomatik ayrıştırmanın yanlış fiyat üretmesindense insanın bakması
iyidir — burada yanlış sayı, hastaya yanlış fiyat söylenmesi demek.

    .venv/bin/python scripts/fiyat-goc.py            # rapor + uygula
    .venv/bin/python scripts/fiyat-goc.py --dene     # yalnız rapor
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import baglan, sema_kur              # noqa: E402
from app.hizmet import HizmetVar, hizmet_ekle    # noqa: E402
from app.kb import hermes_md_yaz                 # noqa: E402

# "Tek diş implant 25.000 TL" → 25000 · "1.500 TL" → 1500 · "850" → 850
SAYI = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?")


def fiyat_ayikla(metin: str) -> float | None:
    m = SAYI.search(metin)
    if not m:
        return None
    tam = m.group(1).replace(".", "")
    return float(f"{tam}.{m.group(2)}") if m.group(2) else float(tam)


def main() -> None:
    dene = "--dene" in sys.argv
    conn = baglan()
    sema_kur(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, baslik, icerik FROM bilgi_tabani WHERE kategori = 'fiyatlar' ORDER BY id"
        )
        kayitlar = cur.fetchall()

    if not kayitlar:
        print("Taşınacak fiyat kaydı yok.")
        conn.close()
        return

    tasinan, elde_kalan = [], []
    for bid, baslik, icerik in kayitlar:
        fiyat = fiyat_ayikla(icerik)
        if fiyat is None:
            elde_kalan.append((bid, baslik, icerik))
            continue

        if not dene:
            try:
                hizmet_ekle(conn, baslik, fiyat)
            except HizmetVar:
                pass          # ikinci koşu: hizmet zaten var, kaydı yine de kapat
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bilgi_tabani WHERE id = %s", (bid,))
            conn.commit()
        tasinan.append((baslik, fiyat, icerik))

    print(f"\nTaşınan ({len(tasinan)}):")
    for baslik, fiyat, icerik in tasinan:
        print(f"  {baslik}: {fiyat:g} TL   ← {icerik[:60]}")

    if elde_kalan:
        print(f"\n⚠ Fiyat ayıklanamadı ({len(elde_kalan)}) — 'genel'e alındı, elle girin:")
        for bid, baslik, icerik in elde_kalan:
            print(f"  #{bid} {baslik}: {icerik[:70]}")
        if not dene:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE bilgi_tabani SET kategori = 'genel' WHERE id = ANY(%s)",
                    ([b[0] for b in elde_kalan],),
                )
            conn.commit()

    if dene:
        print("\n(--dene: hiçbir şey değiştirilmedi)")
    else:
        hermes_md_yaz(conn, Path(__file__).resolve().parent.parent / ".hermes.md")
        print("\n.hermes.md yeniden üretildi.")
    conn.close()


if __name__ == "__main__":
    main()
