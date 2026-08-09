"""CRM temel işlemleri: kişi tekilliği, görüşme bağlama, idempotans."""

import pytest

from app.crm import gorusme_ekle, gorusme_gecmisi, kisi_upsert, kullanim_ozeti


def test_ayni_telefon_yeni_kisi_acmaz(conn):
    a = kisi_upsert(conn, "905321112233", "Ayşe")
    b = kisi_upsert(conn, "905321112233")
    assert a == b

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kisiler WHERE telefon = '905321112233'")
        assert cur.fetchone()[0] == 1


def test_upsert_son_temasi_gunceller(conn):
    kid = kisi_upsert(conn, "905321112233")
    with conn.cursor() as cur:
        cur.execute("SELECT son_temas FROM kisiler WHERE id = %s", (kid,))
        ilk = cur.fetchone()[0]

    kisi_upsert(conn, "905321112233")
    with conn.cursor() as cur:
        cur.execute("SELECT son_temas FROM kisiler WHERE id = %s", (kid,))
        assert cur.fetchone()[0] >= ilk


def test_upsert_var_olan_adi_silmez(conn):
    """İkinci mesajda ad gelmezse, kayıtlı ad korunmalı."""
    kid = kisi_upsert(conn, "905321112233", "Ayşe")
    kisi_upsert(conn, "905321112233", None)
    with conn.cursor() as cur:
        cur.execute("SELECT ad FROM kisiler WHERE id = %s", (kid,))
        assert cur.fetchone()[0] == "Ayşe"


def test_gorusme_kisiye_baglanir(conn, kisi_id):
    gid = gorusme_ekle(conn, kisi_id, "gelen", "Merhaba")
    assert gid is not None

    with conn.cursor() as cur:
        cur.execute("SELECT kisi_id, yon, mesaj FROM gorusmeler WHERE id = %s", (gid,))
        assert cur.fetchone() == (kisi_id, "gelen", "Merhaba")


def test_ayni_wa_message_id_ikinci_kez_kaydedilmez(conn, kisi_id):
    """OpenWA teslimatı at-least-once — aynı mesaj iki kez gelebilir."""
    ilk = gorusme_ekle(conn, kisi_id, "gelen", "Merhaba", wa_message_id="wamid.ABC")
    ikinci = gorusme_ekle(conn, kisi_id, "gelen", "Merhaba", wa_message_id="wamid.ABC")

    assert ilk is not None
    assert ikinci is None

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gorusmeler WHERE wa_message_id = 'wamid.ABC'")
        assert cur.fetchone()[0] == 1


def test_wa_message_id_olmayan_kayitlar_cakismaz(conn, kisi_id):
    """Giden mesajların bir kısmının wa_message_id'si olmayabilir; NULL'lar tekil değildir."""
    assert gorusme_ekle(conn, kisi_id, "giden", "Bir") is not None
    assert gorusme_ekle(conn, kisi_id, "giden", "İki") is not None


def test_olmayan_kisiye_gorusme_eklenmez(conn):
    with pytest.raises(Exception):
        gorusme_ekle(conn, 999999, "gelen", "Hayalet")


def test_gecmis_eskiden_yeniye_sirali_ve_sinirli(conn, kisi_id):
    for i in range(15):
        gorusme_ekle(conn, kisi_id, "gelen", f"mesaj-{i}")

    gecmis = gorusme_gecmisi(conn, kisi_id, limit=10)
    assert len(gecmis) == 10
    # Son 10 mesaj, eskiden yeniye
    assert gecmis[0]["mesaj"] == "mesaj-5"
    assert gecmis[-1]["mesaj"] == "mesaj-14"


def test_kullanim_ozeti_mesaj_ve_token_toplar(conn, kisi_id):
    gorusme_ekle(conn, kisi_id, "gelen", "Soru bir")
    gorusme_ekle(conn, kisi_id, "giden", "Cevap bir", giris_token=100, cikis_token=50)
    gorusme_ekle(conn, kisi_id, "gelen", "Soru iki")
    gorusme_ekle(conn, kisi_id, "giden", "Cevap iki", giris_token=200, cikis_token=80)

    ozet = kullanim_ozeti(conn, 3600)
    assert ozet == {"mesaj_adedi": 2, "giris_token": 300, "cikis_token": 130}


def test_kullanim_ozeti_pencere_disini_saymaz(conn, kisi_id):
    gid = gorusme_ekle(conn, kisi_id, "gelen", "Eski soru")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gorusmeler SET olusturma = now() - interval '2 hours' WHERE id = %s",
            (gid,),
        )
    conn.commit()

    ozet = kullanim_ozeti(conn, 3600)  # 1 saatlik pencere
    assert ozet == {"mesaj_adedi": 0, "giris_token": 0, "cikis_token": 0}
