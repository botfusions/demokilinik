"""Bağlantı sağlık nöbetçisi.

İki yönde de bozulabilir: sessiz kalıp kopmayı kaçırmak, ya da her kontrolde mail
atıp uyarıyı gürültüye çevirmek. Bu yüzden uyarı ikinci ardışık hatada çıkar ve
düzelene kadar tekrarlanmaz.
"""

import asyncio
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
import psycopg

log = logging.getLogger("saglik")

ARALIK_SN = int(os.environ.get("SAGLIK_ARALIK_SN", "600"))
UYARI_ESIGI = 2

# Aynı VPS'te birden çok klinik instance'ı çalışıyor (bkz. docker-compose,
# her klinik kendi stack'i) — tek bir Telegram/mail'den hepsi izlenebiliyorsa
# hangi klinikten geldiği etikette belli olmalı.
KLINIK_ADI = os.environ.get("KLINIK_ADI", "Klinik Paneli")


def kontrol_sonucu_isle(
    conn: psycopg.Connection, servis: str, basarili: bool, hata: str | None = None
) -> str | None:
    """Sonucu kaydeder ve gereken aksiyonu döner: None | 'uyari' | 'duzeldi'."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ardisik_hata, uyari_gonderildi FROM baglanti_saglik WHERE servis = %s",
            (servis,),
        )
        satir = cur.fetchone()
        ardisik, uyarildi = satir if satir else (0, False)

        if basarili:
            cur.execute(
                """
                INSERT INTO baglanti_saglik
                    (servis, durum, son_kontrol, son_basarili, hata, ardisik_hata, uyari_gonderildi)
                VALUES (%s, 'saglikli', now(), now(), NULL, 0, false)
                ON CONFLICT (servis) DO UPDATE SET
                    durum = 'saglikli', son_kontrol = now(), son_basarili = now(),
                    hata = NULL, ardisik_hata = 0, uyari_gonderildi = false
                """,
                (servis,),
            )
            conn.commit()
            return "duzeldi" if uyarildi else None

        yeni_ardisik = ardisik + 1
        uyari_zamani = yeni_ardisik >= UYARI_ESIGI and not uyarildi

        cur.execute(
            """
            INSERT INTO baglanti_saglik
                (servis, durum, son_kontrol, hata, ardisik_hata, uyari_gonderildi)
            VALUES (%s, 'bozuk', now(), %s, %s, %s)
            ON CONFLICT (servis) DO UPDATE SET
                durum = 'bozuk', son_kontrol = now(), hata = EXCLUDED.hata,
                ardisik_hata = EXCLUDED.ardisik_hata,
                uyari_gonderildi = baglanti_saglik.uyari_gonderildi OR EXCLUDED.uyari_gonderildi
            """,
            (servis, hata, yeni_ardisik, uyari_zamani),
        )
    conn.commit()
    return "uyari" if uyari_zamani else None


def saglik_ozeti(conn: psycopg.Connection) -> list[dict]:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        # instagram_yoklama iç bir kalp atışı satırı; personele gösterilecek şey
        # 'instagram' satırı — nöbetçi zaten kalp atışının yaşını oraya yansıtıyor.
        cur.execute(
            "SELECT * FROM baglanti_saglik WHERE servis <> 'instagram_yoklama' ORDER BY servis"
        )
        return cur.fetchall()


def uyari_maili_gonder(konu: str, govde: str) -> None:
    alici = os.environ.get("YONETICI_EPOSTA")
    sunucu = os.environ.get("SMTP_HOST")
    if not alici or not sunucu:
        log.warning("Uyarı maili atlanıyor (YONETICI_EPOSTA/SMTP_HOST yok): %s", konu)
        return

    m = EmailMessage()
    m["Subject"] = konu
    m["From"] = os.environ.get("SMTP_KULLANICI", alici)
    m["To"] = alici
    m.set_content(govde)

    try:
        with smtplib.SMTP(sunucu, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
            s.starttls()
            if os.environ.get("SMTP_KULLANICI"):
                s.login(os.environ["SMTP_KULLANICI"], os.environ.get("SMTP_PAROLA", ""))
            s.send_message(m)
    except Exception as e:  # mail gidemezse nöbetçi durmamalı
        log.error("Uyarı maili gönderilemedi: %s", e)


def uyari_telegram_gonder(mesaj: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Uyarı Telegram atlanıyor (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID yok)")
        return

    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": mesaj},
            timeout=20,
        )
    except Exception as e:  # Telegram'a ulaşılamazsa nöbetçi durmamalı
        log.error("Uyarı Telegram gönderilemedi: %s", e)


# ── kontroller ──────────────────────────────────────────────

def _whatsapp_kontrol() -> tuple[bool, str | None]:
    from app.openwa import oturum_durumu

    try:
        durum = oturum_durumu()
    except Exception as e:
        return False, f"OpenWA'ya ulaşılamadı: {e}"
    return (durum == "ready"), (None if durum == "ready" else f"oturum durumu: {durum}")


def _composio_kontrol() -> tuple[bool, str | None]:
    anahtar = os.environ.get("COMPOSIO_API_KEY")
    if not anahtar:
        return True, None  # yapılandırılmamış servis için alarm çalmaz

    try:
        y = httpx.get(
            "https://backend.composio.dev/api/v3/connected_accounts",
            headers={"x-api-key": anahtar},
            timeout=20,
        )
    except Exception as e:
        return False, f"Composio'ya ulaşılamadı: {e}"

    if y.status_code != 200:
        return False, f"Composio HTTP {y.status_code}"

    hesaplar = y.json().get("items", y.json().get("data", []))
    kopuk = [h for h in hesaplar if h.get("status") not in ("ACTIVE", "active", None)]
    if kopuk:
        adlar = ", ".join(str(h.get("toolkit", h.get("appName", "?"))) for h in kopuk)
        return False, f"bağlantı düştü: {adlar}"
    return True, None


def _instagram_yoklama_kontrol(conn: psycopg.Connection) -> tuple[bool, str | None]:
    """Instagram yoklama döngüsü hâlâ dönüyor mu?

    `_composio_kontrol` bağlantının sağlığına bakar; bağlantı ACTIVE kalıp
    döngünün kendisi ölebilir (yakalanmamış hata, askıda kalan istek). O durumda
    hiçbir alarm çalmaz ve DM'ler cevapsız birikir. Burada döngünün kendi yazdığı
    kalp atışının yaşına bakılıyor.
    """
    from app.instagram import ARALIK_SN as YOKLAMA_ARALIK, yapilandirildi_mi

    if not yapilandirildi_mi():
        return True, None                  # kanal kapalıysa alarm çalmaz

    # Üç tur kaçırmak tolere edilir: tek yavaş tur (LLM çağrıları) alarm çalmasın.
    esik = max(YOKLAMA_ARALIK * 3, 180)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT son_basarili, hata FROM baglanti_saglik WHERE servis = 'instagram_yoklama'"
        )
        satir = cur.fetchone()

    if not satir or satir[0] is None:
        # Servis yeni açıldıysa ilk tur henüz yazmamış olabilir; ilk turdan
        # sonra bu dal bir daha çalışmaz.
        return False, "yoklama hiç çalışmadı"

    yas = (datetime.now(timezone.utc) - satir[0]).total_seconds()
    if yas > esik:
        son_hata = f" — son hata: {satir[1]}" if satir[1] else ""
        return False, f"yoklama {int(yas)} sn'dir tur atmadı (eşik {esik} sn){son_hata}"
    return True, None


async def nobetci(baglan_fn) -> None:
    """FastAPI'nin kendi döngüsünde çalışır — ayrı scheduler paketi yok."""
    while True:
        try:
            conn = baglan_fn()
            try:
                for servis, kontrol in (("whatsapp", _whatsapp_kontrol),
                                        ("composio", _composio_kontrol),
                                        ("instagram", _instagram_yoklama_kontrol)):
                    # instagram kontrolü veritabanını okur, diğerleri ağa çıkar
                    if servis == "instagram":
                        basarili, hata = await asyncio.to_thread(kontrol, conn)
                    else:
                        basarili, hata = await asyncio.to_thread(kontrol)
                    aksiyon = kontrol_sonucu_isle(conn, servis, basarili, hata)

                    if aksiyon == "uyari":
                        # Önce sistem doktoru denesin: otomatik onarılabilirse
                        # (ör. Instagram poller restart) 'koptu' alarmı yumuşar.
                        from app import doktor  # geç import: döngü yok
                        try:
                            onarildi, onarim_mesaji = doktor.onar(conn, servis, "otomatik")
                        except Exception as e:
                            onarildi, onarim_mesaji = False, f"onarım hatası: {e}"
                        if onarildi:
                            konu = f"[{KLINIK_ADI}] {servis} otomatik onarıldı"
                            govde = (
                                f"{servis} bozuktu; sistem doktoru müdahale etti:\n\n"
                                f"{onarim_mesaji}\n\nBir sonraki kontrol doğrulayacak."
                            )
                        else:
                            konu = f"[{KLINIK_ADI}] {servis} bağlantısı koptu"
                            govde = (
                                f"{servis} bağlantısı iki ardışık kontrolde başarısız.\n\n"
                                f"Hata: {hata}\n\n"
                                f"Otomatik onarım denendi: {onarim_mesaji}\n\n"
                                f"Panelden 'Doktor' bölümüne bakın."
                            )
                        uyari_maili_gonder(konu, govde)
                        uyari_telegram_gonder(f"{konu}\n\n{govde}")
                    elif aksiyon == "duzeldi":
                        konu = f"[{KLINIK_ADI}] {servis} bağlantısı geri geldi"
                        govde = f"{servis} yeniden çalışıyor."
                        uyari_maili_gonder(konu, govde)
                        uyari_telegram_gonder(f"{konu}\n\n{govde}")
            finally:
                conn.close()
        except Exception as e:
            log.error("Sağlık nöbetçisi hatası: %s", e)

        await asyncio.sleep(ARALIK_SN)
