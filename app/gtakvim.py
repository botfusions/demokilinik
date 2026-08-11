"""Google Takvim — randevu açılınca etkinlik, iptal olunca silinir.

**Neden davetli modeli:** Klinik tek bir Google hesabı bağlar (Composio), etkinlik
o hesabın takvimine yazılır ve doktorun e-postası **davetli** olarak eklenir.
Google davetliye etkinliği kendi takvimine düşürür — doktor kendi telefonunda,
kendi rengiyle görür. Doktor tarafında kurulum yok: ayrı OAuth, ayrı takvim,
abonelik yok. (Composio'nun Takvim araçlarında `colorId` alanı olmadığı için
"her hekime bir renk" yolu zaten kapalı; renk seçimi doktorun kendi telefonunda.)

**KVKK — veri minimizasyonu.** Etkinlik açıklamasına hastanın serbest metin notu
(`randevular.notlar`, şikayet/semptom içerebilir) girmez; `app/bildirim.py` ile
aynı kural. Yalnız randevuyu yönetmek için gerekli asgari bilgi yazılır.

**Takvim hatası randevuyu düşürmez.** Composio'ya ulaşılamazsa CRM kaydı
geçerlidir, personel panelde görür; hastaya normal onay gider.
"""

import logging
import os
from datetime import datetime

import httpx
import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

TEMEL_URL = "https://backend.composio.dev/api/v3"

# Araç adları `python -m app.gtakvim --kesfet` ile doğrulandı (2026-08-11).
ARAC_OLUSTUR = os.environ.get("TAKVIM_ARAC_OLUSTUR", "GOOGLECALENDAR_CREATE_EVENT")
ARAC_SIL = os.environ.get("TAKVIM_ARAC_SIL", "GOOGLECALENDAR_DELETE_EVENT")


class TakvimHatasi(Exception):
    """Composio çağrısı başarısız — ağ, yetki ya da araç hatası."""


def yapilandirildi_mi() -> bool:
    """Anahtar ya da kullanıcı yoksa takvim tamamen kapalıdır (testler dahil)."""
    return bool(os.environ.get("COMPOSIO_API_KEY") and os.environ.get("TAKVIM_KULLANICI"))


def _cagir(arac: str, **argumanlar) -> dict:
    anahtar = os.environ.get("COMPOSIO_API_KEY")
    if not anahtar:
        raise TakvimHatasi("COMPOSIO_API_KEY tanımsız")

    govde = {"arguments": argumanlar, "user_id": os.environ.get("TAKVIM_KULLANICI", "default")}
    try:
        y = httpx.post(f"{TEMEL_URL}/tools/execute/{arac}",
                       headers={"x-api-key": anahtar}, json=govde, timeout=45)
    except Exception as e:
        raise TakvimHatasi(f"Composio'ya ulaşılamadı: {e}") from e

    if y.status_code != 200:
        raise TakvimHatasi(f"{arac} HTTP {y.status_code}: {y.text[:300]}")

    sonuc = y.json()
    # Composio HTTP 200 dönüp gövdede başarısızlık bildirebilir.
    if sonuc.get("successful") is False:
        raise TakvimHatasi(f"{arac} başarısız: {str(sonuc.get('error'))[:300]}")
    return sonuc.get("data") or {}


def _etkinlik_id(veri: dict) -> str:
    """Composio sarmalayıcısı sürümüne göre id'yi farklı yerde döndürüyor."""
    for kaynak in (veri, veri.get("response_data") or {}, veri.get("event") or {}):
        if isinstance(kaynak, dict) and kaynak.get("id"):
            return str(kaynak["id"])
    return ""


def _sure(baslangic: datetime, bitis: datetime) -> tuple[int, int]:
    """Araç saat ve dakikayı ayrı istiyor; dakika alanı 0-59 ile sınırlı."""
    toplam = max(int((bitis - baslangic).total_seconds() // 60), 1)
    return toplam // 60, toplam % 60


def etkinlik_olustur(ozet: str, baslangic: datetime, bitis: datetime,
                     aciklama: str = "", davetli: str | None = None) -> str:
    """Etkinliği yazar, Google event id döner. Hata olursa `TakvimHatasi`."""
    saat, dakika = _sure(baslangic, bitis)
    arg = {
        # Araç "offsetsiz, Z'siz" naive zaman istiyor; dilimi ayrı alandan alıyor.
        "start_datetime": baslangic.strftime("%Y-%m-%dT%H:%M:%S"),
        "event_duration_hour": saat,
        "event_duration_minutes": dakika,
        "timezone": os.environ.get("TZ", "Europe/Istanbul"),
        "calendar_id": os.environ.get("GOOGLE_TAKVIM_ID", "primary"),
        "summary": ozet,
        "description": aciklama,
    }
    if davetli:
        arg["attendees"] = [davetli]
        arg["send_updates"] = True      # doktorun takvimine düşsün, haberi olsun
    return _etkinlik_id(_cagir(ARAC_OLUSTUR, **arg))


def etkinlik_sil(event_id: str) -> None:
    _cagir(ARAC_SIL, event_id=event_id,
           calendar_id=os.environ.get("GOOGLE_TAKVIM_ID", "primary"))


# ── randevu köprüsü ─────────────────────────────────────────

def _randevu_ozeti(conn: psycopg.Connection, randevu_id: int) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.hizmet, r.baslangic, r.bitis, r.acil, r.google_event_id,
                   d.ad AS doktor_ad, d.eposta AS doktor_eposta,
                   k.ad AS hasta_ad, k.telefon AS hasta_telefon
              FROM randevular r
              JOIN kisiler k ON k.id = r.kisi_id
              LEFT JOIN doktorlar d ON d.id = r.doktor_id
             WHERE r.id = %s
            """,
            (randevu_id,),
        )
        return cur.fetchone()


def randevuyu_takvime_yaz(conn: psycopg.Connection, randevu_id: int) -> str | None:
    """Yeni randevuyu takvime yazar, event id'yi kaydeder. Hata yutulur, loglanır."""
    if not yapilandirildi_mi():
        return None

    r = _randevu_ozeti(conn, randevu_id)
    if r is None:
        return None

    hasta = r["hasta_ad"] or "İsimsiz hasta"
    ozet = f"{r['hizmet']} — {hasta}"
    if r["acil"]:
        ozet = "ACİL · " + ozet
    # Notlar bilerek yok (KVKK); telefon randevuyu yönetmek için gerekli asgari veri.
    aciklama = f"Hasta: {hasta}\nTelefon: {r['hasta_telefon']}"
    if r["doktor_ad"]:
        aciklama += f"\nHekim: {r['doktor_ad']}"

    try:
        event_id = etkinlik_olustur(ozet, r["baslangic"], r["bitis"], aciklama,
                                    davetli=r["doktor_eposta"])
    except TakvimHatasi as e:
        log.warning("Randevu #%s takvime yazılamadı: %s", randevu_id, e)
        return None

    if event_id:
        with conn.cursor() as cur:
            cur.execute("UPDATE randevular SET google_event_id = %s WHERE id = %s",
                        (event_id, randevu_id))
        conn.commit()
    return event_id or None


def randevuyu_takvimden_sil(conn: psycopg.Connection, randevu_id: int) -> None:
    """İptal edilen randevunun etkinliğini siler. Hata yutulur, loglanır."""
    if not yapilandirildi_mi():
        return

    r = _randevu_ozeti(conn, randevu_id)
    if not r or not r["google_event_id"]:
        return

    try:
        etkinlik_sil(r["google_event_id"])
    except TakvimHatasi as e:
        log.warning("Randevu #%s takvimden silinemedi: %s", randevu_id, e)
        return

    with conn.cursor() as cur:
        cur.execute("UPDATE randevular SET google_event_id = NULL WHERE id = %s", (randevu_id,))
    conn.commit()


def _kesfet() -> None:
    """Composio'daki gerçek Takvim araç adlarını basar (bkz. app/instagram.py)."""
    import json

    anahtar = os.environ.get("COMPOSIO_API_KEY")
    if not anahtar:
        raise SystemExit("COMPOSIO_API_KEY tanımsız")

    y = httpx.get(f"{TEMEL_URL}/tools", headers={"x-api-key": anahtar},
                  params={"toolkit_slug": "googlecalendar", "limit": 100}, timeout=45)
    y.raise_for_status()
    veri = y.json()
    for a in (veri.get("items") or veri.get("data") or []):
        ad = a.get("slug") or a.get("name") or ""
        if "EVENT" in ad.upper():
            print(ad, json.dumps((a.get("input_parameters") or {}).get("required"), ensure_ascii=False))


if __name__ == "__main__":
    import sys
    if "--kesfet" in sys.argv:
        _kesfet()
    else:
        print("kullanım: python -m app.gtakvim --kesfet")
