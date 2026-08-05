"""Bağlantı sağlık nöbetçisi.

İki yanlış yönde de bozulabilir: sessiz kalıp kopmayı kaçırmak, ya da
her kontrolde mail atıp uyarıyı gürültüye çevirmek. İkisi de test edilir.
"""

from app.saglik import kontrol_sonucu_isle


def test_saglikli_serviste_aksiyon_yok(conn):
    assert kontrol_sonucu_isle(conn, "composio", True) is None


def test_tek_hata_uyari_tetiklemez(conn):
    """Geçici bir ağ hatası kliniği telefona sarılmaya mecbur bırakmamalı."""
    assert kontrol_sonucu_isle(conn, "composio", False, "timeout") is None


def test_ikinci_ardisik_hata_uyari_verir(conn):
    kontrol_sonucu_isle(conn, "composio", False, "timeout")
    assert kontrol_sonucu_isle(conn, "composio", False, "timeout") == "uyari"


def test_uyari_tekrarlanmaz(conn):
    kontrol_sonucu_isle(conn, "composio", False, "timeout")
    kontrol_sonucu_isle(conn, "composio", False, "timeout")

    for _ in range(5):
        assert kontrol_sonucu_isle(conn, "composio", False, "timeout") is None


def test_araya_giren_basari_sayaci_sifirlar(conn):
    kontrol_sonucu_isle(conn, "composio", False, "timeout")
    kontrol_sonucu_isle(conn, "composio", True)
    # Sayaç sıfırlandığı için bu yine "birinci" hata
    assert kontrol_sonucu_isle(conn, "composio", False, "timeout") is None


def test_duzelme_bir_kez_bildirilir(conn):
    kontrol_sonucu_isle(conn, "composio", False, "timeout")
    kontrol_sonucu_isle(conn, "composio", False, "timeout")  # uyarı gitti

    assert kontrol_sonucu_isle(conn, "composio", True) == "duzeldi"
    assert kontrol_sonucu_isle(conn, "composio", True) is None


def test_uyarilmadan_duzelme_bildirilmez(conn):
    """Uyarı gönderilmemişse 'geri geldi' maili de gitmemeli."""
    kontrol_sonucu_isle(conn, "composio", False, "timeout")
    assert kontrol_sonucu_isle(conn, "composio", True) is None


def test_servisler_birbirinden_bagimsiz(conn):
    kontrol_sonucu_isle(conn, "composio", False, "timeout")
    kontrol_sonucu_isle(conn, "composio", False, "timeout")  # composio uyarıda

    assert kontrol_sonucu_isle(conn, "whatsapp", False, "disconnected") is None


def test_durum_ve_hata_kaydediliyor(conn):
    kontrol_sonucu_isle(conn, "whatsapp", False, "disconnected")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT durum, hata, ardisik_hata FROM baglanti_saglik WHERE servis = 'whatsapp'"
        )
        durum, hata, ardisik = cur.fetchone()

    assert durum != "saglikli"
    assert hata == "disconnected"
    assert ardisik == 1


def test_son_basarili_zamani_korunur(conn):
    """Servis çökünce, en son ne zaman çalıştığı bilgisi kaybolmamalı."""
    kontrol_sonucu_isle(conn, "whatsapp", True)
    with conn.cursor() as cur:
        cur.execute("SELECT son_basarili FROM baglanti_saglik WHERE servis = 'whatsapp'")
        onceki = cur.fetchone()[0]

    kontrol_sonucu_isle(conn, "whatsapp", False, "disconnected")
    with conn.cursor() as cur:
        cur.execute("SELECT son_basarili FROM baglanti_saglik WHERE servis = 'whatsapp'")
        assert cur.fetchone()[0] == onceki
