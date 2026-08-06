"""Instagram DM kanalı — YALNIZ BİLGİLENDİRME.

Kapsam bilinçli olarak dar (bkz. README § Instagram kanalı):

  ✓ SSS, fiyat, çalışma saatleri — ajan bilgi tabanından cevaplar
  ✓ Randevu isteyen kişiyi WhatsApp hattına yönlendirir
  ✗ Randevu açmaz            — Instagram'dan randevu kaydı oluşmaz
  ✗ Hatırlatma göndermez     — kendiliğinden mesaj çıkmaz

Bu daraltma keyfi değil, iki teknik zorunluluktan geliyor:

  1. **Composio'nun Instagram araç setinde trigger yok.** Gelen DM'i haber veren
     webhook yok; mesajları biz yokluyoruz. 30 saniyelik gecikme bilgilendirme
     için sorun değil, randevu çakışması için olurdu.
  2. **Instagram'da onaylı şablon mekanizması yok.** 24 saatlik pencere dışında
     Meta serbest metni reddeder. Randevudan 24 saat önce gidecek hatırlatma
     neredeyse her zaman pencere dışında kalır — bu kanalda mümkün değil.

WhatsApp tarafıyla ilişki: Instagram cevapları `gorusmeler.kanal='instagram'`
olarak kaydedilir ve `hatirlatma.giden_sayisi` bunları saymaz. Instagram trafiği
WhatsApp'ın ban koruması tavanını yemez; iki kanalın bütçesi ayrıdır.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
import psycopg

log = logging.getLogger("instagram")

ARALIK_SN = int(os.environ.get("INSTAGRAM_ARALIK_SN", "30"))
# Yoklamada geriye kaç saat bakılır. Servis kısa süre durursa kaçan mesajlar
# yakalanır; uzun duruştan sonra eski mesajlara cevap yağmuru olmaz.
GERIYE_SAAT = int(os.environ.get("INSTAGRAM_GERIYE_SAAT", "6"))
# Bir turda en çok kaç mesaja cevap yazılır. Her cevap bir LLM çağrısı.
TUR_TAVANI = int(os.environ.get("INSTAGRAM_TUR_TAVANI", "5"))

# Araç adları env'den ezilebilir: Composio araç slug'larını değiştirirse kod
# değil .env düzeltilir. Gerçek adları doğrulamak için: python -m app.instagram --kesfet
ARAC_KONUSMALAR = os.environ.get("IG_ARAC_KONUSMALAR", "INSTAGRAM_LIST_ALL_CONVERSATIONS")
ARAC_MESAJLAR = os.environ.get("IG_ARAC_MESAJLAR", "INSTAGRAM_LIST_ALL_MESSAGES")
ARAC_GONDER = os.environ.get("IG_ARAC_GONDER", "INSTAGRAM_SEND_TEXT_MESSAGE")
ARAC_OKUNDU = os.environ.get("IG_ARAC_OKUNDU", "INSTAGRAM_MARK_SEEN")

TEMEL_URL = "https://backend.composio.dev/api/v3"


class InstagramHatasi(Exception):
    """Composio çağrısı başarısız — ağ, yetki ya da araç hatası."""


def yapilandirildi_mi() -> bool:
    """Anahtar yoksa kanal kapalıdır; nöbetçi hiç başlamaz, alarm da çalmaz."""
    return bool(os.environ.get("COMPOSIO_API_KEY") and os.environ.get("INSTAGRAM_KULLANICI"))


def _cagir(arac: str, **argumanlar) -> dict:
    """Composio aracını çalıştırır, `data` alanını döner."""
    anahtar = os.environ.get("COMPOSIO_API_KEY")
    if not anahtar:
        raise InstagramHatasi("COMPOSIO_API_KEY tanımsız")

    govde = {"arguments": argumanlar, "user_id": os.environ.get("INSTAGRAM_KULLANICI", "default")}
    try:
        y = httpx.post(
            f"{TEMEL_URL}/tools/execute/{arac}",
            headers={"x-api-key": anahtar},
            json=govde,
            timeout=45,
        )
    except Exception as e:
        raise InstagramHatasi(f"Composio'ya ulaşılamadı: {e}") from e

    if y.status_code != 200:
        raise InstagramHatasi(f"{arac} HTTP {y.status_code}: {y.text[:300]}")

    sonuc = y.json()
    # Composio HTTP 200 dönüp gövdede başarısızlık bildirebilir; sessizce
    # boş liste sanmamak için açıkça bakılıyor.
    if sonuc.get("successful") is False:
        raise InstagramHatasi(f"{arac} başarısız: {str(sonuc.get('error'))[:300]}")
    return sonuc.get("data") or {}


def _liste(veri, *adaylar: str) -> list:
    """Composio sarmalayıcısının içinden liste çeker.

    Araçların dönüş şekli toolkit sürümüyle değişiyor (bazen düz liste, bazen
    {"data": [...]}, bazen {"conversations": [...]}). Şekle bağımlı kalmamak için
    bilinen anahtarlar sırayla denenir.
    """
    if isinstance(veri, list):
        return veri
    if not isinstance(veri, dict):
        return []
    for ad in adaylar:
        if isinstance(veri.get(ad), list):
            return veri[ad]
    for deger in veri.values():          # tek listeli sarmalayıcı: ilk listeyi al
        if isinstance(deger, list):
            return deger
    return []


def _alan(kayit: dict, *adaylar: str, varsayilan=None):
    for ad in adaylar:
        if kayit.get(ad) not in (None, ""):
            return kayit[ad]
    return varsayilan


# ── Composio çağrıları ──────────────────────────────────────

def konusmalar() -> list[dict]:
    return _liste(_cagir(ARAC_KONUSMALAR), "conversations", "data", "items")


def mesajlar(konusma_id: str) -> list[dict]:
    veri = _cagir(ARAC_MESAJLAR, conversation_id=konusma_id)
    return _liste(veri, "messages", "data", "items")


def mesaj_gonder(igsid: str, metin: str) -> str:
    """Instagram DM gönderir, mesaj id'sini döner."""
    veri = _cagir(ARAC_GONDER, recipient_id=igsid, message=metin)
    return str(_alan(veri, "message_id", "id", "mid", varsayilan=""))


def okundu_isaretle(igsid: str) -> None:
    try:
        _cagir(ARAC_OKUNDU, recipient_id=igsid)
    except InstagramHatasi as e:
        log.info("Okundu işaretlenemedi (%s): %s", igsid, e)   # kritik değil


# ── yoklama ─────────────────────────────────────────────────

def kisi_anahtari(igsid: str) -> str:
    """Instagram kullanıcısı `kisiler.telefon` alanında 'ig:<IGSID>' olarak durur.

    ponytail: şema değiştirmemek için telefon sütunu genel adres olarak kullanılıyor.
    Randevu ve hatırlatma bu kanalda kapalı olduğundan gerçek telefon zaten
    gerekmiyor. Instagram'dan randevu alınacak olursa doğru çözüm sütunu
    (kanal, adres) çiftine çevirmektir — o zaman burası kalkar.
    """
    return f"ig:{igsid}"


def _gelen_mi(m: dict, kendi_id: str | None) -> bool:
    """Mesaj karşı taraftan mı geldi? Kendi cevaplarımıza cevap yazmamak için."""
    gonderen = _alan(m, "from", "sender_id", "from_id")
    if isinstance(gonderen, dict):
        gonderen = _alan(gonderen, "id", "user_id")
    if gonderen is None:
        return False
    return not (kendi_id and str(gonderen) == str(kendi_id))


def _zaman(m: dict) -> datetime | None:
    ham = _alan(m, "created_time", "timestamp", "created_at")
    if ham is None:
        return None
    try:
        if isinstance(ham, (int, float)):
            return datetime.fromtimestamp(float(ham) / (1000 if ham > 1e11 else 1), timezone.utc)
        return datetime.fromisoformat(str(ham).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


def yeni_mesajlar() -> list[dict]:
    """Cevaplanacak gelen DM'ler: [{igsid, mesaj_id, metin, ad}]

    Tekrar cevap koruması burada DEĞİL, veritabanında: `gorusmeler.wa_message_id`
    tekil indeksi aynı mesaj id'sini ikinci kez kabul etmiyor.
    """
    kendi = os.environ.get("INSTAGRAM_HESAP_ID")
    sinir = datetime.now(timezone.utc) - timedelta(hours=GERIYE_SAAT)
    bulunan = []

    for k in konusmalar():
        kid = _alan(k, "id", "conversation_id")
        if not kid:
            continue
        try:
            liste = mesajlar(str(kid))
        except InstagramHatasi as e:
            log.warning("Konuşma okunamadı (%s): %s", kid, e)
            continue

        for m in liste:
            if not _gelen_mi(m, kendi):
                continue
            an = _zaman(m)
            if an and an < sinir:
                continue
            metin = (_alan(m, "message", "text", "body") or "").strip()
            mid = _alan(m, "id", "message_id", "mid")
            if not metin or not mid:
                continue

            gonderen = _alan(m, "from", "sender_id", "from_id")
            ad = gonderen.get("username") or gonderen.get("name") if isinstance(gonderen, dict) else None
            igsid = gonderen.get("id") if isinstance(gonderen, dict) else gonderen

            bulunan.append({"igsid": str(igsid), "mesaj_id": f"ig:{mid}",
                            "metin": metin, "ad": ad})

    return bulunan


def tur_calistir(conn: psycopg.Connection, gonder_fn=None) -> int:
    """Bir yoklama turu. Cevaplanan mesaj sayısını döner."""
    from app import ajan
    from app.crm import gorusme_ekle, gorusme_gecmisi, kisi_upsert

    gonder_fn = gonder_fn or mesaj_gonder
    cevaplanan = 0

    for m in yeni_mesajlar():
        if cevaplanan >= TUR_TAVANI:
            log.info("Tur tavanı (%d) doldu, kalanlar sonraki tura", TUR_TAVANI)
            break

        kid = kisi_upsert(conn, kisi_anahtari(m["igsid"]), m["ad"])

        # Tekrar teslimat kilidi: aynı mesaj id'si ikinci kez None döner.
        if gorusme_ekle(conn, kid, "gelen", m["metin"],
                        wa_message_id=m["mesaj_id"], kanal="instagram") is None:
            continue

        gecmis = gorusme_gecmisi(conn, kid, limit=10)[:-1]
        try:
            yanit, maliyet = ajan.cevap_uret(gecmis, m["metin"], kanal="instagram")
        except ajan.CevapUretilemedi as e:
            log.error("Ajan cevap üretemedi (%s): %s", m["igsid"], e)
            continue     # WhatsApp'taki gibi hazır metin göndermiyoruz: bu kanalda
                         # sessiz kalmak, yanlış beklenti yaratmaktan iyi

        # Instagram'da konum iğnesi yok (Composio araç setinde karşılığı yok);
        # işaret yine de ayıklanmalı, yoksa hastaya ham "[KONUM]" yazısı gider.
        yanit, _ = ajan.konum_ayikla(yanit)

        # Önce kaydet sonra gönder — Instagram'a ulaşılamasa da personel panelde görür
        gorusme_ekle(conn, kid, "giden", yanit, maliyet_usd=maliyet, kanal="instagram")
        try:
            gonder_fn(m["igsid"], yanit)
        except InstagramHatasi as e:
            log.error("Instagram mesajı gönderilemedi (%s): %s", m["igsid"], e)
            continue
        okundu_isaretle(m["igsid"])
        cevaplanan += 1

    return cevaplanan


def kalp_atisi(conn: psycopg.Connection, basarili: bool, hata: str | None = None) -> None:
    """Turun canlılığını `baglanti_saglik`'e yazar.

    Composio bağlantısı ACTIVE kalıp bu döngü sessizce ölürse hiçbir alarm
    çalmazdı; DM'ler cevapsız birikirdi. Nöbetçi bu satırın yaşını okuyor
    (`saglik._instagram_yoklama_kontrol`).
    """
    from app.saglik import kontrol_sonucu_isle

    kontrol_sonucu_isle(conn, "instagram_yoklama", basarili, hata)


async def nobetci(baglan_fn) -> None:
    """FastAPI'nin kendi döngüsünde çalışır — ayrı scheduler paketi yok."""
    import asyncio

    while True:
        conn = None
        try:
            conn = baglan_fn()
            n = await asyncio.to_thread(tur_calistir, conn)
            if n:
                log.info("%d Instagram mesajı cevaplandı", n)
            kalp_atisi(conn, True)
        except Exception as e:
            log.error("Instagram nöbetçisi hatası: %s", e)
            if conn is not None:
                try:
                    kalp_atisi(conn, False, str(e)[:400])
                except Exception:
                    pass      # kalp atışı yazılamadıysa nöbetçi yine de dönmeli
        finally:
            if conn is not None:
                conn.close()

        await asyncio.sleep(ARALIK_SN)


# ── keşif ───────────────────────────────────────────────────

def _kesfet() -> None:
    """Composio'daki gerçek Instagram araç adlarını ve şemalarını basar.

    Araç slug'ları ve argüman adları belgelenmiş değil; anahtar geldiğinde bu
    komutla doğrulanır, gerekiyorsa .env'deki IG_ARAC_* satırları düzeltilir.
    """
    import json

    anahtar = os.environ.get("COMPOSIO_API_KEY")
    if not anahtar:
        raise SystemExit("COMPOSIO_API_KEY tanımsız")

    y = httpx.get(f"{TEMEL_URL}/tools", headers={"x-api-key": anahtar},
                  params={"toolkit_slug": "instagram", "limit": 100}, timeout=45)
    y.raise_for_status()
    for a in _liste(y.json(), "items", "data"):
        ad = a.get("slug") or a.get("name")
        if not ad or "MESSAGE" not in ad.upper() and "CONVERSATION" not in ad.upper() \
           and "SEEN" not in ad.upper():
            continue
        girdi = (a.get("input_parameters") or a.get("parameters") or {}).get("properties", {})
        print(f"\n{ad}\n  {a.get('description', '')[:120]}")
        print(f"  argümanlar: {json.dumps(list(girdi), ensure_ascii=False)}")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    if "--kesfet" in sys.argv:
        _kesfet()
    else:
        from app.db import baglan

        c = baglan()
        try:
            print(f"{tur_calistir(c)} mesaj cevaplandı")
        finally:
            c.close()
