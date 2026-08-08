"""Sistem doktoru — aksaklıkta otomatik müdahale.

saglik.py bozukluğu TESPİT eder (alarm çalar), doktor ONARIR. İki mekanizma:

1. Görev süpervizörü — yasam'da başlatılan asyncio nöbetçi taskleri
   (saglik / hatirlatma / instagram) burada kayıtlıdır. Biri .done()
   olursa (sert çöküş; nöbetçilerin kendi while-True try/except'i
   yakalamadığı bir durum) süpervizör yeniden doğurur. Güvence katmanı:
   sessizce ölen bir döngü olmasın.
2. onar(servis) — saglik'nin 'uyari' anında çağrılır. Instagram poller
   takılıysa task'i cancel + restart eder (en temiz otomatik onarım).
   WhatsApp / Composio içeriden onarılamaz — ayrı süreç (Chromium) veya
   OAuth gerektirir; dürüstçe 'insan gerek' der ve uyarıya düşer.

Her girişim sistem_onarim tablosuna yazılır; /doktor panelinde görünür.
"""

import asyncio
import logging

from psycopg.rows import dict_row

log = logging.getLogger("doktor")

SUPERVISOR_ARALIK_SN = 60

# ad -> (nobetci_fn, etkin). nobetci_fn: baglan_fn -> coroutine.
_fabrikalar: dict[str, tuple] = {}
_gorevler: dict[str, asyncio.Task] = {}
_baglan_fn = None


def baglan_fn_ayarla(fn) -> None:
    """yasam çağırır (bir kez). Tüm fabrikalar baglan_fn'i böyle alır."""
    global _baglan_fn
    _baglan_fn = fn


def kayit(ad: str, nobetci_fn, etkin: bool = True) -> None:
    """yasam çağırır: kayit('instagram', instagram.nobetci, etkin=...)."""
    _fabrikalar[ad] = (nobetci_fn, etkin)


def gorev_baslat(ad: str) -> bool:
    """Etkinse ve çalışmıyorsa başlatır. True = başlattı/yeni doğurdu."""
    giris = _fabrikalar.get(ad)
    if not giris:
        return False
    nobetci_fn, etkin = giris
    if not etkin or _baglan_fn is None:
        return False
    eski = _gorevler.get(ad)
    if eski and not eski.done():
        return False  # zaten canlı
    _gorevler[ad] = asyncio.create_task(nobetci_fn(_baglan_fn), name=ad)
    return True


def gorev_yeniden_baslat(ad: str) -> bool:
    """Mevcut task'i iptal edip yenisini doğurur. onar('instagram') kullanır."""
    giris = _fabrikalar.get(ad)
    if not giris or _baglan_fn is None:
        return False
    eski = _gorevler.get(ad)
    if eski and not eski.done():
        eski.cancel()
    _gorevler[ad] = asyncio.create_task(giris[0](_baglan_fn), name=ad)
    return True


def gorev_durumu() -> dict[str, dict]:
    """Panel için her görevin canlılık durumu."""
    out = {}
    for ad, (_fn, etkin) in _fabrikalar.items():
        if not etkin:
            out[ad] = {"etkin": False, "canli": False, "crashed": False}
            continue
        t = _gorevler.get(ad)
        out[ad] = {
            "etkin": True,
            "canli": bool(t and not t.done()),
            # bitti ama iptal edilmedi → exception ile çökmüş
            "crashed": bool(t and t.done() and not t.cancelled() and t.exception() is not None),
        }
    return out


async def supervisor() -> None:
    """.done() olan (çökmüş/iptal) nöbetçi görevlerini yeniden doğurur."""
    while True:
        try:
            for ad in list(_fabrikalar):
                t = _gorevler.get(ad)
                if t and t.done():
                    exc = None
                    if not t.cancelled():
                        try:
                            exc = t.exception()
                        except asyncio.InvalidStateError:
                            pass
                    log.warning("Görev çöktü, yeniden doğuruluyor: %s (%s)", ad, exc)
                if gorev_baslat(ad):
                    log.info("Doktor görevi başlattı: %s", ad)
        except Exception as e:  # süpervizör durmamalı
            log.error("Doktor süpervizör hatası: %s", e)
        await asyncio.sleep(SUPERVISOR_ARALIK_SN)


# ── onarım ──────────────────────────────────────────────────

def _kayit_yaz(conn, servis: str, tetikleyici: str, basarili: bool, mesaj: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sistem_onarim (servis, tetikleyici, basarili, mesaj) "
            "VALUES (%s, %s, %s, %s)",
            (servis, tetikleyici, basarili, mesaj[:500]),
        )
    conn.commit()


def onar(conn, servis: str, tetikleyici: str = "otomatik") -> tuple[bool, str]:
    """Bir servis için onarım dener. (başarılı_mı, açıklama).

    tetikleyici: 'otomatik' (saglik uyarısı), 'manuel' (panel düğmesi),
    'supervisor' (görev çöküşünde).
    """
    basarili, mesaj = _dene(servis)
    _kayit_yaz(conn, servis, tetikleyici, basarili, mesaj)
    return basarili, mesaj


def _dene(servis: str) -> tuple[bool, str]:
    # Instagram poller takılı (kalp atışı bayat) → task yeniden doğur.
    # Döngü bir httpx çağrısında askıda kalmış olabilir; cancel+restart temizler.
    if servis == "instagram":
        giris = _fabrikalar.get("instagram")
        if not giris or not giris[1]:  # kasıtlı kapalıysa onarmamalı
            return False, "Instagram kanalı kapalı — .env'de açın (INSTAGRAM_NOBETCISI + yapılandırma)."
        if gorev_yeniden_baslat("instagram"):
            return True, "Instagram yoklama döngüsü yeniden başlatıldı."
        return False, "Instagram döngüsü başlatılamadı (baglan_fn yok)."

    # WhatsApp (OpenWA) ayrı bir Chromium süreci; bu uygulamadan restart
    # edilemez. İçeriden yapılabilecek tek şey teşhis + insan yönlendirmesi.
    if servis == "whatsapp":
        return (False,
                "OpenWA ayrı süreç — uygulamadan restart edilemez. "
                "Paneldeki WhatsApp (QR) bağlantısından oturumu yeniden tara.")

    # Composio OAuth düştüyse tarayıcı gerektir; otomatik bağlanılamaz.
    if servis == "composio":
        return (False,
                "Composio bağlantısı OAuth gerektirir — app.composio.dev'den "
                "yeniden bağlan.")

    return False, f"Bilinmeyen servis '{servis}' — otomatik onarım tanımsız."


def gecmis(conn, limit: int = 30) -> list[dict]:
    """Son onarım girişimleri — /doktor panelinde."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT servis, zaman, tetikleyici, basarili, mesaj "
            "FROM sistem_onarim ORDER BY zaman DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


if __name__ == "__main__":
    # self-check: servis bilinmeyene onarım tanımsız dönmeli (DB'siz dal).
    ok, _ = _dene("instagram")
    assert ok is False, "instagram fabrikası kayıtsızken False dönmeli"
    ok, msg = _dene("whatsapp")
    assert ok is False and "OpenWA" in msg
    ok, msg = _dene("composio")
    assert ok is False and "OAuth" in msg
    ok, _ = _dene("bilinmeyen")
    assert ok is False
    print("doktor self-check OK")
