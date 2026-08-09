#!/usr/bin/env python
"""Demo hasta + randevu — satış demosu için takvimi doldurur.

8 uydurma hasta (gerçek olmayan 905000000XX telefonlar), aktif doktorlara
round-robin + çakışmasız dağıtılmış 8 randevu açar. **Her zaman içinde
bulunulan takvim haftasından başlayıp 6 haftaya** (her hafta Pazar kapalı)
yazar — müşteri takvimde ileri/geri gittiğinde her hafta dolu görsün. Her
hafta aynı şablon olduğu için tüm doktor renkleri 6 hafta boyunca görünür.
Tekrar çalıştırılabilir: önceki demo randevularını silip yeniden yazar.

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
    "Zeynep Çelik", "Mustafa Aydın", "Elif Öztürk", "Hüseyin Arslan",
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
    # Demo: tek hafta değil 6 hafta dolu olsun — müşteri takvimde ileri/geri
    # gittiğinde her hafta dolu görsün. Her hafta aynı şablon (her doktora 2),
    # böylece tüm renkler 6 hafta boyunca görünür kalır.
    HAFTALAR = 6

    eklenen = 0
    with conn.cursor() as cur:
        for hafta in range(HAFTALAR):
            for i, ad in enumerate(HASTALAR):
                telefon = f"{TELEFON_ONEK}{i + 1}"
                kid = kisi_upsert(conn, telefon, ad)

                gun = pazartesi + timedelta(days=hafta * 7 + (i % 6))  # her hafta Pzt-Cmt
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
                eklenen += 1
    conn.commit()

    son_hafta_basi = pazartesi + timedelta(days=(HAFTALAR - 1) * 7)
    print(f"\n{eklenen}/{len(HASTALAR) * HAFTALAR} randevu eklendi "
          f"({pazartesi:%d.%m}-{son_hafta_basi:%d.%m} başlangıçlı {HAFTALAR} hafta).")
    print("Dağılım:", hizmet_dagilimi(conn))


if __name__ == "__main__":
    main()
