"""İnsan devri (PRD 16-08-2026-PRD-insan-devri.md, T1-T8).

Devir açıkken asistan susar ama mesaj kaydı sürer; personel panelden yazar;
2 saat cevapsız kalırsa devir otomatik düşer (K1). Testler sahte gönderimle
çalışır — Telegram ve OpenWA gerçek uçlara asla gitmez.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.ajan as ajan
import app.devri as devri
import app.hatirlatma as hatirlatma
import app.openwa as openwa
import app.main as main
from app.crm import devir_yaz, devirdekiler, gorusme_gecmisi, kisi_bul, kisi_upsert
from app.kural import devir_istedi_mi

GIZLI = "test-gizli"
TELEFON = "905321112233"


def _imzala(govde: bytes) -> str:
    return "sha256=" + hmac.new(GIZLI.encode(), govde, hashlib.sha256).hexdigest()


def _govde(mesaj: str, wamid: str):
    return json.dumps({
        "event": "message.received",
        "data": {"id": wamid, "from": f"{TELEFON}@c.us", "body": mesaj,
                 "isGroup": False, "contact": {"name": "Test Hasta"}},
    }).encode()


@pytest.fixture
def istemci(conn, monkeypatch):
    """Webhook istemcisi: ajan ve OpenWA gönderimi sahte.

    Giden kilitleri (İK-2) de açılır: bildirim testleri gece de koşabilsin,
    tavan dolu olmasın. Kilitlerin kendisi ayrı testlerde kapatılır.
    """
    gonderilenler = []
    monkeypatch.setattr(
        ajan, "cevap_uret",
        lambda gecmis, mesaj, **kw: ("Test yanıtı", {}),
    )
    monkeypatch.setattr(openwa, "mesaj_gonder",
                        lambda tel, metin: gonderilenler.append((tel, metin)) or "wamid.OUT")
    monkeypatch.setattr(hatirlatma, "sessiz_saatte_mi", lambda an: False)
    monkeypatch.setattr(hatirlatma, "SAATLIK_TAVAN", 9999)
    monkeypatch.setattr(hatirlatma, "GUNLUK_TAVAN", 9999)

    c = TestClient(main.app)
    c.gonderilenler = gonderilenler
    return c


@pytest.fixture
def panel(conn):
    """Personel girişi yapmış panel istemcisi (test_kullanici deseni)."""
    from app.kullanici import kullanici_ekle

    kullanici_ekle(conn, "admin", "yoneticiparola", "admin")
    c = TestClient(main.app, follow_redirects=False)
    c.post("/giris", data={"kullanici_adi": "admin", "parola": "yoneticiparola"})
    return c


def _gonder(c, mesaj: str, wamid: str):
    g = _govde(mesaj, wamid)
    return c.post("/webhook/whatsapp", content=g,
                  headers={"X-OpenWA-Signature": _imzala(g)})


def _kisi(conn):
    return kisi_bul(conn, TELEFON)


# ── T8 önce: tetikleyici kelime hassasiyeti ────────────────

def test_t8_kelime_listesi():
    assert devir_istedi_mi("Yetkiliyle görüşmek istiyorum")
    assert devir_istedi_mi("biriyle görüşebilir miyim")
    assert devir_istedi_mi("canlı destek var mı")
    assert devir_istedi_mi("I want to talk to a person")
    # T8: kısa kelime tek başına tetiklememeli
    assert not devir_istedi_mi("insan gibi konuşuyorsun")
    assert not devir_istedi_mi("çok hızlı cevap veriyorsunuz")
    assert not devir_istedi_mi("merhaba")


def test_t8_sohbet_icinde_yanlis_tetiklenmez(istemci, conn):
    _gonder(istemci, "insan gibi konuşuyorsun çok başarılı", "wamid.D1")
    assert _kisi(conn)["insan_devri_at"] is None
    assert istemci.gonderilenler[-1][1] == "Test yanıtı", "ajan normal cevap vermiş olmalı"


# ── devrin başlaması ve ajanın susması ─────────────────────

def test_t1_devir_talebi_devri_acar_ve_sabit_cevap_gider(istemci, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D2")

    assert _kisi(conn)["insan_devri_at"] is not None
    mesaj = istemci.gonderilenler[-1][1]
    assert "aktarıyorum" in mesaj
    assert mesaj != "Test yanıtı", "ajan değil, sabit aktarım mesajı gitmeli"


def test_t2_devir_acikken_mesaj_kaydedilir_ajan_cagrilmaz(istemci, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D3")
    onceki = len(istemci.gonderilenler)

    _gonder(istemci, "hâlâ orada mısınız?", "wamid.D4")

    kayitlar = [g["mesaj"] for g in gorusme_gecmisi(conn, _kisi(conn)["id"], limit=50)]
    assert "hâlâ orada mısınız?" in kayitlar, "gelen mesaj mutlaka kaydedilir"
    assert len(istemci.gonderilenler) == onceki, "devir açıkken hiçbir cevap gitmez"


# ── panel tarafı ────────────────────────────────────────────

def test_t3_panelden_mesaj_giden_olarak_kaydedilir_ve_gider(istemci, panel, conn):
    _gonder(istemci, "merhaba", "wamid.D5")
    kid = _kisi(conn)["id"]

    r = panel.post(f"/hastalar/{kid}/mesaj", data={"metin": "Hocam hemen bakıyoruz"})
    assert r.status_code == 303

    kayitlar = gorusme_gecmisi(conn, kid, limit=5)
    assert kayitlar[-1]["yon"] == "giden"
    assert kayitlar[-1]["mesaj"] == "Hocam hemen bakıyoruz"
    assert (TELEFON, "Hocam hemen bakıyoruz") in istemci.gonderilenler


def test_panel_mesaji_devri_acar_ve_sayaci_tazeler(istemci, panel, conn):
    """K1: personel yazınca sayaç son mesajdan işler — kolon now() olur."""
    _gonder(istemci, "merhaba", "wamid.D6")
    kid = _kisi(conn)["id"]
    assert _kisi(conn)["insan_devri_at"] is None

    panel.post(f"/hastalar/{kid}/mesaj", data={"metin": "merhaba, ben sekreter"})
    assert _kisi(conn)["insan_devri_at"] is not None


def test_t4_gonderim_hatasi_kaydi_dusurmez(istemci, panel, conn, monkeypatch):
    _gonder(istemci, "merhaba", "wamid.D7")
    kid = _kisi(conn)["id"]

    def _patla(tel, metin):
        raise RuntimeError("OpenWA yok")
    monkeypatch.setattr(openwa, "mesaj_gonder", _patla)

    r = panel.post(f"/hastalar/{kid}/mesaj", data={"metin": "kayıt kalsın"})
    assert r.status_code == 303

    kayitlar = gorusme_gecmisi(conn, kid, limit=5)
    assert kayitlar[-1]["mesaj"] == "kayıt kalsın"


def test_t5_devri_bitirince_ajan_geri_doner(istemci, panel, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D8")
    kid = _kisi(conn)["id"]

    r = panel.post(f"/hastalar/{kid}/devri-bitir")
    assert r.status_code == 303
    assert _kisi(conn)["insan_devri_at"] is None

    _gonder(istemci, "tekrar merhaba", "wamid.D9")
    assert istemci.gonderilenler[-1][1] == "Test yanıtı"


def test_personel_devral_dugmesi(istemci, panel, conn):
    """PRD 4.2: personel hasta istemese de araya girebilmeli."""
    _gonder(istemci, "randevu almak istiyorum", "wamid.D10")
    kid = _kisi(conn)["id"]

    panel.post(f"/hastalar/{kid}/devri-baslat")
    assert _kisi(conn)["insan_devri_at"] is not None


# ── K1: otomatik geri alma ─────────────────────────────────

def test_t6_iki_saat_cevapsiz_devir_duser(istemci, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D11")
    kid = _kisi(conn)["id"]

    # Süre dolmamış: dokunulmaz
    devri._turu_isle(conn, datetime.now().astimezone() + timedelta(minutes=30))
    assert _kisi(conn)["insan_devri_at"] is not None

    # 3 saat sonra: devir düşer, bilgi mesajı kaydedilir ve gider
    devri._turu_isle(conn, datetime.now().astimezone() + timedelta(hours=3))
    assert _kisi(conn)["insan_devri_at"] is None
    kayitlar = gorusme_gecmisi(conn, kid, limit=5)
    assert kayitlar[-1]["yon"] == "giden"
    assert "ulaşamadık" in kayitlar[-1]["mesaj"]
    assert "ulaşamadık" in istemci.gonderilenler[-1][1]


def test_t6_personel_mesaji_sayaci_yeniden_baslatir(istemci, panel, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D12")
    kid = _kisi(conn)["id"]
    panel.post(f"/hastalar/{kid}/mesaj", data={"metin": "bakıyoruz"})

    # Personel yazdıktan ~1 saat sonra: hâlâ düşmemeli
    devri._turu_isle(conn, datetime.now().astimezone() + timedelta(minutes=55))
    assert _kisi(conn)["insan_devri_at"] is not None


# ── K2: mesai dışı aktarım mesajı ───────────────────────────

def test_k2_mesai_disinda_donus_saati_yazilir():
    # 16.08.2026 pazar; conftest penceresi 09:00-18:00, pzt-cmt
    mesaj = devri.aktarim_mesaji(datetime(2026, 8, 16, 12, 0))
    assert "pazartesi 09:00" in mesaj


def test_k2_mesai_saat_oncesi_bugun_yazar():
    # 17.08.2026 pazartesi 07:30 — açılış öncesi
    mesaj = devri.aktarim_mesaji(datetime(2026, 8, 17, 7, 30))
    assert "bugün 09:00" in mesaj


# ── K3: hatırlatmalar etkilenmez ───────────────────────────

def test_t7_devir_acikken_hatirlatma_yine_gider(istemci, conn):
    from app import hatirlatma

    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D13")
    kid = _kisi(conn)["id"]

    # Geçmişe planlanmış bir hatırlatma satırı elle (K3: akış devirden bağımsız)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO randevular (kisi_id, hizmet, baslangic, bitis) VALUES (%s, %s, %s, %s) RETURNING id",
            (kid, "Kontrol", datetime.now() + timedelta(hours=1), datetime.now() + timedelta(hours=2)),
        )
        rid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s, '24s', now() - interval '1 minute')",
            (rid,),
        )
    conn.commit()

    giden = []
    n = hatirlatma.tur_calistir(conn, gonder_fn=lambda *a, **kw: giden.append(a))
    assert n == 1
    assert giden, "devir açıkken hatırlatma yine gönderilmeli"


# ── devirdekiler / panel görünürlüğü ───────────────────────

def test_panel_hasta_sayfasinda_rozet_ve_ana_sayfada_sayac(istemci, panel, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D14")
    kid = _kisi(conn)["id"]

    assert len(devirdekiler(conn)) == 1

    r = panel.get(f"/hastalar/{kid}")
    assert "İnsan devrede" in r.text
    r = panel.get("/")
    assert "İnsan devri bekliyor: 1 hasta" in r.text
    assert f'href="/hastalar/{kid}"' in r.text, "devirdeki hasta kartta linkli"


# ── T9: personel WhatsApp bildirimi (numaralar panelden, kullanicilar tablosu) ──

def _personel_ekle(conn, ad: str, telefon: str | None):
    from app.kullanici import kullanici_ekle
    return kullanici_ekle(conn, ad, "duzenlibirparola", "personel", None, telefon)


def test_t9_devir_baslayinca_personel_numaralarina_bildirim_gider(istemci, conn):
    _personel_ekle(conn, "sekreter", "+905551112233")
    _personel_ekle(conn, "yonetici", "+905334445566")
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D15")

    bildirimler = [g for g in istemci.gonderilenler if "İnsan müdahalesi gerekli" in g[1]]
    assert {tel for tel, _ in bildirimler} == {"+905551112233", "+905334445566"}
    metin = bildirimler[0][1]
    assert TELEFON in metin and "Neden: " + devri.NEDEN_HASTA in metin


def test_t9_bir_numara_hataliysa_digeri_yine_alir_devir_dusmez(istemci, conn, monkeypatch):
    _personel_ekle(conn, "patlayan", "+905550000000")
    _personel_ekle(conn, "saglam", "+905551111111")
    giden = []

    def _kismi_patla(tel, metin):
        if tel == "+905550000000":
            raise RuntimeError("numara yok")
        giden.append((tel, metin))
        return "wamid.OUT"
    monkeypatch.setattr(openwa, "mesaj_gonder", _kismi_patla)

    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D16")
    assert _kisi(conn)["insan_devri_at"] is not None, "bildirim hatası devri düşürmez"
    assert ("+905551111111", giden[-1][1]) and "İnsan müdahalesi gerekli" in giden[-1][1], \
        "sağlam numara bildirimi aldı"


def test_t9_numarasiz_kullaniciya_bildirim_yok(istemci, conn):
    _personel_ekle(conn, "telefonsuz", None)
    onceki = len(istemci.gonderilenler)
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D17")
    assert _kisi(conn)["insan_devri_at"] is not None
    assert len(istemci.gonderilenler) == onceki + 1, "yalnız hastaya aktarım mesajı gitti"


def test_t9_pasif_kullaniciya_bildirim_gitmez(istemci, conn):
    from app.kullanici import kullanici_durum_yaz
    kid = _personel_ekle(conn, "izinli", "+905550009999")
    kullanici_durum_yaz(conn, kid, False)

    onceki = len(istemci.gonderilenler)
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D23")
    assert len(istemci.gonderilenler) == onceki + 1, "pasifleştirilen personel bildirim almaz"


def test_t9_panelden_numara_guncellenir(istemci, panel, conn):
    """Yönetici Kullanıcılar sayfasından bildirim numarası yazar/siler."""
    from app.kullanici import kullanicilar_listele, kullanici_telefon_yaz
    _personel_ekle(conn, "sekreter", None)
    kid = kullanicilar_listele(conn)[0]["id"]

    panel.post(f"/kullanicilar/{kid}/telefon", data={"telefon": "+905557778888"})
    assert kullanicilar_listele(conn)[0]["telefon"] == "+905557778888"

    panel.post(f"/kullanicilar/{kid}/telefon", data={"telefon": ""})
    assert kullanicilar_listele(conn)[0]["telefon"] is None


# ── T10: devir nedeni kaydı ─────────────────────────────────

def test_t10_hasta_tetiginde_neden_kaydedilir_sistem_satiri_duser(istemci, panel, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D18")
    kid = _kisi(conn)["id"]

    assert _kisi(conn)["devir_nedeni"] == devri.NEDEN_HASTA
    kayitlar = [g["mesaj"] for g in gorusme_gecmisi(conn, kid, limit=10, sistem_dahil=True)]
    assert f"devir açıldı: {devri.NEDEN_HASTA}" in kayitlar

    panel.post(f"/hastalar/{kid}/devri-bitir")
    assert _kisi(conn)["devir_nedeni"] is None, "devir bitince neden temizlenir"


def test_t10_panel_devral_dugmesi_nedenini_yazar(istemci, panel, conn):
    _gonder(istemci, "randevu almak istiyorum", "wamid.D19")
    kid = _kisi(conn)["id"]

    panel.post(f"/hastalar/{kid}/devri-baslat")
    assert _kisi(conn)["devir_nedeni"] == devri.NEDEN_PANEL

    r = panel.get(f"/hastalar/{kid}")
    assert f"İnsan devrede — {devri.NEDEN_PANEL}" in r.text


def test_t10_sistem_satiri_ajan_gecmisine_girmez(istemci, panel, conn, monkeypatch):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D20")
    panel.post(f"/hastalar/{_kisi(conn)['id']}/devri-bitir")

    yakalanan = {}
    def _yakala(gecmis, mesaj, **kw):
        yakalanan["gecmis"] = gecmis
        return "Test yanıtı", {}
    monkeypatch.setattr(ajan, "cevap_uret", _yakala)

    _gonder(istemci, "insan gibi konuşuyorsun çok başarılı", "wamid.D22")
    assert yakalanan["gecmis"], "ajan çağrılmış olmalı"
    assert all(g["yon"] != "sistem" for g in yakalanan["gecmis"]), \
        "devir kaydı ajan prompt'una girmemeli"


def test_t11_rapor_devir_dokumunu_icerir(istemci, conn):
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.D21")

    from app import rapor
    from app.crm import kullanim_ozeti
    m = rapor._mesaj(kullanim_ozeti(conn, 3600))
    assert f"Devir: 1 ({devri.NEDEN_HASTA} 1)" in m


# ── PRD 26-08-2026 İK-1/İK-2: bildirim içeriği ve giden kilitleri ──

def _bildirimler(c):
    return [g for g in c.gonderilenler if "İnsan müdahalesi gerekli" in g[1]]


def test_ik1_bildirimde_hasta_mesaji_gecmez(istemci, conn):
    """T1: bildirim metninde gorusmeler.mesaj içeriği geçmez (KVKK)."""
    _personel_ekle(conn, "sekreter", "+905551112233")
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.I1")

    metin = _bildirimler(istemci)[0][1]
    assert "yetkiliyle görüşmek istiyorum" not in metin, "hasta serbest metni bildirime girmez"
    assert "Son mesaj" not in metin
    assert TELEFON in metin and "Panelden devralın." in metin


def test_ik2_tavan_doluysa_bildirim_gitmez_devir_acilir(istemci, conn, monkeypatch, caplog):
    """T2: saatlik tavan dolu — bildirim gönderilmez, loglanır, devir açılır."""
    import logging

    _personel_ekle(conn, "sekreter", "+905551112233")
    monkeypatch.setattr(hatirlatma, "SAATLIK_TAVAN", 0)

    onceki = len(istemci.gonderilenler)
    with caplog.at_level(logging.WARNING, logger="app.devri"):
        _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.I2")

    assert _kisi(conn)["insan_devri_at"] is not None, "devir yine açılır"
    assert len(istemci.gonderilenler) == onceki + 1, "yalnız hastaya aktarım mesajı gitti"
    assert "Devir bildirimi gitmedi" in caplog.text, "tavan nedeniyle log düşmeli"


def test_ik2_sessiz_saatte_bildirim_dusurulur(istemci, conn, monkeypatch):
    """T3: sessiz saat — bildirim ertelenmez, düşürülür; devir kaydı panelde durur."""
    _personel_ekle(conn, "sekreter", "+905551112233")
    monkeypatch.setattr(hatirlatma, "sessiz_saatte_mi", lambda an: True)

    onceki = len(istemci.gonderilenler)
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.I3")

    assert len(istemci.gonderilenler) == onceki + 1, "personel bildirimi gitmedi"
    assert _kisi(conn)["insan_devri_at"] is not None, "devir kaydı durur"


def test_ik2_ayni_hasta_kisa_arayla_tek_bildirim(istemci, conn, monkeypatch):
    """T4: aynı hasta 5 dk içinde iki kez devre girerse tek bildirim gider."""
    _personel_ekle(conn, "sekreter", "+905551112233")
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.I4")
    assert len(_bildirimler(istemci)) == 1

    devir_yaz(conn, _kisi(conn)["id"], None)   # devri bitir, hemen yeniden aç
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.I5")
    assert len(_bildirimler(istemci)) == 1, "debounce: 15 dk içinde ikinci bildirim gitmez"

    monkeypatch.setattr(devri, "BILDIRIM_ARALIK_DK", 0)   # aralık doldu say
    devir_yaz(conn, _kisi(conn)["id"], None)
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.I6")
    assert len(_bildirimler(istemci)) == 2, "aralık dolduğunda bildirim yeniden gider"


def test_ik2_bildirim_sayaca_girer(istemci, conn):
    """Bildirim 'sistem' satırı olarak yazılır — giden tavanı onu görür."""
    _personel_ekle(conn, "sekreter", "+905551112233")
    _gonder(istemci, "yetkiliyle görüşmek istiyorum", "wamid.I7")

    kid = _kisi(conn)["id"]
    kayitlar = [g["mesaj"] for g in gorusme_gecmisi(conn, kid, limit=10, sistem_dahil=True)]
    assert any(m.startswith("devir bildirimi: 1 numara") for m in kayitlar)
    assert hatirlatma.giden_sayisi(conn, 1) >= 1
