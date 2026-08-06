"""Instagram bilgilendirme kanalı.

Hiçbir test Composio'ya ya da LLM'e çıkmaz: ajan ve gönderim enjekte edilir.
Sınanan asıl şey kapsamın dar kalması — bu kanaldan randevu ve hatırlatma çıkmıyor.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from app import instagram
from app.crm import gorusme_ekle, gorusme_gecmisi, kisi_bul, kisi_upsert


# ── kanal sınırı: prompt ────────────────────────────────────

def test_instagram_promptu_randevu_yasagini_soyler():
    from app.ajan import prompt_hazirla

    p = prompt_hazirla([], "yarın 14:00'e randevu alabilir miyim", kanal="instagram")
    assert "Randevu KAYDI AÇMA" in p
    assert "Randevu araçlarını çağırma" in p
    assert "WhatsApp" in p


def test_whatsapp_promptu_degismedi():
    from app.ajan import prompt_hazirla

    p = prompt_hazirla([], "merhaba")
    assert "WhatsApp'tan gönderilecek" in p
    assert "Randevu KAYDI AÇMA" not in p     # WhatsApp'ta randevu açılabilir


def test_promptta_whatsapp_numarasi_gecer(monkeypatch):
    from app.ajan import prompt_hazirla

    monkeypatch.setenv("KLINIK_WHATSAPP_NUMARASI", "0555 111 22 33")
    assert "0555 111 22 33" in prompt_hazirla([], "randevu", kanal="instagram")


# ── tavan sızıntısı: Instagram WhatsApp'ın bütçesini yemez ──

def test_instagram_cevaplari_whatsapp_tavanina_sayilmaz(conn):
    from app.hatirlatma import giden_sayisi

    kid = kisi_upsert(conn, "905321112233")
    for i in range(30):
        gorusme_ekle(conn, kid, "giden", f"ig {i}", wa_message_id=f"ig:{i}", kanal="instagram")
    assert giden_sayisi(conn, 1) == 0

    gorusme_ekle(conn, kid, "giden", "wa", wa_message_id="wa:1")
    assert giden_sayisi(conn, 1) == 1


def test_tavan_dolu_degilken_hatirlatma_durmaz(conn):
    """Sızıntı olsaydı 30 Instagram mesajı saatlik tavanı (20) doldururdu."""
    from app.hatirlatma import tavan_kontrol

    kid = kisi_upsert(conn, "905321112233")
    for i in range(30):
        gorusme_ekle(conn, kid, "giden", f"ig {i}", wa_message_id=f"ig:{i}", kanal="instagram")

    tavan_kontrol(conn)      # fırlatmamalı


# ── tur: kayıt, tekrar teslimat, kanal damgası ──────────────

def _sahte_mesaj(mid="m1", metin="fiyat ne kadar", igsid="17841400000000001"):
    return {"igsid": igsid, "mesaj_id": f"ig:{mid}", "metin": metin, "ad": "hasta_x"}


@pytest.fixture
def sahte_ajan(monkeypatch):
    """LLM çağrısı yok: sabit cevap döner, çağrıları kaydeder."""
    cagrilar = []

    def cevap_uret(gecmis, mesaj, kanal="whatsapp"):
        cagrilar.append({"gecmis": gecmis, "mesaj": mesaj, "kanal": kanal})
        return "Merhaba, implant fiyatımız 15.000 TL'dir.", 0.0012

    monkeypatch.setattr("app.ajan.cevap_uret", cevap_uret)
    monkeypatch.setattr(instagram, "okundu_isaretle", lambda _: None)
    return cagrilar


def test_tur_mesaji_kaydeder_ve_cevaplar(conn, monkeypatch, sahte_ajan):
    monkeypatch.setattr(instagram, "yeni_mesajlar", lambda: [_sahte_mesaj()])
    gonderilen = []

    n = instagram.tur_calistir(conn, gonder_fn=lambda ig, m: gonderilen.append((ig, m)) or "x1")

    assert n == 1
    assert len(gonderilen) == 1
    assert sahte_ajan[0]["kanal"] == "instagram"

    kisi = kisi_bul(conn, "ig:17841400000000001")
    assert kisi is not None and kisi["ad"] == "hasta_x"

    gecmis = gorusme_gecmisi(conn, kisi["id"])
    assert [g["yon"] for g in gecmis] == ["gelen", "giden"]
    assert all(g["kanal"] == "instagram" for g in gecmis)
    assert gecmis[1]["maliyet_usd"] is not None


def test_ayni_mesaj_ikinci_turda_tekrar_cevaplanmaz(conn, monkeypatch, sahte_ajan):
    """Yoklama her turda aynı konuşmayı okur — kilit veritabanında."""
    monkeypatch.setattr(instagram, "yeni_mesajlar", lambda: [_sahte_mesaj()])

    assert instagram.tur_calistir(conn, gonder_fn=lambda ig, m: "x1") == 1
    assert instagram.tur_calistir(conn, gonder_fn=lambda ig, m: "x1") == 0
    assert len(sahte_ajan) == 1              # ikinci turda LLM hiç çağrılmadı


def test_tur_tavani_asilmaz(conn, monkeypatch, sahte_ajan):
    monkeypatch.setattr(instagram, "TUR_TAVANI", 2)
    monkeypatch.setattr(instagram, "yeni_mesajlar",
                        lambda: [_sahte_mesaj(mid=f"m{i}", igsid=f"1784140000000000{i}")
                                 for i in range(5)])

    assert instagram.tur_calistir(conn, gonder_fn=lambda ig, m: "x") == 2


def test_gonderim_hatasinda_cevap_kaydi_kalir(conn, monkeypatch, sahte_ajan):
    """Instagram'a ulaşılamasa da personel panelde ajanın ne dediğini görmeli."""
    monkeypatch.setattr(instagram, "yeni_mesajlar", lambda: [_sahte_mesaj()])

    def patla(ig, m):
        raise instagram.InstagramHatasi("ağ yok")

    assert instagram.tur_calistir(conn, gonder_fn=patla) == 0

    kisi = kisi_bul(conn, "ig:17841400000000001")
    assert [g["yon"] for g in gorusme_gecmisi(conn, kisi["id"])] == ["gelen", "giden"]


def test_bu_kanaldan_randevu_olusmaz(conn, monkeypatch, sahte_ajan):
    """Kapsam kilidi: tur_calistir randevu tablosuna dokunmaz."""
    monkeypatch.setattr(instagram, "yeni_mesajlar",
                        lambda: [_sahte_mesaj(metin="yarına randevu istiyorum")])
    instagram.tur_calistir(conn, gonder_fn=lambda ig, m: "x")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM randevular")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM hatirlatmalar")
        assert cur.fetchone()[0] == 0


# ── gelen/giden ayrımı ve zaman penceresi ───────────────────

def test_kendi_mesajimiza_cevap_yazmayiz():
    kendi = "17841409999999999"
    assert instagram._gelen_mi({"from": {"id": "111"}}, kendi) is True
    assert instagram._gelen_mi({"from": {"id": kendi}}, kendi) is False


def test_zaman_ayristirma():
    assert instagram._zaman({"created_time": "2026-08-06T10:00:00Z"}).year == 2026
    assert instagram._zaman({"timestamp": 1785945600}).year == 2026
    assert instagram._zaman({"timestamp": 1785945600000}).year == 2026   # milisaniye
    assert instagram._zaman({}) is None


def test_liste_ayikla():
    assert instagram._liste([1, 2]) == [1, 2]
    assert instagram._liste({"conversations": [1]}, "conversations") == [1]
    assert instagram._liste({"data": [1]}, "conversations", "data") == [1]
    assert instagram._liste({"beklenmedik": [7]}, "conversations") == [7]
    assert instagram._liste({"x": 1}, "conversations") == []


# ── canlılık nöbetçisi (gerçek boşluk) ──────────────────────

@pytest.fixture
def ig_acik(monkeypatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "test")
    monkeypatch.setenv("INSTAGRAM_KULLANICI", "test")


def test_kanal_kapaliyken_alarm_calmaz(conn, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_KULLANICI", raising=False)
    from app.saglik import _instagram_yoklama_kontrol

    assert _instagram_yoklama_kontrol(conn) == (True, None)


def test_yoklama_hic_calismadiysa_bozuk(conn, ig_acik):
    from app.saglik import _instagram_yoklama_kontrol

    basarili, hata = _instagram_yoklama_kontrol(conn)
    assert basarili is False and "hiç çalışmadı" in hata


def test_kalp_atisi_sonrasi_saglikli(conn, ig_acik):
    from app.saglik import _instagram_yoklama_kontrol

    instagram.kalp_atisi(conn, True)
    assert _instagram_yoklama_kontrol(conn) == (True, None)


def test_donmus_dongu_yakalanir(conn, ig_acik):
    """Composio bağlantısı ACTIVE kalıp döngü ölürse alarm bu satırdan çalar."""
    from app.saglik import _instagram_yoklama_kontrol

    instagram.kalp_atisi(conn, True)
    eski = datetime.now(timezone.utc) - timedelta(seconds=instagram.ARALIK_SN * 5 + 600)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE baglanti_saglik SET son_basarili = %s WHERE servis = 'instagram_yoklama'",
            (eski,),
        )
    conn.commit()

    basarili, hata = _instagram_yoklama_kontrol(conn)
    assert basarili is False and "tur atmadı" in hata


def test_yapilandirildi_mi(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("INSTAGRAM_KULLANICI", raising=False)
    assert instagram.yapilandirildi_mi() is False

    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    assert instagram.yapilandirildi_mi() is False      # kullanıcı da şart

    monkeypatch.setenv("INSTAGRAM_KULLANICI", "u")
    assert instagram.yapilandirildi_mi() is True


def test_anahtarsiz_cagri_patlar(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    with pytest.raises(instagram.InstagramHatasi):
        instagram._cagir("INSTAGRAM_LIST_ALL_CONVERSATIONS")


# ── toplu mesaj yasağı bu kanalda da geçerli ────────────────

def test_toplu_gonderim_fonksiyonu_yok():
    """`mesaj_gonder` tek alıcı alır. Liste alan bir sürümü olmamalı.

    WhatsApp tarafındaki aynı adlı testin kardeşi — kural kanal değiştirince
    gevşemiyor. Bkz. README § Toplu mesaj yasağı.
    """
    import inspect

    kaynak = inspect.getsource(instagram)
    assert "def toplu" not in kaynak
    imza = inspect.signature(instagram.mesaj_gonder)
    assert list(imza.parameters) == ["igsid", "metin"]
