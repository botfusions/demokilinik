"""Bilgi tabanı → .hermes.md

Ajanın hastaya söyleyebileceği her şeyin kaynağı bu dosya. Pasifleştirilmiş bir
kaydın dosyaya sızması, klinikten kaldırılmış bir fiyatın hastaya söylenmesi demektir.
"""

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

BASLIK = """# Klinik Bilgileri

Aşağıdaki bilgiler klinik personeli tarafından girilmiştir ve tek doğru kaynaktır.
Burada olmayan hiçbir bilgiyi uydurma; bilmiyorsan personele yönlendir.
"""

KATEGORI_ADLARI = {
    "fiyatlar": "Fiyatlar",
    "hizmetler": "Hizmetler",
    "calisma_saatleri": "Çalışma Saatleri",
    "adres": "Adres ve Ulaşım",
    "sss": "Sık Sorulan Sorular",
    "genel": "Genel",
}


def bilgi_ekle(conn: psycopg.Connection, baslik: str, icerik: str, kategori: str = "genel") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bilgi_tabani (baslik, icerik, kategori) VALUES (%s, %s, %s) RETURNING id",
            (baslik, icerik, kategori),
        )
        bid = cur.fetchone()[0]
    conn.commit()
    return bid


def bilgi_guncelle(conn: psycopg.Connection, bilgi_id: int, baslik: str, icerik: str,
                   kategori: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE bilgi_tabani
               SET baslik = %s, icerik = %s, kategori = %s, guncelleme = now()
             WHERE id = %s
            """,
            (baslik, icerik, kategori, bilgi_id),
        )
    conn.commit()


def bilgi_pasiflestir(conn: psycopg.Connection, bilgi_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bilgi_tabani SET aktif = false, guncelleme = now() WHERE id = %s", (bilgi_id,)
        )
    conn.commit()


def bilgi_aktiflestir(conn: psycopg.Connection, bilgi_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bilgi_tabani SET aktif = true, guncelleme = now() WHERE id = %s", (bilgi_id,)
        )
    conn.commit()


def bilgiler_listele(conn: psycopg.Connection, yalniz_aktif: bool = False) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT * FROM bilgi_tabani
             WHERE (%s = false OR aktif)
             ORDER BY kategori, id
            """,
            (yalniz_aktif,),
        )
        return cur.fetchall()


def hermes_md_uret(conn: psycopg.Connection) -> str:
    """Aktif kayıtlardan, kategoriye göre gruplu markdown üretir."""
    parcalar = [BASLIK]
    son_kategori = None

    for satir in bilgiler_listele(conn, yalniz_aktif=True):
        if satir["kategori"] != son_kategori:
            son_kategori = satir["kategori"]
            parcalar.append(f"\n## {son_kategori}  <!-- {KATEGORI_ADLARI.get(son_kategori, son_kategori)} -->\n")
        parcalar.append(f"### {satir['baslik']}\n{satir['icerik']}\n")

    if son_kategori is None:
        parcalar.append(
            "\n_Bilgi tabanı henüz boş. Hiçbir soruya cevap uydurma; "
            "hastayı klinik personeline yönlendir._\n"
        )

    return "\n".join(parcalar)


def hermes_md_yaz(conn: psycopg.Connection, yol) -> None:
    """Dosyayı yazar. İçerik değişmediyse dosyaya dokunmaz."""
    yol = Path(yol)
    yeni = hermes_md_uret(conn)
    if yol.exists() and yol.read_text(encoding="utf-8") == yeni:
        return
    yol.write_text(yeni, encoding="utf-8")
