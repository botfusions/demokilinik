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
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

PROJE_KOKU = Path(__file__).resolve().parent.parent
load_dotenv(PROJE_KOKU / ".env")

from app import ajan, openwa, saglik  # noqa: E402  (load_dotenv'den sonra)
from app.crm import (  # noqa: E402
    CalismaSaatiDisi,
    GecmisTarih,
    RandevuCakismasi,
    dolu_araliklar,
    gorusme_ekle,
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

def _oturum_var(request: Request) -> bool:
    kurabiye = request.cookies.get(COOKIE_ADI)
    if not kurabiye:
        return False
    try:
        return imzalayici.loads(kurabiye) == "personel"
    except BadSignature:
        return False


def personel(request: Request):
    """Panel sayfaları için. Yetkisizse /giris'e yönlendirir."""
    if not _oturum_var(request):
        raise HTTPException(status_code=303, headers={"Location": "/giris"})


def ic_anahtar(request: Request):
    """Ajanın kullandığı iç API için. Panel cookie'si buraya geçmez."""
    beklenen = os.environ.get("IC_API_ANAHTARI")
    if not beklenen or request.headers.get("X-Ic-Anahtar") != beklenen:
        raise HTTPException(status_code=401, detail="Geçersiz iç anahtar")


@app.exception_handler(HTTPException)
async def yonlendir_ya_da_hata(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


# ── giriş ───────────────────────────────────────────────────

@app.get("/giris", response_class=HTMLResponse)
def giris_sayfasi(request: Request, hata: str = ""):
    return sablonlar.TemplateResponse(request, "giris.html", {"hata": hata})


@app.post("/giris")
def giris(parola: str = Form(...)):
    if parola != os.environ.get("PANEL_PAROLA"):
        return RedirectResponse("/giris?hata=1", status_code=303)

    y = RedirectResponse("/", status_code=303)
    y.set_cookie(COOKIE_ADI, imzalayici.dumps("personel"), httponly=True, samesite="lax")
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
        "bugun": f"{b.day} {AYLAR_TR[b.month - 1]} {b.year}, {GUNLER_TR[b.weekday()]}",
        "sayilar": ozet_sayilar(conn),
        "gunler": gunler,
        "saatler": saatler,
        "yogun_gun": yogun_gun if yogun_gun and yogun_gun["adet"] else None,
        "yogun_saat": yogun_saat if yogun_saat and yogun_saat["adet"] else None,
        "hizmetler": hizmet_dagilimi(conn),
        "randevular": randevular_listele(conn, gun=b),
        "kisiler": kisiler_listele(conn)[:8],
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.get("/bilgi", response_class=HTMLResponse, dependencies=[Depends(personel)])
def bilgi_sayfasi(request: Request, conn=Depends(db)):
    return sablonlar.TemplateResponse(request, "bilgi.html", {
        "sayfa": "bilgi",
        "bilgiler": bilgiler_listele(conn),
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/bilgi", dependencies=[Depends(personel)])
def bilgi_kaydet(
    baslik: str = Form(...), icerik: str = Form(...), kategori: str = Form("genel"),
    conn=Depends(db),
):
    bilgi_ekle(conn, baslik, icerik, kategori)
    hermes_md_yaz(conn, HERMES_MD)
    return RedirectResponse("/bilgi", status_code=303)


@app.post("/bilgi/{bilgi_id}/pasiflestir", dependencies=[Depends(personel)])
def bilgi_kapat(bilgi_id: int, conn=Depends(db)):
    bilgi_pasiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    return RedirectResponse("/bilgi", status_code=303)


@app.post("/bilgi/{bilgi_id}/aktiflestir", dependencies=[Depends(personel)])
def bilgi_ac(bilgi_id: int, conn=Depends(db)):
    bilgi_aktiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    return RedirectResponse("/bilgi", status_code=303)


@app.get("/randevular", response_class=HTMLResponse, dependencies=[Depends(personel)])
def randevu_sayfasi(request: Request, gun: str = "", conn=Depends(db)):
    secilen = date.fromisoformat(gun) if gun else None
    return sablonlar.TemplateResponse(request, "randevular.html", {
        "sayfa": "randevular",
        "randevular": randevular_listele(conn, gun=secilen),
        "gun": gun,
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/randevular", dependencies=[Depends(personel)])
def randevu_ekle_elle(
    telefon: str = Form(...), hizmet: str = Form(...),
    baslangic: str = Form(...), bitis: str = Form(...),
    conn=Depends(db),
):
    kid = kisi_upsert(conn, telefon)
    try:
        randevu_olustur(conn, kid, hizmet, datetime.fromisoformat(baslangic),
                        datetime.fromisoformat(bitis))
    except (RandevuCakismasi, GecmisTarih, CalismaSaatiDisi) as e:
        return RedirectResponse(f"/randevular?hata={e}", status_code=303)
    return RedirectResponse("/randevular", status_code=303)


@app.post("/randevular/{randevu_id}/iptal", dependencies=[Depends(personel)])
def randevu_iptal_et(randevu_id: int, conn=Depends(db)):
    randevu_iptal(conn, randevu_id)
    return RedirectResponse("/randevular", status_code=303)


@app.get("/hastalar", response_class=HTMLResponse, dependencies=[Depends(personel)])
def hastalar(request: Request, conn=Depends(db)):
    return sablonlar.TemplateResponse(request, "hastalar.html", {
        "sayfa": "hastalar",
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
        "kisi": kisi,
        "gorusmeler": gorusme_gecmisi(conn, kisi_id, limit=200),
        "saglik": saglik.saglik_ozeti(conn),
    })


@app.post("/hastalar/{kisi_id}/not", dependencies=[Depends(personel)])
def hasta_notu(kisi_id: int, personel_notu: str = Form(""), conn=Depends(db)):
    personel_notu_yaz(conn, kisi_id, personel_notu)
    return RedirectResponse(f"/hastalar/{kisi_id}", status_code=303)


# ── ajanın kullandığı iç API ────────────────────────────────

@app.get("/api/uygunluk", dependencies=[Depends(ic_anahtar)])
def uygunluk(gun: str, conn=Depends(db)):
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
        "dolu": [
            {"baslangic": d["baslangic"].strftime("%H:%M"), "bitis": d["bitis"].strftime("%H:%M")}
            for d in dolu_araliklar(conn, g)
        ],
    }


@app.post("/api/randevu", dependencies=[Depends(ic_anahtar)])
def randevu_api(govde: dict, conn=Depends(db)):
    try:
        kid = kisi_upsert(conn, govde["telefon"], govde.get("ad"))
        rid = randevu_olustur(
            conn, kid, govde["hizmet"],
            datetime.fromisoformat(govde["baslangic"]),
            datetime.fromisoformat(govde["bitis"]),
            govde.get("notlar"),
        )
    except RandevuCakismasi as e:
        raise HTTPException(409, f"O saat dolu: {e}")
    except (GecmisTarih, CalismaSaatiDisi) as e:
        raise HTTPException(422, str(e))
    except KeyError as e:
        raise HTTPException(400, f"Eksik alan: {e}")

    return {"randevu_id": rid, "durum": "bekliyor"}


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
