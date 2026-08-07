"""Klinik resepsiyonist köprüsü ve personel paneli.

Tek servis: WhatsApp webhook'unu karşılar, ajanı çağırır, cevabı gönderir; aynı
uygulama personelin paneli. İki ayrı yetki var — panel cookie'si (personel) ve
X-Ic-Anahtar (ajanın CRM'e yazması). Biri diğerini açmaz.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

PROJE_KOKU = Path(__file__).resolve().parent.parent
load_dotenv(PROJE_KOKU / ".env")

from app import ajan, bildirim, hatirlatma, instagram, iyilestirme, kural, openwa, saglik  # noqa: E402  (load_dotenv'den sonra)
from app.crm import (  # noqa: E402
    CalismaSaatiDisi,
    GecmisTarih,
    RandevuCakismasi,
    DoktorYok,
    dolu_araliklar,
    doktor_bazli_doluluk,
    doktor_durum_yaz,
    doktor_ekle,
    doktorlar_listele,
    en_bos_doktor,
    en_erken_uygun,
    gorusme_ekle,
    hastanin_doktoru,
    gun_bazli_doluluk,
    hizmet_dagilimi,
    ozet_sayilar,
    saat_bazli_doluluk,
    gorusme_gecmisi,
    kisi_bul,
    kisi_upsert,
    kisiler_listele,
    personel_notu_yaz,
    randevu_iptal,
    randevu_durum_yaz,
    randevu_olustur,
    randevular_araliginda,
    randevular_listele,
)
from app.db import baglan, sema_kur  # noqa: E402
from app.hizmet import (  # noqa: E402
    HizmetVar,
    fiyat_guncelle,
    fiyat_metni,
    hizmet_ekle,
    hizmetler_listele,
    kampanya_durum_yaz,
    kampanya_ekle,
    kampanyalar_listele,
)
from app.kullanici import (  # noqa: E402
    KullaniciVar,
    ParolaZayif,
    ilk_admin_kur,
    islem_kayitlari,
    islem_yaz,
    kullanici_dogrula,
    kullanici_durum_yaz,
    kullanici_ekle,
    kullanici_getir,
    kullanicilar_listele,
    parola_degistir,
)
from app.kb import (  # noqa: E402
    bilgi_ekle,
    bilgi_guncelle,
    bilgi_aktiflestir,
    bilgi_pasiflestir,
    bilgiler_listele,
    hermes_md_yaz,
)
from egitim import metni_ayristir  # noqa: E402  (URL tarama vendor konsolunda: egitim.sunucu)

log = logging.getLogger("klinik")

HERMES_MD = PROJE_KOKU / ".hermes.md"
COOKIE_ADI = "klinik_oturum"
HATA_MESAJI = (
    "Mesajınızı aldık, birazdan size dönüş yapacağız. Acil durumlar için bizi arayabilirsiniz."
)

imzalayici = URLSafeSerializer(os.environ.get("COOKIE_SECRET", "gelistirme"), salt="panel")
sablonlar = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Panelde görünen isimler — DB'deki 3 durum (bekliyor/onayli/iptal) değişmiyor,
# yalnız daha açıklayıcı görünsün diye eşleniyor. CSS sınıfı hâlâ ham değeri
# kullanıyor (rozet.aktif/pasif gibi renk sınıfları bundan etkilenmez).
DURUM_ETIKETLERI = {"bekliyor": "Planlandı", "onayli": "Teyit Edildi", "iptal": "İptal Edildi"}
sablonlar.env.filters["durum_etiketi"] = lambda d: DURUM_ETIKETLERI.get(d, d)


@asynccontextmanager
async def yasam(app: FastAPI):
    import asyncio

    c = baglan()
    sema_kur(c)
    hermes_md_yaz(c, HERMES_MD)
    if ilk_admin_kur(c):
        log.warning("İlk yönetici oluşturuldu: kullanıcı 'admin', parola .env'deki PANEL_PAROLA")
    c.close()

    gorevler = []
    if os.environ.get("SAGLIK_NOBETCISI", "1") == "1":
        gorevler.append(asyncio.create_task(saglik.nobetci(baglan)))
    if os.environ.get("HATIRLATMA_NOBETCISI", "1") == "1":
        gorevler.append(asyncio.create_task(hatirlatma.nobetci(baglan)))
    # Instagram yapılandırılmamışsa nöbetçi hiç başlamaz — boşa Composio çağrısı
    # yapmaz, sağlık nöbetçisi de bu kanal için alarm çalmaz.
    if os.environ.get("INSTAGRAM_NOBETCISI", "1") == "1" and instagram.yapilandirildi_mi():
        gorevler.append(asyncio.create_task(instagram.nobetci(baglan)))
        log.info("Instagram bilgilendirme kanalı açık (%d sn aralık)", instagram.ARALIK_SN)
    yield
    for g in gorevler:
        g.cancel()


app = FastAPI(title="Klinik Resepsiyonist", lifespan=yasam)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

# Panel başlığı ve marka. Her kliniğe ayrı kurulum yapıldığı için tek değişken yeter.
KLINIK_ADI = os.environ.get("KLINIK_ADI", "Klinik Paneli")
# Jinja global: her şablona ayrı ayrı context'e koymak yerine tek yerden.
sablonlar.env.globals["klinik_adi"] = KLINIK_ADI


def db():
    c = baglan()
    try:
        yield c
    finally:
        c.close()


# ── yetkilendirme ───────────────────────────────────────────

def _oturum_kullanici_id(request: Request) -> int | None:
    kurabiye = request.cookies.get(COOKIE_ADI)
    if not kurabiye:
        return None
    try:
        veri = imzalayici.loads(kurabiye)
        return int(veri["k"]) if isinstance(veri, dict) else None
    except (BadSignature, KeyError, ValueError, TypeError):
        return None


def personel(request: Request):
    """Panel sayfaları için. Yetkisizse /giris'e yönlendirir.

    Kullanıcıyı request.state'e koyar; işlem kaydı kimin yaptığını oradan okur.
    Pasifleştirilen kullanıcının açık oturumu bir sonraki istekte kapanır.
    """
    kid = _oturum_kullanici_id(request)
    if kid is None:
        raise HTTPException(status_code=303, headers={"Location": "/giris"})

    c = baglan()
    try:
        k = kullanici_getir(c, kid)
    finally:
        c.close()

    if not k or not k["aktif"]:
        raise HTTPException(status_code=303, headers={"Location": "/giris?hata=oturum"})

    request.state.kullanici = k


def yonetici(request: Request):
    """Yalnız admin. Kullanıcı yönetimi ve doktor tanımı buradan geçer."""
    personel(request)
    if request.state.kullanici["rol"] != "admin":
        raise HTTPException(403, "Bu sayfa yalnızca yöneticiler içindir")


def _kim(request: Request) -> dict | None:
    return getattr(request.state, "kullanici", None)


def ic_anahtar(request: Request):
    """Ajanın kullandığı iç API için. Panel cookie'si buraya geçmez."""
    beklenen = os.environ.get("IC_API_ANAHTARI")
    if not beklenen or request.headers.get("X-Ic-Anahtar") != beklenen:
        raise HTTPException(status_code=401, detail="Geçersiz iç anahtar")


@app.exception_handler(HTTPException)
async def yonlendir_ya_da_hata(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    # Ajan iç API'yi curl ile çağırıp cevabı okuyor; HTML dönerse "o saat dolu"
    # ile "sunucu çöktü" arasındaki farkı göremez ve hastaya yanlış şey söyler.
    if request.url.path.startswith("/api/"):
        return JSONResponse({"hata": exc.detail}, status_code=exc.status_code)

    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


# ── giriş ───────────────────────────────────────────────────

@app.get("/giris", response_class=HTMLResponse)
def giris_sayfasi(request: Request, hata: str = ""):
    return sablonlar.TemplateResponse(request, "giris.html", {"hata": hata})


@app.post("/giris")
def giris(kullanici_adi: str = Form(...), parola: str = Form(...), conn=Depends(db)):
    k = kullanici_dogrula(conn, kullanici_adi, parola)
    if not k:
        # Kullanıcı adı mı parola mı yanlış, söylemiyoruz — hesap sayımına yardım etmez
        return RedirectResponse("/giris?hata=1", status_code=303)

    islem_yaz(conn, k, "giriş")
    y = RedirectResponse("/", status_code=303)
    y.set_cookie(COOKIE_ADI, imzalayici.dumps({"k": k["id"]}), httponly=True, samesite="lax")
    return y


@app.post("/cikis")
def cikis():
    y = RedirectResponse("/giris", status_code=303)
    y.delete_cookie(COOKIE_ADI)
    return y


# ── panel ───────────────────────────────────────────────────

GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
AYLAR_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
            "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(personel)])
def ozet(request: Request, conn=Depends(db)):
    gunler = gun_bazli_doluluk(conn)
    saatler = saat_bazli_doluluk(conn)
    b = date.today()

    # Grafiğin başlığında "en yoğun gün" yazılır; hiç randevu yoksa bir gün seçilmez
    yogun_gun = max(gunler, key=lambda g: g["adet"]) if gunler else None
    yogun_saat = max(saatler, key=lambda s: s["adet"]) if saatler else None

    return sablonlar.TemplateResponse(request, "ozet.html", {
        "sayfa": "ozet",
        "kullanici": _kim(request),
        "bugun": f"{b.day} {AYLAR_TR[b.month - 1]} {b.year}, {GUNLER_TR[b.weekday()]}",
        "sayilar": ozet_sayilar(conn),
        "gunler": gunler,
        "saatler": saatler,
        "yogun_gun": yogun_gun if yogun_gun and yogun_gun["adet"] else None,
        "yogun_saat": yogun_saat if yogun_saat and yogun_saat["adet"] else None,
        "hizmetler": hizmet_dagilimi(conn),
        "randevular": randevular_listele(conn, gun=b),
        "doktor_yuk": doktor_bazli_doluluk(conn),
        "kisiler": kisiler_listele(conn)[:8],
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.get("/bilgi", response_class=HTMLResponse, dependencies=[Depends(yonetici)])
def bilgi_sayfasi(request: Request, conn=Depends(db)):
    return sablonlar.TemplateResponse(request, "bilgi.html", {
        "sayfa": "bilgi",
        "kullanici": _kim(request),
        "bilgiler": bilgiler_listele(conn),
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/bilgi", dependencies=[Depends(yonetici)])
def bilgi_kaydet(
    request: Request, baslik: str = Form(...), icerik: str = Form(...), kategori: str = Form("genel"),
    conn=Depends(db),
):
    bid = bilgi_ekle(conn, baslik, icerik, kategori)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi ekledi", f"#{bid} {baslik}")
    return RedirectResponse("/bilgi", status_code=303)


@app.post("/bilgi/{bilgi_id}/guncelle", dependencies=[Depends(yonetici)])
def bilgi_duzenle(
    request: Request, bilgi_id: int, baslik: str = Form(...), icerik: str = Form(...),
    kategori: str = Form("genel"), conn=Depends(db),
):
    bilgi_guncelle(conn, bilgi_id, baslik, icerik, kategori)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi düzenledi", f"#{bilgi_id} {baslik}")
    return RedirectResponse("/bilgi", status_code=303)


@app.post("/bilgi/{bilgi_id}/pasiflestir", dependencies=[Depends(yonetici)])
def bilgi_kapat(request: Request, bilgi_id: int, conn=Depends(db)):
    bilgi_pasiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi pasifleştirdi", f"#{bilgi_id}")
    return RedirectResponse("/bilgi", status_code=303)


@app.post("/bilgi/{bilgi_id}/aktiflestir", dependencies=[Depends(yonetici)])
def bilgi_ac(request: Request, bilgi_id: int, conn=Depends(db)):
    bilgi_aktiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi aktifleştirdi", f"#{bilgi_id}")
    return RedirectResponse("/bilgi", status_code=303)


# ── ajan eğitim merkezi ──────────────────────────────────────
# İki girdi: sohbetle eğit (personel yazar, AKTİF iner) ve site tarama
# (makine kazır, PASİF/taslak iner — onay Bilgi Tabanı'nda, yukarıdaki rotalarla).

@app.get("/egitim", response_class=HTMLResponse, dependencies=[Depends(yonetici)])
def egitim_sayfasi(request: Request, conn=Depends(db)):
    # beklemede taslak sayısı: pasif KB kayıtları (onay bekleyen kazınan içerik)
    taslak_adedi = sum(1 for b in bilgiler_listele(conn) if not b["aktif"])
    return sablonlar.TemplateResponse(request, "egitim.html", {
        "sayfa": "egitim",
        "kullanici": _kim(request),
        "taslak_adedi": taslak_adedi,
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/egitim/egit", dependencies=[Depends(yonetici)])
def egitim_egit(request: Request, metin: str = Form(...), conn=Depends(db)):
    try:
        kayitlar, uyarilar = metni_ayristir(metin)
    except Exception as e:  # sağlayıcı/LLM hatası — panele kullanıcı dostu düşer
        log.warning("Eğitim başarısız: %s", e)
        return RedirectResponse(f"/egitim?hata={quote(str(e))}", status_code=303)
    mevcut = {b["baslik"] for b in bilgiler_listele(conn)}  # dedup: aktif + pasif
    eklendi = 0
    for k in kayitlar:
        if k["baslik"] in mevcut:
            continue
        bid = bilgi_ekle(conn, k["baslik"], k["icerik"], k["kategori"], aktif=True)
        mevcut.add(k["baslik"])
        islem_yaz(conn, _kim(request), "eğitimle bilgi ekledi", f"#{bid} {k['baslik']}")
        eklendi += 1
    if eklendi:
        hermes_md_yaz(conn, HERMES_MD)
    qs = f"eklendi={eklendi}"
    if uyarilar:
        qs += "&uyari=" + quote("; ".join(uyarilar))
    return RedirectResponse(f"/egitim?{qs}", status_code=303)


@app.get("/iyilestirme", response_class=HTMLResponse, dependencies=[Depends(yonetici)])
def iyilestirme_sayfasi(request: Request, conn=Depends(db)):
    """Ajan hiçbir şeyi otomatik değiştirmez — burası salt-okunur bir öneri
    listesi. Kararı ve yazımı (bilgi tabanı/SOUL.md) hep personel verir."""
    return sablonlar.TemplateResponse(request, "iyilestirme.html", {
        "sayfa": "iyilestirme",
        "kullanici": _kim(request),
        "bosluklar": iyilestirme.kb_bosluklari(conn),
        "tekrarlar": iyilestirme.tekrarlanan_sorular(conn),
        "saglik": saglik.saglik_ozeti(conn),
    })


# ── takvim ──────────────────────────────────────────────────

# Hekim renkleri: listedeki sıraya göre deterministik atanır. Rastgele ya da
# id'ye göre modüler seçim, doktor eklenince tüm renklerin kaymasına yol açardı;
# personel takvimi renkten tanıyor.
DOKTOR_RENKLERI = ["#10b981", "#6366f1", "#f59e0b", "#ec4899", "#06b6d4", "#8b5cf6"]


def hafta_basi(gun: date) -> date:
    """O günün içinde bulunduğu haftanın pazartesisi."""
    return gun - timedelta(days=gun.isoweekday() - 1)


@app.get("/takvim", response_class=HTMLResponse, dependencies=[Depends(personel)])
def takvim_sayfasi(request: Request, hafta: str = "", conn=Depends(db)):
    bas = hafta_basi(date.fromisoformat(hafta) if hafta else date.today())
    bit = bas + timedelta(days=7)

    doktorlar = doktorlar_listele(conn, yalniz_aktif=True)
    renk = {d["id"]: DOKTOR_RENKLERI[i % len(DOKTOR_RENKLERI)]
            for i, d in enumerate(doktorlar)}

    return sablonlar.TemplateResponse(request, "takvim.html", {
        "sayfa": "takvim",
        "kullanici": _kim(request),
        "randevular": randevular_araliginda(conn, bas, bit),
        "doktorlar": doktorlar,
        "renk": renk,
        "gunler": [bas + timedelta(days=i) for i in range(7)],
        "saatler": list(range(9, 19)),
        "bugun": date.today(),
        "hafta_bas": bas,
        "onceki": (bas - timedelta(days=7)).isoformat(),
        "sonraki": bit.isoformat(),
        # Composio anahtarı yokken eşitleme düğmesi pasif kalır; sahte
        # "eşitlendi" göstermek personeli yanıltırdı.
        "takvim_bagli": bool(os.environ.get("COMPOSIO_API_KEY")),
        "saglik": saglik.saglik_ozeti(conn),
    })


# ── yönetici: fiyat ve kampanya ─────────────────────────────

@app.get("/yonetici", response_class=HTMLResponse, dependencies=[Depends(yonetici)])
def yonetici_sayfasi(request: Request, hata: str = "", conn=Depends(db)):
    hizmetler = hizmetler_listele(conn)
    gecerli = kampanyalar_listele(conn, yalniz_gecerli=True)
    return sablonlar.TemplateResponse(request, "yonetici.html", {
        "sayfa": "yonetici",
        "kullanici": _kim(request),
        "hizmetler": hizmetler,
        "kampanyalar": kampanyalar_listele(conn),
        # Panelde "ajan bunu şöyle söyleyecek" satırı — fiyatın hastaya nasıl
        # gideceğini kaydetmeden önce görmek, yanlış kampanyayı yakalatır.
        "onizleme": {h["id"]: fiyat_metni(h, gecerli) for h in hizmetler},
        "bugun_tarih": date.today(),
        "hata": hata,
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/hizmet", dependencies=[Depends(yonetici)])
def hizmet_kaydet(request: Request, ad: str = Form(...), fiyat: float = Form(...),
                  conn=Depends(db)):
    try:
        hid = hizmet_ekle(conn, ad, fiyat)
    except HizmetVar as e:
        return RedirectResponse(f"/yonetici?hata={e}", status_code=303)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "hizmet ekledi", f"#{hid} {ad} {fiyat}")
    return RedirectResponse("/yonetici", status_code=303)


@app.post("/hizmet/fiyatlar", dependencies=[Depends(yonetici)])
async def fiyatlari_kaydet(request: Request, conn=Depends(db)):
    """Fiyat listesinin toplu kaydı — form `fiyat_<id>` alanlarıyla gelir."""
    form = await request.form()
    degisen = []
    for anahtar, deger in form.items():
        if not anahtar.startswith("fiyat_"):
            continue
        try:
            hid, yeni = int(anahtar[6:]), float(deger)
        except ValueError:
            continue
        if fiyat_guncelle(conn, hid, yeni):
            degisen.append(f"#{hid}={yeni:g}")

    if degisen:
        hermes_md_yaz(conn, HERMES_MD)
        islem_yaz(conn, _kim(request), "fiyat güncelledi", ", ".join(degisen))
    return RedirectResponse("/yonetici", status_code=303)


@app.post("/kampanya", dependencies=[Depends(yonetici)])
def kampanya_kaydet(request: Request, ad: str = Form(...), indirim: int = Form(...),
                    hizmet_id: str = Form(""), bitis: str = Form(""), conn=Depends(db)):
    if not 1 <= indirim <= 100:
        return RedirectResponse("/yonetici?hata=İndirim 1-100 arası olmalı", status_code=303)

    kid = kampanya_ekle(
        conn, ad, indirim,
        hizmet_id=int(hizmet_id) if hizmet_id else None,
        bitis=date.fromisoformat(bitis) if bitis else None,
    )
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "kampanya ekledi", f"#{kid} {ad} %{indirim}")
    return RedirectResponse("/yonetici", status_code=303)


@app.post("/kampanya/{kampanya_id}/durum", dependencies=[Depends(yonetici)])
def kampanya_durum(request: Request, kampanya_id: int, aktif: str = Form(""),
                   conn=Depends(db)):
    acik = aktif == "1"
    kampanya_durum_yaz(conn, kampanya_id, acik)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "kampanya " + ("açtı" if acik else "kapattı"),
              f"#{kampanya_id}")
    return RedirectResponse("/yonetici", status_code=303)


@app.get("/randevular", response_class=HTMLResponse, dependencies=[Depends(personel)])
def randevu_sayfasi(request: Request, gun: str = "", hata: str = "", conn=Depends(db)):
    secilen = date.fromisoformat(gun) if gun else None
    return sablonlar.TemplateResponse(request, "randevular.html", {
        "sayfa": "randevular",
        "kullanici": _kim(request),
        "randevular": randevular_listele(conn, gun=secilen),
        "doktorlar": doktorlar_listele(conn, yalniz_aktif=True),
        "gun": gun,
        "hata": hata,
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/randevular", dependencies=[Depends(personel)])
def randevu_ekle_elle(
    request: Request,
    telefon: str = Form(...), hizmet: str = Form(...),
    baslangic: str = Form(...), bitis: str = Form(...),
    doktor_id: str = Form(""), acil: str = Form(""),
    conn=Depends(db),
):
    kid = kisi_upsert(conn, telefon)
    try:
        rid = randevu_olustur(
            conn, kid, hizmet,
            datetime.fromisoformat(baslangic), datetime.fromisoformat(bitis),
            doktor_id=int(doktor_id) if doktor_id else None,
            acil=bool(acil),
        )
    except (RandevuCakismasi, GecmisTarih, CalismaSaatiDisi, DoktorYok) as e:
        return RedirectResponse(f"/randevular?hata={e}", status_code=303)

    hatirlatma.hatirlatma_planla(conn, rid)
    bildirim.yeni_randevu_bildir(conn, rid)
    islem_yaz(conn, _kim(request), "randevu açtı", f"#{rid} {telefon} {baslangic}")
    return RedirectResponse("/randevular", status_code=303)


@app.post("/randevular/{randevu_id}/iptal", dependencies=[Depends(personel)])
def randevu_iptal_et(request: Request, randevu_id: int, conn=Depends(db)):
    randevu_iptal(conn, randevu_id)
    hatirlatma.hatirlatmalari_iptal_et(conn, randevu_id)
    islem_yaz(conn, _kim(request), "randevu iptal etti", f"#{randevu_id}")
    return RedirectResponse("/randevular", status_code=303)


@app.get("/hastalar", response_class=HTMLResponse, dependencies=[Depends(personel)])
def hastalar(request: Request, conn=Depends(db)):
    return sablonlar.TemplateResponse(request, "hastalar.html", {
        "sayfa": "hastalar",
        "kullanici": _kim(request),
        "kisiler": kisiler_listele(conn),
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.get("/hastalar/{kisi_id}", response_class=HTMLResponse, dependencies=[Depends(personel)])
def hasta_detay(request: Request, kisi_id: int, conn=Depends(db)):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM kisiler WHERE id = %s", (kisi_id,))
        kisi = cur.fetchone()
    if not kisi:
        raise HTTPException(404, "Hasta bulunamadı")

    return sablonlar.TemplateResponse(request, "hasta.html", {
        "sayfa": "hastalar",
        "kullanici": _kim(request),
        "kisi": kisi,
        "gorusmeler": gorusme_gecmisi(conn, kisi_id, limit=200),
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/hastalar/{kisi_id}/not", dependencies=[Depends(personel)])
def hasta_notu(request: Request, kisi_id: int, personel_notu: str = Form(""), conn=Depends(db)):
    personel_notu_yaz(conn, kisi_id, personel_notu)
    islem_yaz(conn, _kim(request), "hasta notu yazdı", f"hasta #{kisi_id}")
    return RedirectResponse(f"/hastalar/{kisi_id}", status_code=303)



# ── doktorlar (yalnız yönetici tanımlar) ────────────────────

@app.get("/doktorlar", response_class=HTMLResponse, dependencies=[Depends(personel)])
def doktor_sayfasi(request: Request, hata: str = "", conn=Depends(db)):
    return sablonlar.TemplateResponse(request, "doktorlar.html", {
        "sayfa": "doktorlar",
        "kullanici": _kim(request),
        "doktorlar": doktorlar_listele(conn),
        "yuk": doktor_bazli_doluluk(conn),
        "hata": hata,
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/doktorlar", dependencies=[Depends(yonetici)])
def doktor_kaydet(request: Request, ad: str = Form(...), uzmanlik: str = Form(""),
                  notlar: str = Form(""), telefon: str = Form(""), conn=Depends(db)):
    did = doktor_ekle(conn, ad.strip(), uzmanlik.strip() or None, notlar.strip() or None,
                      telefon.strip() or None)
    islem_yaz(conn, _kim(request), "doktor ekledi", f"#{did} {ad}")
    return RedirectResponse("/doktorlar", status_code=303)


@app.post("/doktorlar/{doktor_id}/durum", dependencies=[Depends(yonetici)])
def doktor_durum(request: Request, doktor_id: int, aktif: str = Form(""), conn=Depends(db)):
    yeni = bool(aktif)
    doktor_durum_yaz(conn, doktor_id, yeni)
    islem_yaz(conn, _kim(request), "doktor " + ("aktifleştirdi" if yeni else "pasifleştirdi"),
              f"#{doktor_id}")
    return RedirectResponse("/doktorlar", status_code=303)


# ── kullanıcılar (yalnız yönetici) ──────────────────────────

@app.get("/kullanicilar", response_class=HTMLResponse, dependencies=[Depends(yonetici)])
def kullanici_sayfasi(request: Request, hata: str = "", conn=Depends(db)):
    return sablonlar.TemplateResponse(request, "kullanicilar.html", {
        "sayfa": "kullanicilar",
        "kullanici": _kim(request),
        "kullanicilar": kullanicilar_listele(conn),
        "kayitlar": islem_kayitlari(conn, limit=60),
        "hata": hata,
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/kullanicilar", dependencies=[Depends(yonetici)])
def kullanici_kaydet(request: Request, kullanici_adi: str = Form(...),
                     parola: str = Form(...), rol: str = Form("personel"),
                     ad: str = Form(""), conn=Depends(db)):
    try:
        kid = kullanici_ekle(conn, kullanici_adi, parola, rol, ad.strip() or None)
    except (ParolaZayif, KullaniciVar) as e:
        return RedirectResponse(f"/kullanicilar?hata={e}", status_code=303)

    islem_yaz(conn, _kim(request), "kullanıcı açtı", f"#{kid} {kullanici_adi} ({rol})")
    return RedirectResponse("/kullanicilar", status_code=303)


@app.post("/kullanicilar/{kullanici_id}/parola", dependencies=[Depends(yonetici)])
def kullanici_parola(request: Request, kullanici_id: int, parola: str = Form(...),
                     conn=Depends(db)):
    try:
        parola_degistir(conn, kullanici_id, parola)
    except ParolaZayif as e:
        return RedirectResponse(f"/kullanicilar?hata={e}", status_code=303)

    islem_yaz(conn, _kim(request), "parola değiştirdi", f"kullanıcı #{kullanici_id}")
    return RedirectResponse("/kullanicilar", status_code=303)


@app.post("/kullanicilar/{kullanici_id}/durum", dependencies=[Depends(yonetici)])
def kullanici_durum(request: Request, kullanici_id: int, aktif: str = Form(""),
                    conn=Depends(db)):
    yeni = bool(aktif)
    try:
        kullanici_durum_yaz(conn, kullanici_id, yeni)
    except ValueError as e:
        return RedirectResponse(f"/kullanicilar?hata={e}", status_code=303)

    islem_yaz(conn, _kim(request), "kullanıcı " + ("aktifleştirdi" if yeni else "pasifleştirdi"),
              f"#{kullanici_id}")
    return RedirectResponse("/kullanicilar", status_code=303)


# ── ajanın kullandığı iç API ────────────────────────────────

@app.get("/api/doktorlar", dependencies=[Depends(ic_anahtar)])
def doktorlar_api(telefon: str = "", conn=Depends(db)):
    """Aktif doktorlar. `telefon` verilirse hastanın daha önce gittiği doktor da döner."""
    cevap = {
        "doktorlar": [
            {"id": d["id"], "ad": d["ad"], "uzmanlik": d["uzmanlik"]}
            for d in doktorlar_listele(conn, yalniz_aktif=True)
        ],
        "onceki_doktor": None,
        "ilk_ziyaret": True,
    }

    if telefon:
        k = kisi_bul(conn, telefon)
        if k:
            onceki = hastanin_doktoru(conn, k["id"])
            cevap["ilk_ziyaret"] = onceki is None
            if onceki and onceki["aktif"]:
                cevap["onceki_doktor"] = {"id": onceki["id"], "ad": onceki["ad"]}

    return cevap


@app.get("/api/uygunluk", dependencies=[Depends(ic_anahtar)])
def uygunluk(gun: str, doktor_id: int | None = None, conn=Depends(db)):
    from app.crm import _calisma_penceresi

    gunler, ac, kapa = _calisma_penceresi()
    g = date.fromisoformat(gun)
    if g.isoweekday() not in gunler:
        return {"gun": gun, "acik": False, "dolu": []}

    return {
        "gun": gun,
        "acik": True,
        "acilis": ac.strftime("%H:%M"),
        "kapanis": kapa.strftime("%H:%M"),
        "doktor_id": doktor_id,
        "dolu": [
            {
                "baslangic": d["baslangic"].strftime("%H:%M"),
                "bitis": d["bitis"].strftime("%H:%M"),
                "doktor": d.get("doktor_ad"),
            }
            for d in dolu_araliklar(conn, g, doktor_id)
        ],
    }


@app.get("/api/en-erken", dependencies=[Depends(ic_anahtar)])
def en_erken(sure_dk: int = 30, doktor_id: int | None = None, conn=Depends(db)):
    """Acil vaka için en erken müsait slot ve o slotta en boş doktor."""
    slot = en_erken_uygun(conn, sure_dk, doktor_id=doktor_id)
    if not slot:
        return {"bulundu": False}

    return {
        "bulundu": True,
        "baslangic": slot["baslangic"].isoformat(),
        "bitis": slot["bitis"].isoformat(),
        "doktor_id": slot.get("doktor_id"),
        "doktor_ad": slot.get("doktor_ad"),
    }


@app.post("/api/randevu", dependencies=[Depends(ic_anahtar)])
def randevu_api(govde: dict, conn=Depends(db)):
    try:
        kid = kisi_upsert(conn, govde["telefon"], govde.get("ad"))
        bas = datetime.fromisoformat(govde["baslangic"])
        bit = datetime.fromisoformat(govde["bitis"])
        doktor_id = govde.get("doktor_id")
        otomatik = False

        # Doktor belirtilmediyse o aralıkta en boş hekime dağıt. Klinikte hiç
        # doktor tanımlı değilse doktor_id NULL kalır ve sistem tek hekimli çalışır.
        if doktor_id is None and doktorlar_listele(conn, yalniz_aktif=True):
            secilen = en_bos_doktor(conn, bas, bit)
            if not secilen:
                raise RandevuCakismasi(f"{bas:%d.%m.%Y %H:%M} saatinde müsait doktor yok")
            doktor_id, otomatik = secilen["id"], True

        rid = randevu_olustur(
            conn, kid, govde["hizmet"], bas, bit, govde.get("notlar"),
            doktor_id=doktor_id, acil=bool(govde.get("acil")),
        )
    except RandevuCakismasi as e:
        raise HTTPException(409, f"O saat dolu: {e}")
    except (GecmisTarih, CalismaSaatiDisi) as e:
        raise HTTPException(422, str(e))
    except DoktorYok as e:
        raise HTTPException(422, str(e))
    except KeyError as e:
        raise HTTPException(400, f"Eksik alan: {e}")

    hatirlatma.hatirlatma_planla(conn, rid)
    bildirim.yeni_randevu_bildir(conn, rid)

    doktor = kullanilan = None
    if doktor_id:
        kullanilan = next((d for d in doktorlar_listele(conn) if d["id"] == doktor_id), None)
        doktor = kullanilan["ad"] if kullanilan else None

    return {"randevu_id": rid, "durum": "bekliyor",
            "doktor_id": doktor_id, "doktor_ad": doktor, "doktor_otomatik_secildi": otomatik}


@app.get("/api/hasta-randevulari", dependencies=[Depends(ic_anahtar)])
def hasta_randevulari(telefon: str, conn=Depends(db)):
    """Hastanın gelecek randevuları — hatırlatmaya 'iptal' cevabı geldiğinde gerekir."""
    k = kisi_bul(conn, telefon)
    if not k:
        return {"randevular": []}

    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.id, r.hizmet, r.baslangic, r.bitis, r.durum, d.ad AS doktor_ad
              FROM randevular r LEFT JOIN doktorlar d ON d.id = r.doktor_id
             WHERE r.kisi_id = %s AND r.durum <> 'iptal' AND r.baslangic > now()
             ORDER BY r.baslangic
            """,
            (k["id"],),
        )
        satirlar = cur.fetchall()

    return {"randevular": [
        {
            "randevu_id": r["id"],
            "hizmet": r["hizmet"],
            "baslangic": r["baslangic"].isoformat(),
            "doktor_ad": r["doktor_ad"],
            "durum": r["durum"],
        }
        for r in satirlar
    ]}


@app.post("/api/randevu/{randevu_id}/iptal", dependencies=[Depends(ic_anahtar)])
def randevu_iptal_api(randevu_id: int, govde: dict | None = None, conn=Depends(db)):
    """Hasta WhatsApp'tan iptal ettiğinde ajan burayı çağırır.

    Telefon zorunlu: ajan yalnızca konuştuğu hastanın randevusunu iptal edebilsin,
    rastgele bir numarayla başkasının randevusunu düşüremesin.
    """
    telefon = (govde or {}).get("telefon")
    if not telefon:
        raise HTTPException(400, "telefon alanı zorunlu")

    k = kisi_bul(conn, telefon)
    if not k:
        raise HTTPException(404, "Hasta bulunamadı")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT kisi_id, durum FROM randevular WHERE id = %s", (randevu_id,)
        )
        r = cur.fetchone()

    if not r:
        raise HTTPException(404, "Randevu bulunamadı")
    if r[0] != k["id"]:
        raise HTTPException(403, "Bu randevu başka bir hastaya ait")
    if r[1] == "iptal":
        return {"randevu_id": randevu_id, "durum": "iptal", "zaten_iptal": True}

    randevu_iptal(conn, randevu_id)
    hatirlatma.hatirlatmalari_iptal_et(conn, randevu_id)
    return {"randevu_id": randevu_id, "durum": "iptal"}


@app.post("/api/randevu/{randevu_id}/onayla", dependencies=[Depends(ic_anahtar)])
def randevu_onayla_api(randevu_id: int, govde: dict | None = None, conn=Depends(db)):
    """Hasta hatırlatmaya 'evet' dediğinde randevu 'onayli' olur."""
    telefon = (govde or {}).get("telefon")
    if not telefon:
        raise HTTPException(400, "telefon alanı zorunlu")

    k = kisi_bul(conn, telefon)
    with conn.cursor() as cur:
        cur.execute("SELECT kisi_id FROM randevular WHERE id = %s", (randevu_id,))
        r = cur.fetchone()

    if not k or not r:
        raise HTTPException(404, "Randevu bulunamadı")
    if r[0] != k["id"]:
        raise HTTPException(403, "Bu randevu başka bir hastaya ait")

    randevu_durum_yaz(conn, randevu_id, "onayli")
    return {"randevu_id": randevu_id, "durum": "onayli"}


# ── WhatsApp webhook ────────────────────────────────────────

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, arka_plan: BackgroundTasks):
    ham = await request.body()
    gizli = os.environ.get("WEBHOOK_SECRET", "")

    if not openwa.imza_dogrula(ham, request.headers.get("X-OpenWA-Signature"), gizli):
        raise HTTPException(401, "Geçersiz imza")

    olay = await request.json()
    if olay.get("event") != "message.received":
        return {"durum": "yoksayildi"}

    veri = olay.get("data", {})
    # Grup mesajlarına ajan cevap vermez — klinik grubuna düşen sohbet hastaya gitmez
    if veri.get("isGroup"):
        return {"durum": "grup_yoksayildi"}

    mesaj = (veri.get("body") or "").strip()
    if not mesaj:
        return {"durum": "bos"}

    arka_plan.add_task(
        _mesaji_isle,
        openwa.telefon_ayikla(veri.get("from", "")),
        mesaj,
        veri.get("id"),
        (veri.get("contact") or {}).get("name"),
    )
    return {"durum": "alindi"}


def _mesaji_isle(telefon: str, mesaj: str, wa_id: str | None, ad: str | None) -> None:
    """Webhook'un arka plan işi. Kayıt → ajan → gönderim."""
    conn = baglan()
    try:
        kid = kisi_upsert(conn, telefon, ad)

        if gorusme_ekle(conn, kid, "gelen", mesaj, wa_message_id=wa_id) is None:
            log.info("Tekrar teslimat yoksayıldı: %s", wa_id)
            return

        gecmis = gorusme_gecmisi(conn, kid, limit=10)[:-1]  # yeni mesajı prompt ayrıca ekler

        # Kural katmanı (LLM'siz) → hatırlatma kısayolu (LLM'siz) → tam ajan (LLM).
        # Sıralama en ucuzdan en pahalıya: "100 mesajın 100'ü LLM'ye" olmasın.
        if (yanit := kural.cevap_dene(conn, mesaj)) is not None:
            maliyet = None
        elif (yanit := kural.hatirlatma_cevabi_dene(telefon, mesaj)) is not None:
            maliyet = None
        else:
            try:
                yanit, maliyet = ajan.cevap_uret(gecmis, mesaj)
            except ajan.CevapUretilemedi as e:
                log.error("Ajan cevap üretemedi (%s): %s", telefon, e)
                yanit, maliyet = HATA_MESAJI, None

        yanit, konum_istendi = ajan.konum_ayikla(yanit)

        # Önce kaydet, sonra gönder: WhatsApp'a ulaşılamazsa bile personel
        # panelde ajanın ne dediğini görebilmeli.
        gorusme_ekle(conn, kid, "giden", yanit, maliyet_usd=maliyet)
        openwa.mesaj_gonder(telefon, yanit)

        # Konum iğnesi metinden SONRA gider: hasta önce adresi okur, sonra
        # haritayı görür. Konum tanımlı değilse sessizce atlanır — adres zaten
        # cevabın içinde yazılı.
        if konum_istendi and (koordinat := ajan.klinik_konumu()):
            try:
                openwa.konum_gonder(telefon, *koordinat)
            except Exception as e:
                log.warning("Konum gönderilemedi (%s): %s", telefon, e)
    except Exception as e:
        log.exception("Mesaj işlenemedi (%s): %s", telefon, e)
    finally:
        conn.close()
