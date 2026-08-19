"""Instagram DM kanalı — Unipile taşıması.

Composio yolunun (aşağıda app/instagram.py yoklaması) yerine geçebilir; seçim
`INSTAGRAM_SAGLAYICI=unipile` env'iyle yapılır — o an Composio IG nöbetçisi
başlamaz. Kapsam aynı ve dar: yalnız bilgilendirme, randevu açmaz, hatırlatma
göndermez (gerekçe app/instagram.py başlığında ve README § Instagram kanalı).

Taşıma katmanındaki fark:
  - Mesajlar 30 saniyede bir yoklanmaz; Unipile `message_received` webhook'u
    iter (`POST /webhook/unipile`, app/main.py). Yoklamaya göre anlık.
  - Canlılık: webhook sessiz kalması normal olan bir kanaldur ("mesaj gelmedi"
    hata değil). Bu yüzden nöbetçi mesaj değil HESAP DURUMUNU yoklar ve sonucu
    mevcut `instagram_yoklama` kalp atışına yazar — saglik.py değişmeden
    çalışır, hesap düşerse alarm çalar.

Env:
  UNIPILE_URL            https://apiXX.unipile.com:PORT
  UNIPILE_ANAHTAR        X-API-KEY değeri
  UNIPILE_HESAP          bağlı IG hesabının account_id'si
  UNIPILE_WEBHOOK_ANAHTAR  Unipile'ın her webhook'unda gönderdiği başlık gizlisi
  INSTAGRAM_HESAP_ID     kendi IG kullanıcı id'miz (giden mesajları ayıklar)
"""

import asyncio
import logging
import os

import httpx

from app.instagram import InstagramHatasi

log = logging.getLogger("unipile")

# Hesap durumu bu aralıkla yoklanır.instagram.yoklamanın 30 sn'siyle aynı
# varsayılan; sağlık nöbetçisinin eşiği bu aralığa göre hesaplanıyor.
ARALIK_SN = int(os.environ.get("INSTAGRAM_ARALIK_SN", "30"))


class UnipileHatasi(InstagramHatasi):
    """Unipile çağrısı başarısız — ağ, yetki ya da hesap hatası.

    InstagramHatasi'dan türetiliyor ki instagram.cevapla'daki gönderim
    hatası yakalama iki taşımada da aynı davransın (giden kaydı kalır).
    """


def yapilandirildi_mi() -> bool:
    """Üç env de doluysa kanal açıktır; eksikse nöbetçi ve webhook sessiz."""
    return bool(os.environ.get("UNIPILE_URL")
                and os.environ.get("UNIPILE_ANAHTAR")
                and os.environ.get("UNIPILE_HESAP"))


def _istek(yontem: str, yol: str, **ek) -> dict:
    anahtar = os.environ.get("UNIPILE_ANAHTAR", "")
    temel = os.environ.get("UNIPILE_URL", "").rstrip("/")
    try:
        y = httpx.request(yontem, f"{temel}{yol}", timeout=30,
                          headers={"X-API-KEY": anahtar, "accept": "application/json"}, **ek)
    except Exception as e:
        raise UnipileHatasi(f"Unipile'a ulaşılamadı: {e}") from e
    if y.status_code >= 300:
        raise UnipileHatasi(f"{yol} HTTP {y.status_code}: {y.text[:300]}")
    return y.json() if y.content else {}


def hesap_durumu() -> tuple[bool, str | None]:
    """Bağlı IG hesabı sağlıklı mı? (OK → sağlıklı; CONNECTING/STOPPED → değil)"""
    veri = _istek("GET", f"/api/v1/accounts/{os.environ.get('UNIPILE_HESAP', '')}")
    durum = veri.get("status", "?")
    if durum == "OK":
        return True, None
    return False, f"hesap durumu {durum}"


def mesaj_gonder(chat_id: str, metin: str) -> str:
    """Instagram DM gönderir (tek alıcı — toplu gönderim mimari olarak yasak)."""
    veri = _istek("POST", f"/api/v1/chats/{chat_id}/messages", json={"text": metin})
    return str(veri.get("id", ""))


def olay_ayikla(olay: dict) -> dict | None:
    """`message_received` webhook gövdesinden cevaplanacak mesajı çıkarır.

    Dönüş: {igsid, chat_id, mesaj_id, metin, ad} ya da None (yoksay).
    Yoksayma sebepleri: başka olay, başka hesap, kendi gönderdiğimiz mesaj,
    boş metin. Unipile gövde biçimi sürümle oynayabilir; anahtar adları
    savunmacı denenir, tanınmayan şekil None döner ve webhook 200 cevaplanır
    (Unipile yeniden denemesin — kötü biçimli olay tekrar da kötü olur).
    """
    ad_ = olay.get("name") or olay.get("event") or olay.get("event_name")
    if ad_ != "message_received":
        return None

    hesap = olay.get("account_id")
    if hesap and hesap != os.environ.get("UNIPILE_HESAP"):
        return None                    # başka hesabın trafiği (tüm hesaplara webhook olsa bile)

    m = olay.get("message") or {}
    metin = str(m.get("text") or "").strip()
    mid = m.get("id")
    gonderen = str(m.get("sender_id") or "")
    if not metin or not mid or not gonderen:
        return None
    # Kendi gönderdiğimiz mesaj: gönderen hesabın kendi IG id'si.
    if gonderen == os.environ.get("INSTAGRAM_HESAP_ID", ""):
        return None

    return {"igsid": gonderen,
            "chat_id": str(olay.get("chat_id") or m.get("chat_id") or ""),
            "mesaj_id": f"ig:{mid}",
            "metin": metin,
            "ad": m.get("sender_name") or None}


async def nobetci(baglan_fn) -> None:
    """Hesap durumu nöbetçisi — FastAPI'nin döngüsünde, doktor gözetiminde.

    Webhook'u tetikleyen trafik olmadığı için "döngü dönüyor mu" değil "hesap
    ayakta mı" sorusunun cevabını kalp atışına yazar; saglik.py'deki mevcut
    instagram kontrolü değişmeden çalışır.
    """
    from app.instagram import kalp_atisi

    while True:
        conn = None
        try:
            conn = baglan_fn()
            try:
                basarili, hata = await asyncio.to_thread(hesap_durumu)
            except UnipileHatasi as e:
                basarili, hata = False, str(e)[:400]
            kalp_atisi(conn, basarili, hata)
            if not basarili:
                log.error("Unipile hesabı sağlıksız: %s", hata)
        except Exception as e:
            log.error("Unipile nöbetçisi hatası: %s", e)
        finally:
            if conn is not None:
                conn.close()
        await asyncio.sleep(ARALIK_SN)
