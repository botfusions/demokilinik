"""Ajan eğitim merkezi — LLM'siz testler.

`_llm_cagir` ve `httpx.get` monkeypatch'lenir; gerçek ağ/LLM çağrılmaz.
DB gerektiren testler DATABASE_URL yoksa atlanır (conftest kuralı).
"""

import egitim


class _Yanit:
    def __init__(self, metin):
        self.text = metin

    def raise_for_status(self):
        pass


# ── JSON parse (LLM çıktısını soyma) ─────────────────────────

def test_json_citsiz_cozulur():
    assert egitim._json_ayistir('{"kayitlar": [], "uyarilar": []}') == {
        "kayitlar": [], "uyarilar": []
    }


def test_json_citli_cozulur():
    govde = egitim._json_ayistir('```json\n{"kayitlar": [], "uyarilar": ["x"]}\n```')
    assert govde["uyarilar"] == ["x"]


def test_json_bos_ve_bozuk():
    assert egitim._json_ayistir("") == {}
    assert egitim._json_ayistir("bu json değil") == {}


# ── _temizle: kategori sınırlama + boş eleme ─────────────────

def test_gecersiz_kategori_genele_duser():
    # 'fiyatlar' dahil tanımsız kategoriler genel'e düşer — fiyat KB'ye girmez
    k, _ = egitim._temizle({"kayitlar": [
        {"baslik": "X", "icerik": "Y", "kategori": "fiyatlar"},
        {"baslik": "A", "icerik": "B", "kategori": "tanimsiz"},
    ]})
    assert [r["kategori"] for r in k] == ["genel", "genel"]


def test_bos_alanlar_elanir():
    k, _ = egitim._temizle({"kayitlar": [
        {"baslik": "", "icerik": "Y"},        # başlıksız
        {"baslik": "A", "icerik": "   "},     # içeriksiz
        {"baslik": "K", "icerik": "D", "kategori": "adres"},
    ]})
    assert len(k) == 1 and k[0]["baslik"] == "K"


def test_gecerli_kategori_korunur():
    k, _ = egitim._temizle({"kayitlar": [
        {"baslik": "S", "icerik": "D", "kategori": "sss"},
    ]})
    assert k[0]["kategori"] == "sss"


# ── metni_ayristir: fiyat kuralı (LLM stub) ──────────────────

def test_metni_ayristir_fiyat_kayit_uretmez(monkeypatch):
    # LLM fiyatı uyarıya koyar, kayıt üretmez (sistem promptunun istediği gibi)
    monkeypatch.setattr(egitim, "_llm_cagir", lambda mesajlar:
        '{"kayitlar": [{"baslik": "Çalışma saatleri", "icerik": "Cumartesi 10-16", '
        '"kategori": "calisma_saatleri"}], "uyarilar": ["fiyat: implant 25.000 TL"]}')
    kayitlar, uyarilar = egitim.metni_ayristir("Cumartesi 10-16, implant 25.000")
    assert len(kayitlar) == 1
    assert all("fiyat" not in k["baslik"].lower() for k in kayitlar)
    assert uyarilar and any("fiyat" in u for u in uyarilar)


# ── site_tara: link filtreleme (httpx stub) ──────────────────

def test_site_ayni_domain_alinir_disari_ve_onceliksiz_elanir(monkeypatch):
    anasayfa = (
        '<a href="/iletisim">İletişim</a>'                  # aynı domain, öncelikli
        '<a href="https://baska.com/hakkimizda">X</a>'      # başka domain — elenir
        '<a href="/urun/123">ürün</a>'                      # önceliksiz yol — elenir
    )
    cagrilar = {}

    def sahte_get(url, **kw):
        cagrilar[url] = True
        return _Yanit(anasayfa if url == "https://klinik.com" else "<p>alt sayfa</p>")

    monkeypatch.setattr(egitim.httpx, "get", sahte_get)
    metin, hata = egitim._site_metni("https://klinik.com", derinlik=8)
    assert hata is None
    assert "https://klinik.com/iletisim" in cagrilar
    assert "https://baska.com/hakkimizda" not in cagrilar   # dışarı çıkmadı
    assert "https://klinik.com/urun/123" not in cagrilar    # öncelik filtresi


# ── DB: aktif (eğit) / pasif (tarama) ayrımı ────────────────

def test_egitim_aktif_ekler(conn, monkeypatch):
    monkeypatch.setattr(egitim, "_llm_cagir", lambda m:
        '{"kayitlar": [{"baslik": "Test saati", "icerik": "10-18", '
        '"kategori": "calisma_saatleri"}], "uyarilar": []}')
    kayitlar, _ = egitim.metni_ayristir("herhangi bir metin")
    from app.kb import bilgi_ekle, bilgiler_listele
    for k in kayitlar:
        bilgi_ekle(conn, k["baslik"], k["icerik"], k["kategori"], aktif=True)
    aktif = [b for b in bilgiler_listele(conn, yalniz_aktif=True) if b["baslik"] == "Test saati"]
    assert len(aktif) == 1


def test_site_tara_taslak_pasif_ekler(conn, monkeypatch):
    monkeypatch.setattr(egitim, "_llm_cagir", lambda m:
        '{"kayitlar": [{"baslik": "Adres test", "icerik": "Üsküdar", "kategori": "adres"}], '
        '"uyarilar": []}')
    monkeypatch.setattr(egitim.httpx, "get", lambda url, **kw: _Yanit(
        "<p>Özel Test Kliniği. Adres: Üsküdar, İstanbul. Telefon ve iletişim "
        "bilgileri sayfamızda yer almaktadır. Hizmetler: implant, ortodonti.</p>"))
    kayitlar, _ = egitim.site_tara("https://klinik.com")
    from app.kb import bilgi_ekle, bilgiler_listele
    for k in kayitlar:
        bilgi_ekle(conn, k["baslik"], k["icerik"], k["kategori"], aktif=False)
    # pasif: yalniz_aktif listede yok, tam listede var
    assert not any(b["baslik"] == "Adres test" for b in bilgiler_listele(conn, yalniz_aktif=True))
    assert any(b["baslik"] == "Adres test" and not b["aktif"] for b in bilgiler_listele(conn))


# ── sunucu (vendor konsolu): auth gate ─────────────────────

def test_sunucu_parolasiz_girise_yonlendirir(conn):
    from fastapi.testclient import TestClient

    from egitim import sunucu
    c = TestClient(sunucu.app, follow_redirects=False)
    r = c.get("/")
    assert r.status_code in (302, 303, 307)
    assert "/giris" in r.headers["location"]
