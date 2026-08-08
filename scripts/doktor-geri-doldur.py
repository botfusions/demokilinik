#!/usr/bin/env python
"""Doktoru NULL olan randevulara hekim atar — `doktor_id` sütunu sonradan
eklendiği için (db.py migration) eski randevular hekimsiz kaldı.

Dağıtım kuralı `en_bos_doktor` DEĞİL: o tekil yeni randevu içindir, batch
içindeki atamaları saymaz ve alfabetik ilk hekimi (Ayla) aç gözlü seçer —
sonuç tek renk olur. Burada **dengeli dağıtım** isteriz: her atamada o ana
kadardır en az yüklü ve o saatte çakışmayan hekimi seçer (batch içi sayaçla).
Böylece takvim renkli görünür ve aynı saate iki randevu aynı hekime gitmez.

Tek seferlik, tekrar çalıştırılabilir (doktoru olmayana dokunur, olana değil).

    .venv/bin/python scripts/doktor-geri-doldur.py --dene   # raporla
    .venv/bin/python scripts/doktor-geri-doldur.py          # uygula
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import baglan            # noqa: E402
from psycopg.rows import dict_row    # noqa: E402


def _cakisir(bas, bit, araliklar) -> bool:
    return any(b < bit and bas < e for b, e in araliklar)


def main() -> int:
    dene = "--dene" in sys.argv
    conn = baglan()
    with conn.cursor(row_factory=dict_row) as cur:
        aktif = cur.execute(
            "SELECT id, ad FROM doktorlar WHERE aktif ORDER BY ad"
        ).fetchall()
        if not aktif:
            print("Aktif doktor yok — atama yapılamaz.")
            return 1

        boslar = cur.execute(
            """SELECT id, baslangic, bitis
                 FROM randevular WHERE doktor_id IS NULL ORDER BY baslangic"""
        ).fetchall()
        print(f"Aktif hekim: {len(aktif)} · hekimsiz randevu: {len(boslar)}")

        # ponytail: batch-içi dengeli dağıtım. sayaç + dolu aralıklar canlı tutulur.
        yuk = {d["id"]: 0 for d in aktif}
        dolu = {d["id"]: [] for d in aktif}
        plan = []
        for r in boslar:
            bas, bit = r["baslangic"], r["bitis"]
            aday = [d for d in aktif if not _cakisir(bas, bit, dolu[d["id"]])]
            if not aday:  # tüm hekimler o saatte dolu — yine de birini ver (renk için)
                aday = aktif
            sec = min(aday, key=lambda d: (yuk[d["id"]], d["ad"]))
            plan.append((r["id"], sec["id"], sec["ad"]))
            yuk[sec["id"]] += 1
            dolu[sec["id"]].append((bas, bit))

        for rid, _did, dad in plan:
            print(f"  #{rid:<4} -> {dad}")

        print("\nDağılım:")
        for d in aktif:
            print(f"  {d['ad']}: {yuk[d['id']]}")

        if not dene:
            for rid, did, _dad in plan:
                cur.execute(
                    "UPDATE randevular SET doktor_id = %s WHERE id = %s "
                    "AND doktor_id IS NULL",
                    (did, rid),
                )
            conn.commit()
            print(f"\n{len(plan)} randevuya hekim atandı.")
        else:
            print(f"\n(--dene) {len(plan)} randevu atanacaktı.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
