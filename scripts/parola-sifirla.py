#!/usr/bin/env python
"""Parola sıfırlama — vendor müdahalesi (sunucudan çalıştırılır).

Parola unutulduğunda tek çıkış yolu budur: self-servis sıfırlama yok
(e-posta altyapısı yok). Sessiz SQL yerine bu script kullanılır —
işlem izine "vendor sıfırladı" düşer, kim ne zaman sıfırladı bellidir.

Kilidi de açar (basarisiz_deneme + kilit_bitis), yoksa sıfırlanan
parola kilitli hesaba girseydi bile 15 dakika beklemek gerekirdi.

    docker exec <klinik-konteyneri> python scripts/parola-sifirla.py ayse yeniparola123
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import baglan                                   # noqa: E402
from app.kullanici import ParolaZayif, parola_degistir, islem_yaz  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    kullanici_adi, yeni_parola = sys.argv[1].strip().lower(), sys.argv[2]

    conn = baglan()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM kullanicilar WHERE kullanici_adi = %s", (kullanici_adi,)
            )
            satir = cur.fetchone()
        if not satir:
            print(f"'{kullanici_adi}' diye bir kullanıcı yok.")
            sys.exit(1)

        try:
            parola_degistir(conn, satir[0], yeni_parola)
        except ParolaZayif as e:
            print(f"Parola kabul edilmedi: {e}")
            sys.exit(1)

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE kullanicilar SET basarisiz_deneme = 0, kilit_bitis = NULL"
                " WHERE id = %s",
                (satir[0],),
            )
        conn.commit()
        islem_yaz(conn, {"kullanici_adi": "vendor"}, "parola sıfırladı (sunucudan)", kullanici_adi)
    finally:
        conn.close()

    print(f"'{kullanici_adi}' parolası sıfırlandı, kilit açıldı, iz kaydı düştü.")


if __name__ == "__main__":
    main()
