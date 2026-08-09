"""Bağlantı sağlık nöbetçisi.

İki yanlış yönde de bozulabilir: sessiz kalıp kopmayı kaçırmak, ya da
her kontrolde mail atıp uyarıyı gürültüye çevirmek. İkisi de test edilir.
"""

from app.saglik import kontrol_sonucu_isle, uyari_telegram_gonder


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


def test_telegram_yapilandirilmamissa_gonderim_atlanir(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cagrildi = []
    monkeypatch.setattr("httpx.post", lambda *a, **k: cagrildi.append((a, k)))
    uyari_telegram_gonder("test")
    assert cagrildi == []


class _SahteYanit:
    def raise_for_status(self):
        pass


def test_telegram_yapilandirilmissa_dogru_istekle_gonderilir(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    cagrildi = []

    def sahte_post(*a, **k):
        cagrildi.append((a, k))
        return _SahteYanit()

    monkeypatch.setattr("httpx.post", sahte_post)
    uyari_telegram_gonder("bağlantı koptu")

    assert len(cagrildi) == 1
    (url,), kwargs = cagrildi[0]
    assert url == "https://api.telegram.org/botabc123/sendMessage"
    assert kwargs["json"] == {"chat_id": "999", "text": "bağlantı koptu"}
