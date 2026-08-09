#!/usr/bin/env python
"""Demo hasta + randevu — satış demosu için takvimi doldurur.

7 uydurma hasta (gerçek olmayan 905000000XX telefonlar), aktif doktorlara
round-robin + çakışmasız dağıtılmış 7 randevu açar. Tek seferlik, tekrar
çalıştırılabilir: aynı telefonlu hasta zaten randevulanmışsa atlar.

    .venv/bin/python scripts/demo-hasta-randevu.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crm import (  # noqa: E402
    CalismaSaatiDisi, GecmisTarih, RandevuCakismasi,
    doktorlar_listele, hizmet_dagilimi, kisi_upsert, randevu_olustur,
)
from app.db import baglan  # noqa: E402
from app.hizmet import hizmetler_listele  # noqa: E402

HASTALAR = [
    "Ayşe Demir", "Mehmet Kaya", "Fatma Şahin", "Ali Yılmaz",
    "Zeynep Çelik", "Mustafa Aydın", "Elif Öztürk",
]

# gerçek numaralarla çakışmayacak, açıkça "demo" deseni
TELEFON_ONEK = "90500000000"

SAATLER = [10, 11, 13, 14, 15, 16, 17]


def _sonraki_is_gunu(baslangic: datetime, ofset: int) -> datetime:
    """Bugünden ofset kadar sonraki iş günü (Pazar hariç — CALISMA_GUNLERI)."""
    gun = baslangic
    eklenen = 0
    while eklenen < ofset or gun.isoweekday() == 7:
        gun += timedelta(days=1)
        if gun.isoweekday() != 7:
            eklenen += 1
    return gun


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

    simdi = datetime.now()
    eklenen = 0
    for i, ad in enumerate(HASTALAR):
        telefon = f"{TELEFON_ONEK}{i + 1}"
        kid = kisi_upsert(conn, telefon, ad)

        gun = _sonraki_is_gunu(simdi, i % 5 + 1)
        saat = SAATLER[i % len(SAATLER)]
        baslangic = gun.replace(hour=saat, minute=0, second=0, microsecond=0)
        bitis = baslangic + timedelta(minutes=30)

        doktor = doktorlar[i % len(doktorlar)]
        hizmet = hizmetler[i % len(hizmetler)]

        try:
            rid = randevu_olustur(conn, kid, hizmet, baslangic, bitis,
                                   doktor_id=doktor["id"])
            print(f"  eklendi: #{rid} {ad} — {doktor['ad']} — {hizmet} — "
                  f"{baslangic:%d.%m %H:%M}")
            eklenen += 1
        except (RandevuCakismasi, GecmisTarih, CalismaSaatiDisi) as e:
            print(f"  atlandı ({ad}): {e}")

    print(f"\n{eklenen}/{len(HASTALAR)} randevu eklendi.")
    print("Dağılım:", hizmet_dagilimi(conn))


if __name__ == "__main__":
    main()
