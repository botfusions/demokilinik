"""Randevu hatırlatmaları ve giden mesaj kilitleri.

Bu modül kendiliğinden WhatsApp mesajı gönderiyor — WhatsApp'ın hesap kapatma
sebebi tam olarak budur. Buradaki testler ban riskini tutan kilitlerin gerçekten
kapalı olduğunu doğrular; gevşerlerse kliniğin numarası kapanır ve geri gelmez.
"""

from datetime import datetime, timedelta

import pytest

import app.hatirlatma as hatirlatma
from app.crm import randevu_iptal, randevu_olustur
from app.hatirlatma import (
    TavanAsildi,
    bekleyenler,
    hatirlatma_planla,
    hatirlatmalari_iptal_et,
    metin_uret,
    sessiz_saat_sonrasi,
    sessiz_saatte_mi,
    tavan_kontrol,
    tur_calistir,
)


def _slot(sira):
    """Birbiriyle çakışmayan gelecek randevu zamanları.

    Gün VE saat birlikte ilerler: pazar atlaması iki sırayı aynı güne düşürse bile
    saatleri farklı kalır.
    """
    g = datetime.now() + timedelta(days=sira // 8 + 1)
    while g.isoweekday() == 7:
        g += timedelta(days=1)
    return g.replace(hour=9 + sira % 8, minute=0, second=0, microsecond=0)


def _ileri(saat_sonra):
    """Şimdiden N saat sonrası, çalışma saatine ve açık güne oturtulmuş."""
    g = datetime.now() + timedelta(hours=saat_sonra)
    if g.hour < 9:
        g = g.replace(hour=10, minute=0)
    elif g.hour >= 17:
        g = (g + timedelta(days=1)).replace(hour=10, minute=0)
    while g.isoweekday() == 7:
        g = (g + timedelta(days=1)).replace(hour=10, minute=0)
    return g.replace(second=0, microsecond=0)


class SahteGonderici:
    """Gerçek WhatsApp yerine — kime ne gittiğini sayar."""

    def __init__(self, patlat=False):
        self.gonderilenler = []
        self.patlat = patlat

    def __call__(self, telefon, metin):
        if self.patlat:
            raise ConnectionError("WhatsApp'a ulaşılamadı")
        self.gonderilenler.append((telefon, metin))
        return f"wamid.{len(self.gonderilenler)}"


@pytest.fixture
def bekletme_yok(monkeypatch):
    """Gönderimler arası beklemeyi testte atla — davranışı değiştirmez."""
    monkeypatch.setattr(hatirlatma, "GONDERIM_ARASI_SN", 0)


@pytest.fixture
def gunduz(monkeypatch):
    """Testler gece de koşabilir; sessiz saat kuralı ayrıca test ediliyor."""
    monkeypatch.setattr(hatirlatma, "sessiz_saatte_mi", lambda an: False)


# ── planlama ────────────────────────────────────────────────

def test_iki_hatirlatma_planlanir(conn, kisi_id):
    bas = _ileri(48)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    assert set(hatirlatma_planla(conn, rid)) == {"24s", "1s"}


def test_gecmis_zamanli_hatirlatma_planlanmaz(conn, kisi_id):
    """Randevuya 3 saat kala açılan kayıt için '24 saat önce' mesajı atılmaz."""
    bas = _ileri(3)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    assert hatirlatma_planla(conn, rid) == ["1s"]


def test_ayni_randevuya_ikinci_kez_planlanmaz(conn, kisi_id):
    """Nöbetçi ya da personel iki kez tetiklerse hastaya iki mesaj gitmemeli."""
    bas = _ileri(48)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))

    hatirlatma_planla(conn, rid)
    assert hatirlatma_planla(conn, rid) == []

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM hatirlatmalar WHERE randevu_id = %s", (rid,))
        assert cur.fetchone()[0] == 2


def test_iptal_edilen_randevuya_planlanmaz(conn, kisi_id):
    bas = _ileri(48)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    randevu_iptal(conn, rid)
    assert hatirlatma_planla(conn, rid) == []


def test_iptal_gonderilmemis_hatirlatmalari_dusurur(conn, kisi_id):
    """İptal edilmiş randevu için hastaya hatırlatma gitmemeli."""
    bas = _ileri(48)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    hatirlatma_planla(conn, rid)

    randevu_iptal(conn, rid)
    hatirlatmalari_iptal_et(conn, rid)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM hatirlatmalar WHERE randevu_id = %s", (rid,))
        assert cur.fetchone()[0] == 0


# ── sessiz saat ─────────────────────────────────────────────

@pytest.mark.parametrize("saat,beklenen", [
    (23, True), (3, True), (8, True),      # 21:00–09:00 arası sessiz
    (9, False), (14, False), (20, False),
])
def test_sessiz_saat_gece_yarisini_dogru_kapsar(saat, beklenen):
    an = datetime(2026, 8, 6, saat, 0)
    assert sessiz_saatte_mi(an) is beklenen


def test_sessiz_saate_dusen_hatirlatma_sabaha_ertelenir():
    gece = datetime(2026, 8, 6, 3, 30)
    yeni = sessiz_saat_sonrasi(gece)
    assert yeni.hour == 9 and yeni.date() == gece.date()


def test_aksam_gec_saat_ertesi_sabaha_kayar():
    aksam = datetime(2026, 8, 6, 22, 30)
    yeni = sessiz_saat_sonrasi(aksam)
    assert yeni.hour == 9 and yeni.date() == (aksam + timedelta(days=1)).date()


def test_gunduz_saati_ertelenmez():
    an = datetime(2026, 8, 6, 14, 0)
    assert sessiz_saat_sonrasi(an) == an


def test_sessiz_saatte_gonderim_durur(conn, kisi_id, monkeypatch, bekletme_yok):
    bas = _ileri(2)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
            (rid,),
        )
    conn.commit()

    monkeypatch.setattr(hatirlatma, "sessiz_saatte_mi", lambda an: True)
    g = SahteGonderici()
    assert tur_calistir(conn, gonder_fn=g) == 0
    assert g.gonderilenler == []


# ── tavanlar: toplu mesaj kilidi ────────────────────────────

def test_saatlik_tavan_gonderimi_durdurur(conn, kisi_id, monkeypatch, gunduz, bekletme_yok):
    from app.crm import gorusme_ekle

    monkeypatch.setattr(hatirlatma, "SAATLIK_TAVAN", 3)
    for i in range(3):
        gorusme_ekle(conn, kisi_id, "giden", f"mesaj-{i}")

    with pytest.raises(TavanAsildi, match="saatlik"):
        tavan_kontrol(conn)


def test_gunluk_tavan_gonderimi_durdurur(conn, kisi_id, monkeypatch, gunduz):
    from app.crm import gorusme_ekle

    monkeypatch.setattr(hatirlatma, "SAATLIK_TAVAN", 999)
    monkeypatch.setattr(hatirlatma, "GUNLUK_TAVAN", 2)
    for i in range(2):
        gorusme_ekle(conn, kisi_id, "giden", f"mesaj-{i}")

    with pytest.raises(TavanAsildi, match="günlük"):
        tavan_kontrol(conn)


def test_tavan_dolunca_kuyruk_bosaltilmaz(conn, monkeypatch, gunduz, bekletme_yok):
    """Tavanı aşmak pahasına kuyruğu boşaltmak yasak — mesajlar sonraki tura kalır."""
    from app.crm import kisi_upsert

    monkeypatch.setattr(hatirlatma, "SAATLIK_TAVAN", 2)
    for i in range(5):
        kid = kisi_upsert(conn, f"90532111{i:04d}", f"Hasta {i}")
        bas = _slot(i)
        rid = randevu_olustur(conn, kid, "Kontrol", bas, bas + timedelta(minutes=30))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
                (rid,),
            )
    conn.commit()

    g = SahteGonderici()
    gonderilen = tur_calistir(conn, gonder_fn=g)

    assert gonderilen <= 2, "tavan aşıldı — ban riski"
    assert len(g.gonderilenler) == gonderilen


def test_bir_turda_alinan_is_sinirli(conn, monkeypatch, gunduz, bekletme_yok):
    """Bir anda yüzlerce mesaj çıkmasının yolu olmamalı.

    Randevu kuralları burada test edilmiyor; 25 bekleyen hatırlatma doğrudan
    yazılıyor ki kuyruk limiti tek başına ölçülsün.
    """
    from app.crm import kisi_upsert

    monkeypatch.setattr(hatirlatma, "SAATLIK_TAVAN", 999)
    bas = datetime.now() + timedelta(days=3)

    with conn.cursor() as cur:
        for i in range(25):
            kid = kisi_upsert(conn, f"90533222{i:04d}", f"Hasta {i}")
            cur.execute(
                "INSERT INTO randevular (kisi_id, hizmet, baslangic, bitis) "
                "VALUES (%s,'Kontrol',%s,%s) RETURNING id",
                (kid, bas + timedelta(minutes=i), bas + timedelta(minutes=i + 20)),
            )
            cur.execute(
                "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
                (cur.fetchone()[0],),
            )
    conn.commit()

    assert len(bekleyenler(conn)) <= 10, "tek turda alınan iş sınırsız"


def test_gonderimler_arasi_beklenir(conn, monkeypatch, gunduz):
    """Arka arkaya anlık gönderim yığın mesaj görüntüsü verir."""
    from app.crm import kisi_upsert

    monkeypatch.setattr(hatirlatma, "SAATLIK_TAVAN", 999)
    monkeypatch.setattr(hatirlatma, "GONDERIM_ARASI_SN", 5)

    for i in range(2):
        kid = kisi_upsert(conn, f"90534333{i:04d}", f"Hasta {i}")
        bas = _slot(i)
        rid = randevu_olustur(conn, kid, "Kontrol", bas, bas + timedelta(minutes=30))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
                (rid,),
            )
    conn.commit()

    beklemeler = []
    tur_calistir(conn, gonder_fn=SahteGonderici(), bekle_fn=beklemeler.append)
    assert beklemeler and all(s == 5 for s in beklemeler)


def test_toplu_gonderim_fonksiyonu_yok():
    """Alıcı listesi alan bir fonksiyon eklenirse bu test kırılır — kasıtlı."""
    import inspect

    import app.openwa as openwa

    for modul in (hatirlatma, openwa):
        for ad, nesne in inspect.getmembers(modul, inspect.isfunction):
            if nesne.__module__ != modul.__name__:
                continue
            imza = str(inspect.signature(nesne)).lower()
            assert "telefonlar" not in imza and "alicilar" not in imza, (
                f"{modul.__name__}.{ad} toplu alıcı alıyor — toplu mesaj yasağı ihlali"
            )
            assert "bulk" not in ad.lower() and "toplu" not in ad.lower(), (
                f"{modul.__name__}.{ad} toplu gönderim ima ediyor"
            )


# ── gönderim akışı ──────────────────────────────────────────

def test_hatirlatma_gonderilir_ve_isaretlenir(conn, kisi_id, gunduz, bekletme_yok):
    bas = _ileri(2)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
            (rid,),
        )
    conn.commit()

    g = SahteGonderici()
    assert tur_calistir(conn, gonder_fn=g) == 1
    assert len(g.gonderilenler) == 1

    # İkinci tur aynı mesajı tekrar göndermemeli
    assert tur_calistir(conn, gonder_fn=g) == 0
    assert len(g.gonderilenler) == 1


def test_gonderilen_hatirlatma_gorusmeye_yazilir(conn, kisi_id, gunduz, bekletme_yok):
    """Personel panelde hastaya ne gönderildiğini görebilmeli."""
    bas = _ileri(2)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
            (rid,),
        )
    conn.commit()

    tur_calistir(conn, gonder_fn=SahteGonderici())

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gorusmeler WHERE yon = 'giden'")
        assert cur.fetchone()[0] == 1


def test_iptal_edilen_randevuya_hatirlatma_gitmez(conn, kisi_id, gunduz, bekletme_yok):
    bas = _ileri(2)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
            (rid,),
        )
    conn.commit()
    randevu_iptal(conn, rid)     # hatırlatma kaydı elle silinmese bile

    g = SahteGonderici()
    assert tur_calistir(conn, gonder_fn=g) == 0
    assert g.gonderilenler == []


def test_gecmis_randevuya_hatirlatma_gitmez(conn, kisi_id, gunduz, bekletme_yok):
    """Nöbetçi bir süre çalışmadıysa geçmiş randevuların mesajı gitmemeli."""
    bas = datetime.now() - timedelta(hours=2)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO randevular (kisi_id, hizmet, baslangic, bitis) "
            "VALUES (%s,'Kontrol',%s,%s) RETURNING id",
            (kisi_id, bas, bas + timedelta(minutes=30)),
        )
        rid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',%s)",
            (rid, bas - timedelta(hours=1)),
        )
    conn.commit()

    assert tur_calistir(conn, gonder_fn=SahteGonderici()) == 0


def test_gonderim_hatasi_sonsuz_denenmez(conn, kisi_id, gunduz, bekletme_yok):
    """Ulaşılamayan numara her turda yeniden denenirse sonsuz gönderim olur."""
    bas = _ileri(2)
    rid = randevu_olustur(conn, kisi_id, "Kontrol", bas, bas + timedelta(minutes=30))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hatirlatmalar (randevu_id, tur, planlanan) VALUES (%s,'1s',now())",
            (rid,),
        )
    conn.commit()

    tur_calistir(conn, gonder_fn=SahteGonderici(patlat=True))

    with conn.cursor() as cur:
        cur.execute("SELECT gonderildi, hata FROM hatirlatmalar WHERE randevu_id = %s", (rid,))
        gonderildi, hata = cur.fetchone()

    assert gonderildi is not None, "başarısız gönderim işaretlenmeli"
    assert hata
    assert tur_calistir(conn, gonder_fn=SahteGonderici()) == 0


# ── mesaj metni ─────────────────────────────────────────────

def test_24s_metni_iptal_yolunu_soyler(conn):
    r = {"ad": "Ayşe Yılmaz", "hizmet": "Kontrol", "doktor_ad": "Dr. Ayla Tuncer",
         "baslangic": datetime(2026, 8, 7, 14, 0)}
    m = metin_uret(r, "24s")

    assert "Ayşe" in m and "14:00" in m and "07.08.2026" in m
    assert "Dr. Ayla Tuncer" in m
    assert "iptal" in m.lower()


def test_1s_metni_kisa_ve_iptal_iceriyor(conn):
    r = {"ad": "Ayşe", "hizmet": "Kontrol", "doktor_ad": None,
         "baslangic": datetime(2026, 8, 7, 14, 0)}
    m = metin_uret(r, "1s")

    assert "14:00" in m and "iptal" in m.lower()
    assert len(m) < 200


def test_adsiz_hastaya_da_duzgun_metin(conn):
    r = {"ad": None, "hizmet": "Kontrol", "doktor_ad": None,
         "baslangic": datetime(2026, 8, 7, 14, 0)}
    assert metin_uret(r, "24s").startswith("Merhaba,")
