"""Bilgi tabanı → .hermes.md üretimi.

Ajanın hastaya söyleyebileceği her şeyin kaynağı bu dosya. Pasif bir kaydın
dosyaya sızması, klinikten kaldırılmış bir fiyatın hastaya söylenmesi demektir.
"""

from app.kb import bilgi_ekle, bilgi_pasiflestir, hermes_md_uret, hermes_md_yaz


def test_aktif_kayit_dosyaya_girer(conn):
    bilgi_ekle(conn, "İmplant", "Tek diş implant 25.000 TL", "fiyatlar")
    icerik = hermes_md_uret(conn)
    assert "İmplant" in icerik
    assert "25.000 TL" in icerik


def test_pasif_kayit_dosyaya_girmez(conn):
    bid = bilgi_ekle(conn, "Eski kampanya", "Yarı fiyat", "fiyatlar")
    bilgi_pasiflestir(conn, bid)

    icerik = hermes_md_uret(conn)
    assert "Eski kampanya" not in icerik
    assert "Yarı fiyat" not in icerik


def test_kategoriler_baslik_olur(conn):
    bilgi_ekle(conn, "İmplant", "25.000 TL", "fiyatlar")
    bilgi_ekle(conn, "Hafta içi", "09:00-18:00", "calisma_saatleri")

    icerik = hermes_md_uret(conn)
    assert "fiyatlar" in icerik.lower()
    assert "calisma_saatleri" in icerik.lower() or "çalışma" in icerik.lower()


def test_ayni_kategori_tek_baslik_altinda(conn):
    bilgi_ekle(conn, "İmplant", "25.000 TL", "fiyatlar")
    bilgi_ekle(conn, "Dolgu", "2.000 TL", "fiyatlar")

    icerik = hermes_md_uret(conn)
    assert icerik.lower().count("## fiyatlar") == 1


def test_bos_tabanda_da_gecerli_markdown(conn):
    icerik = hermes_md_uret(conn)
    assert isinstance(icerik, str)
    assert len(icerik) > 0  # ajan boş dosyayla değil, "bilgi yok" notuyla karşılaşmalı


def test_yazma_idempotent(conn, tmp_path):
    bilgi_ekle(conn, "İmplant", "25.000 TL", "fiyatlar")
    yol = tmp_path / ".hermes.md"

    hermes_md_yaz(conn, yol)
    ilk = yol.read_text(encoding="utf-8")
    ilk_mtime = yol.stat().st_mtime_ns

    hermes_md_yaz(conn, yol)
    assert yol.read_text(encoding="utf-8") == ilk
    # İçerik değişmediyse dosyaya dokunulmamalı — gereksiz yazma diskte gürültü
    assert yol.stat().st_mtime_ns == ilk_mtime


def test_degisiklik_dosyaya_yansir(conn, tmp_path):
    bilgi_ekle(conn, "İmplant", "25.000 TL", "fiyatlar")
    yol = tmp_path / ".hermes.md"
    hermes_md_yaz(conn, yol)

    bilgi_ekle(conn, "Dolgu", "2.000 TL", "fiyatlar")
    hermes_md_yaz(conn, yol)

    assert "Dolgu" in yol.read_text(encoding="utf-8")
