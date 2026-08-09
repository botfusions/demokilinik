#!/usr/bin/env python
"""Demo hasta + randevu — satış demosu için takvimi doldurur.

7 uydurma hasta (gerçek olmayan 905000000XX telefonlar), aktif doktorlara
round-robin + çakışmasız dağıtılmış 7 randevu açar. **Her zaman içinde
bulunulan takvim haftasına** (Pazartesi-Cumartesi) yazar — panel varsayılan
olarak o haftayı açtığı için müşteriye demo gösterirken "Sonraki" tıklamaya
gerek kalmaz. Tekrar çalıştırılabilir: önceki demo randevularını silip aynı
haftaya yeniden yazar (hafta değiştiyse eski veri "geçen hafta" gibi kalıp
kafa karıştırmasın diye).

Bu yüzden `app.crm.randevu_olustur`'un "geçmiş tarihe randevu açılamaz"
korumasını kasıtlı olarak atlıyor (doğrudan SQL) — haftanın geçmişte kalan
günleri (bugün Pazartesi'den sonraysa) de demo'da görünsün istiyoruz, bunlar
gerçek rezervasyon değil kozmetik veri.

    .venv/bin/python scripts/demo-hasta-randevu.py
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crm import doktorlar_listele, hizmet_dagilimi, kisi_upsert  # noqa: E402
from app.db import baglan  # noqa: E402
from app.hizmet import hizmetler_listele  # noqa: E402

HASTALAR = [
    "Ayşe Demir", "Mehmet Kaya", "Fatma Şahin", "Ali Yılmaz",
    "Zeynep Çelik", "Mustafa Aydın", "Elif Öztürk",
]

# gerçek numaralarla çakışmayacak, açıkça "demo" deseni
TELEFON_ONEK = "90500000000"

SAATLER = [10, 11, 13, 14, 15, 16, 17]


def _hafta_basi(gun: date) -> date:
    """O günün içinde bulunduğu haftanın pazartesisi (app/main.py:hafta_basi ile aynı)."""
    return gun - timedelta(days=gun.isoweekday() - 1)


def main() -> None:
    conn = baglan()

    doktorlar = doktorlar_listele(conn, yalniz_aktif=True)
    if not doktorlar:
        print("Aktif doktor yok — önce scripts/demo-veri.py çalıştırın.")
        return

    hizmetler = [h["ad"] for h in hizmetler_listele(conn, yalniz_aktif=True)]
    if not hizmetler:
        print("Hizmet listesi boş — önce scripts/demo-veri.py çalıştırın.")
        return

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM randevular WHERE kisi_id IN "
            "(SELECT id FROM kisiler WHERE telefon LIKE %s)",
            (f"{TELEFON_ONEK}%",),
        )
    conn.commit()

    bugun = date.today()
    pazartesi = _hafta_basi(bugun)
    gunler = [pazartesi + timedelta(days=i) for i in range(6)]  # Pzt-Cmt, Pazar kapalı

    eklenen = 0
    with conn.cursor() as cur:
        for i, ad in enumerate(HASTALAR):
            telefon = f"{TELEFON_ONEK}{i + 1}"
            kid = kisi_upsert(conn, telefon, ad)

            gun = gunler[i % len(gunler)]
            saat = SAATLER[i % len(SAATLER)]
            baslangic = datetime.combine(gun, datetime.min.time()).replace(hour=saat)
            bitis = baslangic + timedelta(minutes=30)

            doktor = doktorlar[i % len(doktorlar)]
            hizmet = hizmetler[i % len(hizmetler)]

            cur.execute(
                """
                SELECT 1 FROM randevular
                 WHERE durum <> 'iptal' AND doktor_id = %s
                   AND baslangic < %s AND bitis > %s
                """,
                (doktor["id"], bitis, baslangic),
            )
            if cur.fetchone():
                print(f"  atlandı ({ad}): {doktor['ad']} o saatte dolu")
                continue

            cur.execute(
                """
                INSERT INTO randevular (kisi_id, hizmet, baslangic, bitis, doktor_id)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (kid, hizmet, baslangic, bitis, doktor["id"]),
            )
            rid = cur.fetchone()[0]
            print(f"  eklendi: #{rid} {ad} — {doktor['ad']} — {hizmet} — {baslangic:%d.%m %H:%M}")
            eklenen += 1
    conn.commit()

    print(f"\n{eklenen}/{len(HASTALAR)} randevu eklendi ({pazartesi:%d.%m}-{gunler[-1]:%d.%m} haftası).")
    print("Dağılım:", hizmet_dagilimi(conn))


if __name__ == "__main__":
    main()
