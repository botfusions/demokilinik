"""Kişi, görüşme ve randevu işlemleri.

Randevu kuralları burada tek yerde duruyor: hem panelden elle girilen hem de ajanın
iç API üzerinden açtığı randevu aynı kontrollerden geçer. Ajanın kendi kararına
bırakılan bir doluluk kontrolü, er ya da geç iki hastayı aynı saate koyar.
"""

import os
from datetime import datetime, time

import psycopg
from psycopg.rows import dict_row

from app import gtakvim


class RandevuCakismasi(Exception):
    """İstenen aralık dolu."""


class GecmisTarih(Exception):
    """Geçmişe randevu açılamaz."""


class CalismaSaatiDisi(Exception):
    """Klinik o gün/saat kapalı."""


class DoktorYok(Exception):
    """Verilen doktor kayıtlı değil ya da pasif."""


# ── kişiler ─────────────────────────────────────────────────

def kisi_upsert(conn: psycopg.Connection, telefon: str, ad: str | None = None) -> int:
    """Telefona göre kişiyi bulur ya da açar; her çağrıda son_temas'ı günceller.

    `ad` yalnızca doluysa yazılır — ikinci mesajda ad gelmiyor diye kayıtlı isim silinmez.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kisiler (telefon, ad) VALUES (%s, %s)
            ON CONFLICT (telefon) DO UPDATE
                SET son_temas = now(),
                    ad = COALESCE(EXCLUDED.ad, kisiler.ad)
            RETURNING id
            """,
            (telefon, ad),
        )
        kid = cur.fetchone()[0]
    conn.commit()
    return kid


def kisi_bul(conn: psycopg.Connection, telefon: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM kisiler WHERE telefon = %s", (telefon,))
        return cur.fetchone()


def kisiler_listele(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM kisiler ORDER BY son_temas DESC")
        return cur.fetchall()


def kisi_sil(conn: psycopg.Connection, kisi_id: int) -> str | None:
    """Hastayı ve tüm izini (görüşmeler, randevular, hatırlatmalar) siler.

    Gelecek randevuların takvim etkinlikleri de silinir; DB satırları kisi
    cascade'iyle gider. Silinen hastanın adını döner (işlem kaydı için),
    hasta yoksa None.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT ad, telefon FROM kisiler WHERE id = %s", (kisi_id,))
        k = cur.fetchone()
        if k is None:
            return None
        cur.execute(
            "SELECT id FROM randevular WHERE kisi_id = %s AND baslangic > now() "
            "AND google_event_id IS NOT NULL", (kisi_id,))
        ridler = [r["id"] for r in cur.fetchall()]
    for rid in ridler:
        gtakvim.randevuyu_takvimden_sil(conn, rid)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM kisiler WHERE id = %s", (kisi_id,))
    conn.commit()
    return k["ad"] or k["telefon"]


def personel_notu_yaz(conn: psycopg.Connection, kisi_id: int, not_: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE kisiler SET personel_notu = %s WHERE id = %s", (not_, kisi_id))
    conn.commit()


# ── insan devri ─────────────────────────────────────────────

def devir_yaz(conn: psycopg.Connection, kisi_id: int, zaman, neden: str | None = None) -> None:
    """`None` devri bitirir; `now()` başlatır ya da K1 sayacını tazeler.

    Kolon tek başına hem "devir açık mı" hem "son personel teması ne zaman"
    sorusunu taşır — panel mesaj gönderince değeri now() yapılır, otomatik
    geri alma sayacı oradan işler. `neden` yalnızca devri açarken yazılır;
    sayaç tazelemede (neden=None) mevcut neden korunur.
    """
    with conn.cursor() as cur:
        if zaman is None:
            cur.execute(
                "UPDATE kisiler SET insan_devri_at = NULL, devir_nedeni = NULL WHERE id = %s",
                (kisi_id,),
            )
        else:
            cur.execute(
                "UPDATE kisiler SET insan_devri_at = %s, "
                "devir_nedeni = coalesce(%s, devir_nedeni) WHERE id = %s",
                (zaman, neden, kisi_id),
            )
    conn.commit()


def devirdekiler(conn: psycopg.Connection) -> list[dict]:
    """Devri açık kişiler — panel sayacı ve otomatik geri alma aynı listeyi tarar."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM kisiler WHERE insan_devri_at IS NOT NULL ORDER BY insan_devri_at")
        return cur.fetchall()


# ── görüşmeler ──────────────────────────────────────────────

def gorusme_ekle(
    conn: psycopg.Connection,
    kisi_id: int,
    yon: str,
    mesaj: str,
    wa_message_id: str | None = None,
    maliyet_usd: float | None = None,
    giris_token: int | None = None,
    cikis_token: int | None = None,
    kanal: str = "whatsapp",
) -> int | None:
    """Görüşmeyi kaydeder. Aynı wa_message_id ikinci kez gelirse None döner.

    OpenWA teslimatı at-least-once; aynı mesaj için iki kez cevap yazmamak
    tekrar teslimatın burada durdurulmasına bağlı. Instagram yoklaması da aynı
    kilidi kullanıyor: orada her turda aynı mesajlar tekrar okunur, ikinci kez
    cevap yazılmamasını bu tekil indeks sağlar.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gorusmeler
                (kisi_id, yon, mesaj, wa_message_id, maliyet_usd, giris_token, cikis_token, kanal)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (wa_message_id) DO NOTHING
            RETURNING id
            """,
            (kisi_id, yon, mesaj, wa_message_id, maliyet_usd, giris_token, cikis_token, kanal),
        )
        satir = cur.fetchone()
    conn.commit()
    return satir[0] if satir else None


def gorusme_gecmisi(conn: psycopg.Connection, kisi_id: int, limit: int = 10) -> list[dict]:
    """Son `limit` görüşme, eskiden yeniye — prompt'a bu sırayla girer."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT * FROM (
                SELECT * FROM gorusmeler WHERE kisi_id = %s ORDER BY id DESC LIMIT %s
            ) s ORDER BY id
            """,
            (kisi_id, limit),
        )
        return cur.fetchall()


# ── ayarlar ─────────────────────────────────────────────────

# Panelden değiştirilebilen ayarlar. DB tek doğru kaynak; okuma yolu ise hâlâ
# `os.environ` — açılışta ve her kayıtta DB'den env'e basılıyor. Böylece
# `_calisma_penceresi()`'nin beş çağrı yerinden hiçbirine `conn` taşımak
# gerekmiyor.
# ponytail: tek süreç varsayımı. Birden çok uvicorn worker'ı açılırsa kayıt
# yalnız kendi sürecinin env'ini günceller — o gün her worker'ın DB'den okuması
# ya da restart gerekir.
AYAR_ENV = {"calisma_gunleri": "CALISMA_GUNLERI", "calisma_saatleri": "CALISMA_SAATLERI"}


def ayarlari_yukle(conn: psycopg.Connection) -> None:
    """DB'deki ayarları process env'ine basar. Açılışta bir kez çağrılır."""
    with conn.cursor() as cur:
        cur.execute("SELECT anahtar, deger FROM ayarlar")
        for anahtar, deger in cur.fetchall():
            if (env_adi := AYAR_ENV.get(anahtar)):
                os.environ[env_adi] = deger


def ayar_yaz(conn: psycopg.Connection, anahtar: str, deger: str) -> None:
    """Ayarı DB'ye yazar ve aynı anda env'e basar — restart beklemeden geçerli."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ayarlar (anahtar, deger) VALUES (%s, %s)
            ON CONFLICT (anahtar) DO UPDATE SET deger = EXCLUDED.deger
            """,
            (anahtar, deger),
        )
    conn.commit()
    if (env_adi := AYAR_ENV.get(anahtar)):
        os.environ[env_adi] = deger


# ── çalışma saatleri ────────────────────────────────────────

def _calisma_penceresi() -> tuple[set[int], time, time]:
    gunler = {int(g) for g in os.environ.get("CALISMA_GUNLERI", "1,2,3,4,5").split(",") if g.strip()}
    aralik = os.environ.get("CALISMA_SAATLERI", "09:00-18:00")
    ac, kapa = aralik.split("-")
    return gunler, time.fromisoformat(ac.strip()), time.fromisoformat(kapa.strip())


def calisma_saati_icinde(baslangic: datetime, bitis: datetime) -> bool:
    """Randevunun tamamı açık günün açık saatleri içinde mi.

    Kapanışı aşan randevu (17:45-18:15) reddedilir — personel kapıda kalmasın.
    """
    gunler, ac, kapa = _calisma_penceresi()
    if baslangic.isoweekday() not in gunler:
        return False
    if baslangic.date() != bitis.date():
        return False
    return ac <= baslangic.time() and bitis.time() <= kapa


# ── randevular ──────────────────────────────────────────────

def randevu_olustur(
    conn: psycopg.Connection,
    kisi_id: int,
    hizmet: str,
    baslangic: datetime,
    bitis: datetime,
    notlar: str | None = None,
    doktor_id: int | None = None,
    acil: bool = False,
) -> int:
    """Randevu açar. Çakışma **doktor bazında** ölçülür.

    İki doktor aynı saatte iki hastaya bakabilir; aynı doktor bakamaz. Doktor
    seçilmemiş randevular (klinik tek hekimliyse) kendi aralarında çakışır.
    """
    if baslangic < datetime.now(tz=baslangic.tzinfo):
        raise GecmisTarih(f"{baslangic:%d.%m.%Y %H:%M} geçmiş bir tarih")

    if not calisma_saati_icinde(baslangic, bitis):
        gunler, ac, kapa = _calisma_penceresi()
        raise CalismaSaatiDisi(
            f"Klinik o saatte kapalı. Çalışma saatleri: {ac:%H:%M}-{kapa:%H:%M}"
        )

    with conn.cursor() as cur:
        if doktor_id is not None:
            cur.execute("SELECT ad FROM doktorlar WHERE id = %s AND aktif", (doktor_id,))
            if not cur.fetchone():
                raise DoktorYok(f"{doktor_id} numaralı aktif doktor yok")

        # Kesişim testi: sınır teması (mevcut.bitis == yeni.baslangic) çakışma değil.
        # IS NOT DISTINCT FROM: iki NULL da aynı "doktor" sayılır.
        cur.execute(
            """
            SELECT r.id, d.ad FROM randevular r
            LEFT JOIN doktorlar d ON d.id = r.doktor_id
            WHERE r.durum <> 'iptal'
              AND r.doktor_id IS NOT DISTINCT FROM %s
              AND r.baslangic < %s AND r.bitis > %s
            LIMIT 1
            """,
            (doktor_id, bitis, baslangic),
        )
        if (mevcut := cur.fetchone()):
            kim = f" ({mevcut[1]})" if mevcut[1] else ""
            raise RandevuCakismasi(f"{baslangic:%d.%m.%Y %H:%M} saati dolu{kim}")

        cur.execute(
            """
            INSERT INTO randevular (kisi_id, hizmet, baslangic, bitis, notlar, doktor_id, acil)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (kisi_id, hizmet, baslangic, bitis, notlar, doktor_id, acil),
        )
        rid = cur.fetchone()[0]
    conn.commit()

    # Takvim kancası burada, çağıranlarda değil: randevu iki yoldan açılıyor
    # (ajan → /api/randevu, personel → panel formu) ve ikisinin de takvime
    # düşmesi gerekiyor. Yapılandırılmamışsa (anahtar yok) sessizce atlanır.
    gtakvim.randevuyu_takvime_yaz(conn, rid)
    return rid


def randevu_iptal(conn: psycopg.Connection, randevu_id: int) -> None:
    # Silme önce: durum 'iptal' olduktan sonra da okunabilir ama etkinliğin
    # hastanın/hekimin takviminde kalmaması daha önemli.
    gtakvim.randevuyu_takvimden_sil(conn, randevu_id)
    with conn.cursor() as cur:
        cur.execute("UPDATE randevular SET durum = 'iptal' WHERE id = %s", (randevu_id,))
    conn.commit()


def randevu_durum_yaz(
    conn: psycopg.Connection, randevu_id: int, durum: str, google_event_id: str | None = None
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE randevular
               SET durum = %s,
                   google_event_id = COALESCE(%s, google_event_id)
             WHERE id = %s
            """,
            (durum, google_event_id, randevu_id),
        )
    conn.commit()


def randevular_araliginda(conn: psycopg.Connection, bas, bit) -> list[dict]:
    """[bas, bit) aralığındaki randevular. Haftalık takvim ızgarası bunu kullanır.

    İptaller dışarıda: takvimde iptal edilmiş bir randevunun blok kaplaması,
    o saatin dolu olduğu izlenimi verirdi.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.*, k.ad, k.telefon, d.ad AS doktor_ad
              FROM randevular r
              JOIN kisiler k ON k.id = r.kisi_id
              LEFT JOIN doktorlar d ON d.id = r.doktor_id
             WHERE r.baslangic >= %s AND r.baslangic < %s AND r.durum <> 'iptal'
             ORDER BY r.baslangic
            """,
            (bas, bit),
        )
        return cur.fetchall()


def randevular_listele(conn: psycopg.Connection, gun=None) -> list[dict]:
    """Randevular, başlangıca göre artan. `gun` verilirse yalnız o gün."""
    with conn.cursor(row_factory=dict_row) as cur:
        if gun is None:
            cur.execute(
                """
                SELECT r.*, k.ad, k.telefon, d.ad AS doktor_ad
                  FROM randevular r
                  JOIN kisiler k ON k.id = r.kisi_id
                  LEFT JOIN doktorlar d ON d.id = r.doktor_id
                 ORDER BY r.baslangic
                """
            )
        else:
            cur.execute(
                """
                SELECT r.*, k.ad, k.telefon, d.ad AS doktor_ad
                  FROM randevular r
                  JOIN kisiler k ON k.id = r.kisi_id
                  LEFT JOIN doktorlar d ON d.id = r.doktor_id
                 WHERE r.baslangic::date = %s
                 ORDER BY r.baslangic
                """,
                (gun,),
            )
        return cur.fetchall()


def dolu_araliklar(conn: psycopg.Connection, gun, doktor_id: int | None = None) -> list[dict]:
    """O günün dolu saatleri. `doktor_id` verilirse yalnız o doktorunkiler."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.baslangic, r.bitis, r.doktor_id, d.ad AS doktor_ad
              FROM randevular r LEFT JOIN doktorlar d ON d.id = r.doktor_id
             WHERE r.durum <> 'iptal' AND r.baslangic::date = %s
               AND (%s::int IS NULL OR r.doktor_id = %s)
             ORDER BY r.baslangic
            """,
            (gun, doktor_id, doktor_id),
        )
        return cur.fetchall()


# ── panel istatistikleri ────────────────────────────────────

def gun_bazli_doluluk(conn: psycopg.Connection, hafta_sayisi: int = 8) -> list[dict]:
    """Haftanın hangi günü daha dolu. Son N haftanın randevuları gün gün toplanır."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT EXTRACT(ISODOW FROM baslangic)::int AS gun, count(*) AS adet
              FROM randevular
             WHERE durum <> 'iptal'
               AND baslangic >= now() - make_interval(weeks => %s)
             GROUP BY 1
            """,
            (hafta_sayisi,),
        )
        sayilar = {r["gun"]: r["adet"] for r in cur.fetchall()}

    adlar = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    acik = {int(g) for g in os.environ.get("CALISMA_GUNLERI", "1,2,3,4,5").split(",") if g.strip()}
    return [
        {"gun": i, "ad": adlar[i - 1], "adet": sayilar.get(i, 0), "acik": i in acik}
        for i in range(1, 8)
    ]


def saat_bazli_doluluk(conn: psycopg.Connection, hafta_sayisi: int = 8) -> list[dict]:
    """Günün hangi saati daha dolu — çalışma penceresi boyunca."""
    _, ac, kapa = _calisma_penceresi()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT EXTRACT(HOUR FROM baslangic)::int AS saat, count(*) AS adet
              FROM randevular
             WHERE durum <> 'iptal'
               AND baslangic >= now() - make_interval(weeks => %s)
             GROUP BY 1
            """,
            (hafta_sayisi,),
        )
        sayilar = {r["saat"]: r["adet"] for r in cur.fetchall()}

    return [
        {"saat": s, "etiket": f"{s:02d}", "adet": sayilar.get(s, 0)}
        for s in range(ac.hour, kapa.hour)
    ]


def hizmet_dagilimi(conn: psycopg.Connection, limit: int = 6) -> list[dict]:
    """En çok istenen hizmetler. Limitin dışı 'Diğer' olarak toplanır."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT hizmet, count(*) AS adet
              FROM randevular WHERE durum <> 'iptal'
             GROUP BY 1 ORDER BY 2 DESC
            """
        )
        satirlar = cur.fetchall()

    if len(satirlar) > limit:
        diger = sum(r["adet"] for r in satirlar[limit:])
        satirlar = satirlar[:limit] + [{"hizmet": "Diğer", "adet": diger}]
    return satirlar


def ozet_sayilar(conn: psycopg.Connection) -> dict:
    """Panelin üst şeridindeki dört rakam."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM randevular
                WHERE baslangic::date = current_date AND durum <> 'iptal'),
              (SELECT count(*) FROM randevular
                WHERE durum = 'bekliyor' AND baslangic >= now()),
              (SELECT count(*) FROM kisiler),
              (SELECT count(*) FROM gorusmeler
                WHERE yon = 'gelen' AND olusturma >= now() - interval '7 days')
            """
        )
        bugun, bekleyen, hasta, mesaj = cur.fetchone()

    return {
        "bugunku_randevu": bugun,
        "bekleyen_randevu": bekleyen,
        "toplam_hasta": hasta,
        "haftalik_mesaj": mesaj,
    }


def kullanim_ozeti(conn: psycopg.Connection, pencere_saniye: int) -> dict:
    """Son `pencere_saniye` içinde WhatsApp'a gönderilen mesaj adedi ve toplam token — app/rapor.py."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM gorusmeler
                WHERE yon = 'giden' AND olusturma >= now() - make_interval(secs => %s)),
              (SELECT coalesce(sum(giris_token), 0) FROM gorusmeler
                WHERE olusturma >= now() - make_interval(secs => %s)),
              (SELECT coalesce(sum(cikis_token), 0) FROM gorusmeler
                WHERE olusturma >= now() - make_interval(secs => %s))
            """,
            (pencere_saniye, pencere_saniye, pencere_saniye),
        )
        giden, giris, cikis = cur.fetchone()

        # Devir dökümü — 'devir açıldı: {neden}' sistem satırlarından
        cur.execute(
            """
            SELECT split_part(mesaj, ': ', 2), count(*) FROM gorusmeler
            WHERE yon = 'sistem' AND mesaj LIKE 'devir açıldı: %%'
              AND olusturma >= now() - make_interval(secs => %s)
            GROUP BY 1
            """,
            (pencere_saniye,),
        )
        devir = dict(cur.fetchall())
    return {"giden_mesaj": giden, "giris_token": giris, "cikis_token": cikis, "devir": devir}


# ── doktorlar ───────────────────────────────────────────────

def doktor_ekle(conn: psycopg.Connection, ad: str, uzmanlik: str | None = None,
                notlar: str | None = None, telefon: str | None = None,
                eposta: str | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO doktorlar (ad, uzmanlik, notlar, telefon, eposta) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (ad, uzmanlik, notlar, telefon, eposta),
        )
        did = cur.fetchone()[0]
    conn.commit()
    return did


def doktor_eposta_guncelle(conn: psycopg.Connection, doktor_id: int,
                           eposta: str | None) -> None:
    """Randevu davetleri bu adrese gider; boş gönderilirse davet eklenmez."""
    with conn.cursor() as cur:
        cur.execute("UPDATE doktorlar SET eposta = %s WHERE id = %s", (eposta, doktor_id))
    conn.commit()


def doktor_durum_yaz(conn: psycopg.Connection, doktor_id: int, aktif: bool) -> None:
    """Pasifleştirilen doktora yeni randevu açılamaz; mevcut randevuları durur."""
    with conn.cursor() as cur:
        cur.execute("UPDATE doktorlar SET aktif = %s WHERE id = %s", (aktif, doktor_id))
    conn.commit()


def doktorlar_listele(conn: psycopg.Connection, yalniz_aktif: bool = False) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.*,
                   (SELECT count(*) FROM randevular r
                     WHERE r.doktor_id = d.id AND r.durum <> 'iptal'
                       AND r.baslangic >= now()) AS gelecek_randevu
              FROM doktorlar d
             WHERE (%s = false OR d.aktif)
             ORDER BY d.aktif DESC, d.ad
            """,
            (yalniz_aktif,),
        )
        return cur.fetchall()


def hastanin_doktoru(conn: psycopg.Connection, kisi_id: int) -> dict | None:
    """Hastanın en son gittiği doktor. İlk kez gelen hasta için None.

    Ajan bunu "geçen sefer Dr. X'e gelmiştiniz, yine onu ister misiniz?" demek
    için kullanır — hastaya her seferinde baştan doktor sordurmaz.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.id, d.ad, d.uzmanlik, d.aktif, r.baslangic AS son_randevu
              FROM randevular r JOIN doktorlar d ON d.id = r.doktor_id
             WHERE r.kisi_id = %s AND r.durum <> 'iptal'
             ORDER BY r.baslangic DESC LIMIT 1
            """,
            (kisi_id,),
        )
        return cur.fetchone()


def doktor_musait_mi(conn: psycopg.Connection, doktor_id: int,
                     baslangic: datetime, bitis: datetime) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM randevular
             WHERE durum <> 'iptal' AND doktor_id = %s
               AND baslangic < %s AND bitis > %s
             LIMIT 1
            """,
            (doktor_id, bitis, baslangic),
        )
        return cur.fetchone() is None


def en_bos_doktor(conn: psycopg.Connection, baslangic: datetime,
                  bitis: datetime) -> dict | None:
    """O aralıkta müsait doktorlar arasında **o gün en az yüklü** olanı seçer.

    İlk kez gelen hastanın doktor tercihi olmaz; iş en boş hekime dağıtılır.
    Eşitlikte ada göre — aynı girdi hep aynı doktoru verir, ajan iki kez
    sorulduğunda farklı cevap vermez.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.id, d.ad, d.uzmanlik,
                   (SELECT count(*) FROM randevular r
                     WHERE r.doktor_id = d.id AND r.durum <> 'iptal'
                       AND r.baslangic::date = %s::date) AS gun_yuku
              FROM doktorlar d
             WHERE d.aktif
               AND NOT EXISTS (
                   SELECT 1 FROM randevular r
                    WHERE r.doktor_id = d.id AND r.durum <> 'iptal'
                      AND r.baslangic < %s AND r.bitis > %s)
             ORDER BY gun_yuku ASC, d.ad ASC
             LIMIT 1
            """,
            (baslangic, bitis, baslangic),
        )
        return cur.fetchone()


def en_erken_uygun(conn: psycopg.Connection, sure_dk: int = 30,
                   gun_ufku: int = 14, doktor_id: int | None = None) -> dict | None:
    """En erken müsait slot — acil vakalar için.

    Bugünden başlayarak çalışma saatleri içinde yarım saatlik adımlarla tarar,
    ilk boş aralığı ve o aralıkta en boş doktoru döndürür. Hiç yer yoksa None.
    """
    from datetime import timedelta

    gunler, ac, kapa = _calisma_penceresi()
    simdi = datetime.now()

    for gun_ekle in range(gun_ufku + 1):
        g = (simdi + timedelta(days=gun_ekle)).date()
        if g.isoweekday() not in gunler:
            continue

        an = datetime.combine(g, ac)
        kapanis = datetime.combine(g, kapa)
        # Bugünse geçmiş saatleri atla, sonraki yarım saate yuvarla
        if an < simdi:
            an = simdi.replace(second=0, microsecond=0)
            an += timedelta(minutes=(30 - an.minute % 30) % 30)
            if an.minute % 30:
                an = an.replace(minute=0 if an.minute < 30 else 30)

        while an + timedelta(minutes=sure_dk) <= kapanis:
            bitis = an + timedelta(minutes=sure_dk)
            if doktor_id is not None:
                if doktor_musait_mi(conn, doktor_id, an, bitis):
                    return {"baslangic": an, "bitis": bitis, "doktor_id": doktor_id}
            else:
                d = en_bos_doktor(conn, an, bitis)
                if d:
                    return {"baslangic": an, "bitis": bitis,
                            "doktor_id": d["id"], "doktor_ad": d["ad"]}
            an += timedelta(minutes=30)

    return None


def doktor_bazli_doluluk(conn: psycopg.Connection, hafta_sayisi: int = 8) -> list[dict]:
    """Panel grafiği: hangi doktor ne kadar yüklü."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.ad, count(r.id) AS adet
              FROM doktorlar d
              LEFT JOIN randevular r ON r.doktor_id = d.id AND r.durum <> 'iptal'
                   AND r.baslangic >= now() - make_interval(weeks => %s)
             WHERE d.aktif
             GROUP BY d.id, d.ad ORDER BY adet DESC, d.ad
            """,
            (hafta_sayisi,),
        )
        return cur.fetchall()
