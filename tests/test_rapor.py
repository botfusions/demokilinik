"""Haftalık kullanım raporu — mesaj formatı. DB/network yok, saf fonksiyon."""

from app.rapor import _mesaj


def test_mesaj_sayilari_dogru_gosterir():
    metin = _mesaj({"mesaj_adedi": 12, "giris_token": 4000, "cikis_token": 1500})
    assert "Mesaj: 12" in metin
    assert "Token: 5500" in metin
    assert "giriş 4000, çıkış 1500" in metin


def test_mesaj_fiyat_icermez():
    """$ hiç görünmemeli — müşteriye asla gitmeyecek bilgi."""
    metin = _mesaj({"mesaj_adedi": 0, "giris_token": 0, "cikis_token": 0})
    assert "$" not in metin
    assert "usd" not in metin.lower()
