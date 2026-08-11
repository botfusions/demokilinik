"""OpenWA istemcisi ve webhook imza doğrulaması.

Meta Cloud API'ye geçilirse değişmesi gereken tek dosya burasıdır.
"""

import hashlib
import hmac
import os

import httpx


def _ayar(ad: str, varsayilan: str = "") -> str:
    return os.environ.get(ad, varsayilan)


def imza_dogrula(ham_govde: bytes, imza_basligi: str | None, gizli: str) -> bool:
    """`X-OpenWA-Signature: sha256=<hex>` başlığını HAM gövde üzerinden doğrular.

    Ham gövde şart: JSON'u parse edip yeniden serialize etmek baytları değiştirir
    ve imza tutmaz. Karşılaştırma constant-time.
    """
    if not imza_basligi or not gizli:
        return False
    if not imza_basligi.startswith("sha256="):
        return False

    beklenen = "sha256=" + hmac.new(gizli.encode(), ham_govde, hashlib.sha256).hexdigest()
    return hmac.compare_digest(imza_basligi, beklenen)


def telefon_ayikla(chat_id: str) -> str:
    """'905321112233@c.us' -> '905321112233'

    WhatsApp yeni gizlilik adreslemesinde göndereni numara yerine LID olarak
    veriyor ('253201391558876@lid'). LID'i numaraya çevirmeyi deneriz; olmazsa
    adresi **olduğu gibi** ('...@lid') döndürürüz — kırpıp `@c.us` eklersek
    gönderim 400 döner (canlıda böyle oldu, hastaya cevap gitmedi).
    """
    if chat_id.endswith("@lid"):
        return _lid_numaraya(chat_id)
    return chat_id.split("@", 1)[0]


_LID_ONBELLEK: dict[str, str] = {}


def _lid_numaraya(lid: str) -> str:
    """LID → gerçek numara; çözülemezse LID adresi aynen döner."""
    if lid in _LID_ONBELLEK:
        return _LID_ONBELLEK[lid]
    try:
        with _istemci() as c:
            y = c.get(f"/api/sessions/{oturum_id(c)}/contacts/{lid}")
            y.raise_for_status()
            kimlik = y.json().get("id", "")
    except Exception:                       # rehberde yoksa/uç hata verirse LID ile devam
        kimlik = ""
    sonuc = kimlik.split("@", 1)[0] if kimlik.endswith("@c.us") else lid
    _LID_ONBELLEK[lid] = sonuc
    return sonuc


def _istemci() -> httpx.Client:
    return httpx.Client(
        base_url=_ayar("OPENWA_URL", "http://localhost:2785"),
        headers={"X-Api-Key": _ayar("OPENWA_API_KEY")},
        timeout=30,
    )


_UUID_ONBELLEK: dict[str, str] = {}


def oturum_id(c: httpx.Client | None = None) -> str:
    """Oturum UUID'si. OPENWA_SESSION okunabilir bir ad tutar; API UUID ister.

    Ad→UUID eşlemesi önbelleğe alınır. Oturum silinip yeniden kurulursa UUID değişir;
    o durumda önbellek elle temizlenmez, servis restart'ında zaten yenilenir.
    """
    ad = _ayar("OPENWA_SESSION", "klinik")
    if len(ad) == 36 and ad.count("-") == 4:      # zaten UUID verilmişse dokunma
        return ad
    if ad in _UUID_ONBELLEK:
        return _UUID_ONBELLEK[ad]

    kendi = c is None
    c = c or _istemci()
    try:
        y = c.get("/api/sessions")
        y.raise_for_status()
        for o in y.json():
            if o.get("name") == ad:
                _UUID_ONBELLEK[ad] = o["id"]
                return o["id"]
    finally:
        if kendi:
            c.close()

    raise RuntimeError(f"OpenWA'da '{ad}' adlı oturum yok — dashboard'dan oluşturun")


def _chat_id(telefon: str) -> str:
    """Numaraya '@c.us' ekler; adres zaten tamsa (LID) olduğu gibi bırakır."""
    return telefon if "@" in telefon else f"{telefon}@c.us"


def mesaj_gonder(telefon: str, metin: str) -> str:
    """WhatsApp mesajı gönderir, messageId döner."""
    with _istemci() as c:
        y = c.post(
            f"/api/sessions/{oturum_id(c)}/messages/send-text",
            json={"chatId": _chat_id(telefon), "text": metin},
        )
        y.raise_for_status()
        return y.json().get("messageId", "")


def konum_gonder(telefon: str, enlem: float, boylam: float) -> str:
    """Klinik konumunu iğne olarak gönderir.

    Bu OpenWA sürümü yalnız enlem/boylam kabul ediyor — `name`/`address`
    alanları 400 dönüyor, yani iğnenin üstünde etiket görünmez. Adres metni
    ajanın cevabında ayrıca yazılı olduğu için sorun değil.
    """
    with _istemci() as c:
        y = c.post(
            f"/api/sessions/{oturum_id(c)}/messages/send-location",
            json={"chatId": _chat_id(telefon), "latitude": enlem, "longitude": boylam},
        )
        y.raise_for_status()
        return y.json().get("messageId", "")


def oturum_durumu() -> str:
    """'ready' | 'disconnected' | 'qr_ready' | ... — sağlık nöbetçisi kullanır."""
    with _istemci() as c:
        y = c.get(f"/api/sessions/{oturum_id(c)}")
        y.raise_for_status()
        return y.json().get("status", "bilinmiyor")
