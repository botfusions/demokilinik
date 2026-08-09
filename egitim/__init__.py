"""Ajan eğitim merkezi — bilgi tabanını iki yoldan besler (paylaşılan lib).

1. metni_ayristir: personelin yazdığı serbest Türkçe metni KB kayıtlarına
   ayırır. Yazan kişiye güvenildiği için AKTİF eklenir.
2. site_tara: klinik sitesini (ana + alt sayfalar) kazıyıp KB kayıtlarına
   ayırır. Makine-kazınan olduğu için PASİF (taslak) eklenir; onay UI'inde
   personel açar.

Bu paket iki tarafı da besler: vendor konsolu (egitim.sunucu) her iki yolu da
kullanır; klinik paneli (app.main) yalnızca metni_ayristir'ı çağırır — müşteri
URL girmez. İçerik her ikisinde de aynı app.kb / app.hafif üzerinden geçer.

İki sert kural:
- Fiyat asla KB'ye girmez. Parser fiyat görünce kayıt üretmez, uyarı döner
  (fiyatın tek kaynağı `hizmetler` tablosu — kb.py).
- Aynı kimlik/bilgi ve sağlayıcı çözümlemesi hafif.py'den gelir; tekrar edilmez.
"""

import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

from app.hafif import ZAMAN_ASIMI, kimlik_ve_bilgi, saglayici
from app.kb import KATEGORI_ADLARI

log = logging.getLogger(__name__)

# Çok-sayfalı taramada öncelikli alt sayfa yolları. Bunlar dışında derin
# tarama yok — referans sistemin anasayfa-only açığını kapatır ama tüm siteyi
# indirmez. ponytail: ihtiyaç olursa derinlik/liste büyütülür.
_ONCELIKLI = (
    "iletisim", "hakkimizda", "ekip", "sss", "sikca", "anlasma",
    "kurumlar", "sgk", "sigorta", "saat", "hizmet",
)

_SABLON = """\
Sen bir klinik bilgi-ayırıcısısın. Verilen metni bilgi tabanı kayıtlarına ayır.

BAĞLAM — klinik kimliği ve halihazırda bilinenler:
{kimlik}

KURALLAR:
- Her kayıt: {{"baslik": kısa başlık, "icerik": net tek-iki cümle, "kategori": aşağıdakilerden biri}}.
- kategori YALNIZCA şunlardan biri: hizmetler, calisma_saatleri, adres, sss, genel.
- FIYAT ÜRETMİYORSUN. Metinde fiyat/ücret/TL tutarı varsa KESİNLİKLE kayıt oluşturma;
  bunun yerine "uyarilar" listesine "fiyat: ..." diye ekle (fiyatın tek kaynağı
  Fiyat ve Kampanya sayfasıdır).
- Tıbbi teşhis/öneri içeren hiçbir şeyi kayda çevirme.
- Kaynak sitenin marka adı, klinik adı, kişi/doktor ismi veya unvanı kayda GİRMEZ;
  yalnızca genel tedavi/hizmet ve kurum türü bilgisi kalır (örn. "Diş Kliniği A"
  değil "diş kliniği", kişi adı değil "uzman hekim").
- Metinde gerçek bir bilgi yoksa boş bırak; asla uydurma.

ÇIKTI: yalnızca şu biçimde bir JSON nesnesi, başka hiçbir şey yazma:
{{"kayitlar": [...], "uyarilar": [...]}}"""


def _llm_cagir(mesajlar: list[dict]) -> str:
    """Sağlayıcıya bir one-shot çağrı, düz metin döner. Hata fırlatır (rotalar yakalar)."""
    taban, anahtar, model = saglayici()
    if not taban or not anahtar or not model:
        raise RuntimeError("AJAN_PROVIDER / API anahtarı / AJAN_MODEL ayarlı değil")
    yanit = httpx.post(
        f"{taban}/chat/completions",
        headers={"Authorization": f"Bearer {anahtar}"},
        json={"model": model, "messages": mesajlar, "temperature": 0},
        timeout=ZAMAN_ASIMI,
    )
    yanit.raise_for_status()
    return yanit.json()["choices"][0]["message"]["content"].strip()


def _json_ayistir(metin: str) -> dict:
    """LLM çıktısından {{kayitlar, uyarilar}} çeker. Bozuksa boş sözlük."""
    if not metin:
        return {}
    for aday in (metin, *_cekitten(metin)):
        try:
            veri = json.loads(aday)
        except ValueError:
            continue
        if isinstance(veri, dict):
            return veri
    return {}


def _cekitten(metin: str) -> list[str]:
    """```json ... ``` çiti ve gevşek {{...}} bloğu adayları."""
    eslesme = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", metin, re.DOTALL)
    if eslesme:
        yield eslesme.group(1)
    eslesme = re.search(r"\{.*\}", metin, re.DOTALL)
    if eslesme:
        yield eslesme.group(0)


def _temizle(govde: dict) -> tuple[list[dict], list[str]]:
    """LLM sözlüğünü doğrular: kategori sınırla, boşları ele, uyarıları düzelt."""
    kayitlar = []
    for k in govde.get("kayitlar") or []:
        baslik = (k.get("baslik") or "").strip()
        icerik = (k.get("icerik") or "").strip()
        if not baslik or not icerik:
            continue
        kategori = (k.get("kategori") or "genel").strip()
        if kategori not in KATEGORI_ADLARI:  # 'fiyatlar' dahil geçersizler -> genel
            kategori = "genel"
        kayitlar.append({"baslik": baslik, "icerik": icerik, "kategori": kategori})
    uyarilar = [str(u).strip() for u in (govde.get("uyarilar") or []) if str(u).strip()]
    return kayitlar, uyarilar


def _ayir(metin: str, site_mi: bool) -> tuple[list[dict], list[str]]:
    """Ortak LLM çağrısı + temizleme. site_mi yalnızca prompt nüansını değiştirir."""
    kimlik = kimlik_ve_bilgi() or "(klinik bilgisi okunamadı)"
    sistem = _SABLON.format(kimlik=kimlik)
    if site_mi:
        sistem += "\n\nAşağıdaki metin klinik web sitesinden kazınmıştır."
    govde = _json_ayistir(_llm_cagir([
        {"role": "system", "content": sistem},
        {"role": "user", "content": metin},
    ]))
    return _temizle(govde)


def metni_ayristir(metin: str) -> tuple[list[dict], list[str]]:
    """Personelin serbest metnini KB kayıtlarına ayırır. AKTİF eklenecek."""
    return _ayir(metin, site_mi=False)


def site_tara(url: str, derinlik: int = 8) -> tuple[list[dict], list[str]]:
    """Klinik sitesini çok-sayfalı kazıyıp KB kayıtlarına ayırır. PASİF eklenecek."""
    govde_metni, hata = _site_metni(url, derinlik)
    if hata:
        return [], [hata]
    if len(govde_metni) < 50:
        return [], ["siteden anlamlı metin alınamadı"]
    return _ayir(govde_metni, site_mi=True)


def _tag_strip(html: str) -> str:
    """script/style içeriğini at, tag'leri soy, whitespace'i katla."""
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _site_metni(url: str, derinlik: int) -> tuple[str, str | None]:
    """(birleştirilmiş_metin, hata). ponytail: statik HTML, derinlik sayfa sınırı."""
    basliklar = {"User-Agent": "HermesEgitim/1.0"}
    try:
        ana = httpx.get(url, timeout=ZAMAN_ASIMI, follow_redirects=True, headers=basliklar)
        ana.raise_for_status()
    except httpx.HTTPError as e:
        return "", f"site alınamadı: {e}"

    kok = urlparse(url)
    kok_koke = f"{kok.scheme}://{kok.netloc}"  # aynı-domain sınırı
    sayfalar = [ana.text]
    gorulen = {kok.path}

    for href in re.findall(r'href="([^"]+)"', ana.text):
        tam = urljoin(url, href)
        tp = urlparse(tam)
        if f"{tp.scheme}://{tp.netloc}" != kok_koke:  # ponytail: dışarı çıkmıyor
            continue
        if tp.path in gorulen or not any(a in tp.path.lower() for a in _ONCELIKLI):
            continue
        gorulen.add(tp.path)
        try:
            r = httpx.get(tam, timeout=ZAMAN_ASIMI, follow_redirects=True, headers=basliklar)
            r.raise_for_status()
            sayfalar.append(r.text)
        except httpx.HTTPError:
            continue
        if len(sayfalar) >= derinlik:
            break

    metin = "\n\n".join(_tag_strip(s) for s in sayfalar)
    return metin[:20000], None  # ponytail: token şişirmesin diye kırp
