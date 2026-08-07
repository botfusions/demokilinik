"""İyileştirme önerileri — salt-okunur sinyal taraması, LLM'siz.

Ajan hiçbir şeyi otomatik değiştirmez; bu testler yalnız sinyallerin doğru
çıkarıldığını doğruluyor.
"""

from app import iyilestirme
from app.crm import gorusme_ekle, kisi_upsert


def test_bosluk_isareti_yakalanir(conn, kisi_id):
    gorusme_ekle(conn, kisi_id, "gelen", "Otopark ücreti ne kadar?")
    gorusme_ekle(conn, kisi_id, "giden",
                "Bu konuda kesin bilgi vermek için personelimiz size dönecek, en kısa sürede arayacağız.")

    bosluklar = iyilestirme.kb_bosluklari(conn)

    assert len(bosluklar) == 1
    assert bosluklar[0]["soru"] == "Otopark ücreti ne kadar?"
    assert bosluklar[0]["adet"] == 1


def test_normal_cevap_bosluk_sayilmaz(conn, kisi_id):
    gorusme_ekle(conn, kisi_id, "gelen", "Çalışma saatleriniz nedir?")
    gorusme_ekle(conn, kisi_id, "giden", "Hafta içi 09:00-18:00 açığız.")

    assert iyilestirme.kb_bosluklari(conn) == []


def test_tekrar_eden_bosluk_gruplanir(conn):
    kid1 = kisi_upsert(conn, "905321110001")
    kid2 = kisi_upsert(conn, "905321110002")
    for kid in (kid1, kid2):
        gorusme_ekle(conn, kid, "gelen", "Kredi kartına taksit var mı?")
        gorusme_ekle(conn, kid, "giden",
                    "Bu konuda kesin bilgi vermek için personelimiz size dönecek, en kısa sürede arayacağız.")

    bosluklar = iyilestirme.kb_bosluklari(conn)
    assert len(bosluklar) == 1
    assert bosluklar[0]["adet"] == 2


def test_yalniz_whatsapp_kanali_sayilir(conn, kisi_id):
    gorusme_ekle(conn, kisi_id, "gelen", "Kampanya var mı?", kanal="instagram")
    gorusme_ekle(conn, kisi_id, "giden",
                "Bu konuda kesin bilgi vermek için personelimiz size dönecek, en kısa sürede arayacağız.",
                kanal="instagram")

    assert iyilestirme.kb_bosluklari(conn) == []


def test_tekrarlanan_soru_yakalanir(conn, kisi_id):
    gorusme_ekle(conn, kisi_id, "gelen", "İmplant ne kadar sürer")
    gorusme_ekle(conn, kisi_id, "giden", "Genelde 2-3 seans sürer.")
    gorusme_ekle(conn, kisi_id, "gelen", "İmplant ne kadar sürer?")

    tekrarlar = iyilestirme.tekrarlanan_sorular(conn)

    assert len(tekrarlar) == 1
    assert tekrarlar[0]["kisi_id"] == kisi_id


def test_alakasiz_ardisik_sorular_yakalanmaz(conn, kisi_id):
    gorusme_ekle(conn, kisi_id, "gelen", "Otopark var mı?")
    gorusme_ekle(conn, kisi_id, "giden", "Evet, ücretsiz.")
    gorusme_ekle(conn, kisi_id, "gelen", "Adresiniz nedir?")

    assert iyilestirme.tekrarlanan_sorular(conn) == []


def test_farkli_hastalarin_sorulari_karsilastirilmaz(conn):
    kid1 = kisi_upsert(conn, "905321110003")
    kid2 = kisi_upsert(conn, "905321110004")
    gorusme_ekle(conn, kid1, "gelen", "İmplant ne kadar sürer?")
    gorusme_ekle(conn, kid2, "gelen", "İmplant ne kadar sürer?")

    assert iyilestirme.tekrarlanan_sorular(conn) == []
