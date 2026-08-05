"""Klinik resepsiyonist köprüsü ve personel paneli.

Tek servis: WhatsApp webhook'unu karşılar, ajanı çağırır, cevabı gönderir; aynı
uygulama personelin paneli. İki ayrı yetki var — panel cookie'si (personel) ve
X-Ic-Anahtar (ajanın CRM'e yazması). Biri diğerini açmaz.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

PROJE_KOKU = Path(__file__).resolve().parent.parent
load_dotenv(PROJE_KOKU / ".env")

from app import ajan, openwa, saglik  # noqa: E402  (load_dotenv'den sonra)
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
    randevu_olustur,
    randevular_listele,
)
from app.db import baglan, sema_kur  # noqa: E402
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
    bilgi_aktiflestir,
    bilgi_pasiflestir,
    bilgiler_listele,
    hermes_md_yaz,
)

log = logging.getLogger("klinik")

HERMES_MD = PROJE_KOKU / ".hermes.md"
COOKIE_ADI = "klinik_oturum"
HATA_MESAJI = (
    "Mesajınızı aldık, birazdan size dönüş yapacağız. Acil durumlar için bizi arayabilirsiniz."
)

imzalayici = URLSafeSerializer(os.environ.get("COOKIE_SECRET", "gelistirme"), salt="panel")
sablonlar = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def yasam(app: FastAPI):
    import asyncio

    c = baglan()
    sema_kur(c)
    hermes_md_yaz(c, HERMES_MD)
    if ilk_admin_kur(c):
        log.warning("İlk yönetici oluşturuldu: kullanıcı 'admin', parola .env'deki PANEL_PAROLA")
    c.close()

    gorev = None
    if os.environ.get("SAGLIK_NOBETCISI", "1") == "1":
        gorev = asyncio.create_task(saglik.nobetci(baglan))
    yield
    if gorev:
        gorev.cancel()


app = FastAPI(title="Klinik Resepsiyonist", lifespan=yasam)


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


@app.get("/bilgi", response_class=HTMLResponse, dependencies=[Depends(personel)])
def bilgi_sayfasi(request: Request, conn=Depends(db)):
    return sablonlar.TemplateResponse(request, "bilgi.html", {
        "sayfa": "bilgi",
        "kullanici": _kim(request),
        "bilgiler": bilgiler_listele(conn),
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/bilgi", dependencies=[Depends(personel)])
def bilgi_kaydet(
    request: Request, baslik: str = Form(...), icerik: str = Form(...), kategori: str = Form("genel"),
    conn=Depends(db),
):
    bid = bilgi_ekle(conn, baslik, icerik, kategori)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi ekledi", f"#{bid} {baslik}")
    return RedirectResponse("/bilgi", status_code=303)


@app.post("/bilgi/{bilgi_id}/pasiflestir", dependencies=[Depends(personel)])
def bilgi_kapat(request: Request, bilgi_id: int, conn=Depends(db)):
    bilgi_pasiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi pasifleştirdi", f"#{bilgi_id}")
    return RedirectResponse("/bilgi", status_code=303)


@app.post("/bilgi/{bilgi_id}/aktiflestir", dependencies=[Depends(personel)])
def bilgi_ac(request: Request, bilgi_id: int, conn=Depends(db)):
    bilgi_aktiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi aktifleştirdi", f"#{bilgi_id}")
    return RedirectResponse("/bilgi", status_code=303)


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

    islem_yaz(conn, _kim(request), "randevu açtı", f"#{rid} {telefon} {baslangic}")
    return RedirectResponse("/randevular", status_code=303)


@app.post("/randevular/{randevu_id}/iptal", dependencies=[Depends(personel)])
def randevu_iptal_et(request: Request, randevu_id: int, conn=Depends(db)):
    randevu_iptal(conn, randevu_id)
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
                  notlar: str = Form(""), conn=Depends(db)):
    did = doktor_ekle(conn, ad.strip(), uzmanlik.strip() or None, notlar.strip() or None)
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

    doktor = kullanilan = None
    if doktor_id:
        kullanilan = next((d for d in doktorlar_listele(conn) if d["id"] == doktor_id), None)
        doktor = kullanilan["ad"] if kullanilan else None

    return {"randevu_id": rid, "durum": "bekliyor",
            "doktor_id": doktor_id, "doktor_ad": doktor, "doktor_otomatik_secildi": otomatik}


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
        try:
            yanit, maliyet = ajan.cevap_uret(gecmis, mesaj)
        except ajan.CevapUretilemedi as e:
            log.error("Ajan cevap üretemedi (%s): %s", telefon, e)
            yanit, maliyet = HATA_MESAJI, None

        # Önce kaydet, sonra gönder: WhatsApp'a ulaşılamazsa bile personel
        # panelde ajanın ne dediğini görebilmeli.
        gorusme_ekle(conn, kid, "giden", yanit, maliyet_usd=maliyet)
        openwa.mesaj_gonder(telefon, yanit)
    except Exception as e:
        log.exception("Mesaj işlenemedi (%s): %s", telefon, e)
    finally:
        conn.close()
