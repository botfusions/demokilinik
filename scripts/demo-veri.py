#!/usr/bin/env python
"""Demo verisi — DemoDent Ağız ve Diş Sağlığı Kliniği.

Satış demosu için: 4 doktor, ilgili hizmet/fiyat listesi, birkaç genel bilgi
kaydı. Tekrar çalıştırılabilir — aynı isimde doktor/hizmet varsa atlanır,
ikinci kez eklenmez.

Doktorların hepsine **aynı telefon numarası** yazılır (DEMO_DOKTOR_TELEFON ya
da argüman) — demoda telefonu elinde tutan kişi (satışçı/doktor adayı) hangi
doktor seçilirse seçilsin bildirimi kendi telefonunda görür.

    .venv/bin/python scripts/demo-veri.py 905321112233
    # ya da .env'de DEMO_DOKTOR_TELEFON=905321112233 iken argümansız çalıştır
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from app.db import baglan, sema_kur          # noqa: E402
from app.crm import doktor_ekle, doktorlar_listele  # noqa: E402
from app.hizmet import HizmetVar, hizmet_ekle  # noqa: E402
from app.kb import bilgi_ekle, bilgiler_listele, hermes_md_yaz  # noqa: E402

DOKTORLAR = [
    ("Dr. Deniz Kaya", "İmplant ve cerrahi"),
    ("Dr. Selin Arslan", "Ortodonti"),
    ("Dr. Emre Yıldız", "Estetik diş hekimliği"),
    ("Dr. Zeynep Akın", "Çocuk diş hekimliği"),
]

HIZMETLER = [
    ("İmplant", 25000),
    ("Ortodonti (tel tedavisi)", 18000),
    ("Diş beyazlatma", 4500),
    ("Çocuk diş kontrolü", 900),
]

GENEL_BILGILER = [
    ("Klinik adı", "DemoDent Ağız ve Diş Sağlığı Kliniği", "genel"),
    ("Çalışma saatleri", "Hafta içi ve cumartesi 09:00-18:00 açığız, pazar kapalıyız.",
     "calisma_saatleri"),
    ("Adres", "Örnek Mahallesi Demo Caddesi No:1, İstanbul. Otopark mevcuttur.", "adres"),
]


def main() -> None:
    telefon = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DEMO_DOKTOR_TELEFON")

    conn = baglan()
    sema_kur(conn)

    mevcut_adlar = {d["ad"] for d in doktorlar_listele(conn)}
    for ad, uzmanlik in DOKTORLAR:
        if ad in mevcut_adlar:
            print(f"  atlandı (zaten var): {ad}")
            continue
        doktor_ekle(conn, ad, uzmanlik, telefon=telefon)
        print(f"  eklendi: {ad} ({uzmanlik})" + (f" — bildirim: {telefon}" if telefon else ""))

    for ad, fiyat in HIZMETLER:
        try:
            hizmet_ekle(conn, ad, fiyat)
            print(f"  hizmet eklendi: {ad} — {fiyat} TL")
        except HizmetVar:
            print(f"  atlandı (zaten var): {ad}")

    mevcut_basliklar = {b["baslik"] for b in bilgiler_listele(conn)}
    for baslik, icerik, kategori in GENEL_BILGILER:
        if baslik in mevcut_basliklar:
            print(f"  atlandı (zaten var): {baslik}")
            continue
        bilgi_ekle(conn, baslik, icerik, kategori)
        print(f"  bilgi eklendi: {baslik}")

    hermes_md_yaz(conn, Path(__file__).resolve().parent.parent / ".hermes.md")

    if not telefon:
        print("\n⚠ Doktor telefonu verilmedi — bildirim atlanacak.")
        print("  Kullanım: .venv/bin/python scripts/demo-veri.py 905321112233")
    print("\nDemo verisi hazır. Panelde Doktorlar/Bilgi Tabanı/Fiyatlar sayfalarından kontrol edin.")


if __name__ == "__main__":
    main()
