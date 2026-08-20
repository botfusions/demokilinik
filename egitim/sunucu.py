"""Ajan eğitim merkezi — vendor (kurulumcu) konsolu.

Klinik resepsiyonist panelinden ayrı, kendi başına çalışan tek-sayfalık araç.
Aynı Postgres DB'sine ve `.hermes.md`'ye yazar — yani burada eğitilen bilgi
ajanın bir sonraki mesajda söylediği şey olur. Müşteri panelinin aksine URL
tarama ve KB düzenleme burada; müşteriye yalnız yazarak-eğit bırakıldı.

Çalıştırma:
    uvicorn egitim.sunucu:app --port 8001

Giriş: klinik admin hesabı (kullanicilar tablosu, rol='admin'). Ayrı vendor
kullanıcısı yok — ponytail: en az kod + işlem izi (islem_yaz) aynı yerden.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

PROJE_KOKU = Path(__file__).resolve().parent.parent
load_dotenv(PROJE_KOKU / ".env")

from app import ajan, kural  # noqa: E402
from app.db import baglan, sema_kur  # noqa: E402
from app.kb import (  # noqa: E402
    KATEGORI_ADLARI,
    bilgi_aktiflestir,
    bilgi_ekle,
    bilgi_guncelle,
    bilgi_pasiflestir,
    bilgiler_listele,
    hermes_md_yaz,
)
from app.kullanici import islem_yaz, kullanici_dogrula, kullanici_getir  # noqa: E402
import app.hafif as hafif  # noqa: E402
from egitim import metni_ayristir, site_tara  # noqa: E402

log = logging.getLogger("egitim")

HERMES_MD = PROJE_KOKU / ".hermes.md"
COOKIE_ADI = "egitim_oturum"
_cookie_secret = os.environ.get("COOKIE_SECRET")
if not _cookie_secret:
    raise RuntimeError("COOKIE_SECRET env değişkeni zorunlu (eğitim oturum çerezlerini imzalar)")
imzalayici = URLSafeSerializer(_cookie_secret, salt="egitim")
sablonlar = Jinja2Templates(directory=str(Path(__file__).parent))


@asynccontextmanager
async def yasam(app: FastAPI):
    # Clinic app'siz de ayağa kalkabilmeli: şema + .hermes.md taze.
    c = baglan()
    sema_kur(c)
    hermes_md_yaz(c, HERMES_MD)
    c.close()
    yield


app = FastAPI(title="Ajan Eğitim Merkezi", lifespan=yasam)


def db():
    c = baglan()
    try:
        yield c
    finally:
        c.close()


# ── yetkilendirme (app/main.py kalıbının sade hali) ────────

def _oturum_kullanici_id(request: Request) -> int | None:
    kurabiye = request.cookies.get(COOKIE_ADI)
    if not kurabiye:
        return None
    try:
        veri = imzalayici.loads(kurabiye)
        return int(veri["k"]) if isinstance(veri, dict) else None
    except (BadSignature, KeyError, ValueError, TypeError):
        return None


def satici(request: Request):
    """Vendor girişi. Cookie → aktif admin kullanıcı. Değilse /giris'e yönlenir."""
    kid = _oturum_kullanici_id(request)
    if kid is None:
        raise HTTPException(status_code=303, headers={"Location": "/giris"})
    c = baglan()
    try:
        k = kullanici_getir(c, kid)
    finally:
        c.close()
    if not k or not k["aktif"] or k["rol"] != "admin":
        raise HTTPException(status_code=303, headers={"Location": "/giris?hata=oturum"})
    request.state.kullanici = k


def _kim(request: Request) -> dict | None:
    return getattr(request.state, "kullanici", None)


@app.exception_handler(HTTPException)
async def yonlendir_ya_da_hata(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


# ── giriş ───────────────────────────────────────────────────

@app.get("/giris", response_class=HTMLResponse)
def giris_sayfasi(request: Request, hata: str = ""):
    return sablonlar.TemplateResponse(request, "sablon.html", {"sayfa": "giris", "hata": hata})


@app.post("/giris")
def giris(kullanici_adi: str = Form(...), parola: str = Form(...), conn=Depends(db)):
    k = kullanici_dogrula(conn, kullanici_adi, parola)
    if not k or k["rol"] != "admin":
        return RedirectResponse("/giris?hata=1", status_code=303)
    islem_yaz(conn, k, "eğitim girişi")
    y = RedirectResponse("/", status_code=303)
    y.set_cookie(COOKIE_ADI, imzalayici.dumps({"k": k["id"]}), httponly=True, samesite="lax")
    return y


@app.post("/cikis")
def cikis():
    y = RedirectResponse("/giris", status_code=303)
    y.delete_cookie(COOKIE_ADI)
    return y


# ── konsol ──────────────────────────────────────────────────

# ── ajana sor (demo / eğitim doğrulama) ────────────────────
# ponytail: oturum-başı sohbet bellekte (dict[user_id]). Demo aracıdır;
# yeniden başlatmada sıfırlanır, çok-worker'da paylaşılmaz. Üretim değil.
_sohbet: dict[int, list[dict]] = {}


def _ajan_sor(conn, gecmis: list[dict], mesaj: str) -> str:
    """Demo yolu: kural → hafif → ajan. WhatsApp/CRM yan etkisi yoktur.

    Bu gerçek ajan beynidir — simülasyon değil. Ajan araç çağırabilir
    (randevu açar vb.); burada WhatsApp yerine bu ekran kullanıldığı için
    görüşme DB'ye yazılmaz, kimseye mesaj gitmez."""
    yanit = kural.cevap_dene(conn, mesaj)
    if yanit:
        return yanit
    dene = hafif.cevap_dene(gecmis, mesaj)
    if dene:
        return dene[0]
    yanit, _kullanim = ajan.cevap_uret(gecmis, mesaj)
    yanit, _konum = ajan.konum_ayikla(yanit)
    return yanit


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(satici)])
def konsol(request: Request, conn=Depends(db)):
    return _konsol_render(request, conn)


def _konsol_render(request: Request, conn, **ekstra):
    bilgiler = bilgiler_listele(conn)  # aktif + pasif
    return sablonlar.TemplateResponse(request, "sablon.html", {
        "sayfa": "konsol",
        "kullanici": _kim(request),
        "bilgiler": bilgiler,
        "kategoriler": KATEGORI_ADLARI,
        "taslak_adedi": sum(1 for b in bilgiler if not b["aktif"]),
        "sohbet": _sohbet.get(_kim(request)["id"], []),
        **ekstra,
    })


@app.post("/sor", dependencies=[Depends(satici)])
def sor(request: Request, mesaj: str = Form(...), conn=Depends(db)):
    kid = _kim(request)["id"]
    gecmis = list(_sohbet.get(kid, []))
    try:
        yanit = _ajan_sor(conn, gecmis, mesaj)
    except Exception as e:
        log.warning("Ajana sor başarısız: %s", e)
        return _konsol_render(request, conn, soru=mesaj, soru_hata=str(e))
    _sohbet.setdefault(kid, []).append({"yon": "gelen", "mesaj": mesaj})
    _sohbet[kid].append({"yon": "giden", "mesaj": yanit})
    return _konsol_render(request, conn)


@app.post("/sohbet/sifirla", dependencies=[Depends(satici)])
def sohbet_sifirla(request: Request, conn=Depends(db)):
    _sohbet.pop(_kim(request)["id"], None)
    return RedirectResponse("/", status_code=303)


@app.post("/egit", dependencies=[Depends(satici)])
def egit(request: Request, metin: str = Form(...), conn=Depends(db)):
    try:
        kayitlar, uyarilar = metni_ayristir(metin)
    except Exception as e:
        log.warning("Eğitim başarısız: %s", e)
        return RedirectResponse(f"/?hata={quote(str(e))}", status_code=303)
    mevcut = {b["baslik"] for b in bilgiler_listele(conn)}
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
    return RedirectResponse(f"/?{qs}", status_code=303)


@app.post("/tara", dependencies=[Depends(satici)])
def tara(request: Request, url: str = Form(...), conn=Depends(db)):
    try:
        kayitlar, uyarilar = site_tara(url)
    except Exception as e:
        log.warning("Site tarama başarısız: %s", e)
        return RedirectResponse(f"/?hata={quote(str(e))}", status_code=303)
    mevcut = {b["baslik"] for b in bilgiler_listele(conn)}
    taslak = 0
    for k in kayitlar:
        if k["baslik"] in mevcut:
            continue
        bid = bilgi_ekle(conn, k["baslik"], k["icerik"], k["kategori"], aktif=False)
        mevcut.add(k["baslik"])
        islem_yaz(conn, _kim(request), "siteden taslak ekledi", f"#{bid} {k['baslik']}")
        taslak += 1
    qs = f"taslak={taslak}"
    if uyarilar:
        qs += "&uyari=" + quote("; ".join(uyarilar))
    return RedirectResponse(f"/?{qs}", status_code=303)


@app.post("/kayit/{bilgi_id}/guncelle", dependencies=[Depends(satici)])
def kayit_guncelle(
    request: Request, bilgi_id: int,
    baslik: str = Form(...), icerik: str = Form(...), kategori: str = Form("genel"),
    conn=Depends(db),
):
    bilgi_guncelle(conn, bilgi_id, baslik, icerik, kategori)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi düzenledi", f"#{bilgi_id} {baslik}")
    return RedirectResponse("/", status_code=303)


@app.post("/kayit/{bilgi_id}/aktiflestir", dependencies=[Depends(satici)])
def kayit_ac(request: Request, bilgi_id: int, conn=Depends(db)):
    bilgi_aktiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi aktifleştirdi", f"#{bilgi_id}")
    return RedirectResponse("/", status_code=303)


@app.post("/kayit/{bilgi_id}/pasiflestir", dependencies=[Depends(satici)])
def kayit_kapat(request: Request, bilgi_id: int, conn=Depends(db)):
    bilgi_pasiflestir(conn, bilgi_id)
    hermes_md_yaz(conn, HERMES_MD)
    islem_yaz(conn, _kim(request), "bilgi pasifleştirdi", f"#{bilgi_id}")
    return RedirectResponse("/", status_code=303)
