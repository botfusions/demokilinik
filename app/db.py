"""Veritabanı bağlantısı ve şema.

ORM yok — düz SQL, tablolar aşağıda. Migration aracı da yok: şema
`CREATE TABLE IF NOT EXISTS` ile açılışta kurulur. Şema büyüyüp geçmişe dönük veri taşımak gerekirse Alembic ekle.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SEMA = """
CREATE TABLE IF NOT EXISTS kisiler (
    id            serial PRIMARY KEY,
    telefon       text NOT NULL UNIQUE,
    ad            text,
    ilk_temas     timestamptz NOT NULL DEFAULT now(),
    son_temas     timestamptz NOT NULL DEFAULT now(),
    personel_notu text
);

CREATE TABLE IF NOT EXISTS gorusmeler (
    id             serial PRIMARY KEY,
    kisi_id        integer NOT NULL REFERENCES kisiler(id) ON DELETE CASCADE,
    yon            text NOT NULL CHECK (yon IN ('gelen', 'giden')),
    mesaj          text NOT NULL,
    kanal          text NOT NULL DEFAULT 'whatsapp',
    -- Tekil; NULL'lar Postgres'te çakışmaz, giden mesajların bir kısmı id'siz olabilir
    wa_message_id  text UNIQUE,
    maliyet_usd    numeric(10, 6),
    olusturma      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gorusmeler_kisi_idx ON gorusmeler (kisi_id, id);

-- Haftalık kullanım raporu için (app/rapor.py) — $ maliyet değil, ham token.
ALTER TABLE gorusmeler ADD COLUMN IF NOT EXISTS giris_token integer;
ALTER TABLE gorusmeler ADD COLUMN IF NOT EXISTS cikis_token integer;

CREATE TABLE IF NOT EXISTS doktorlar (
    id        serial PRIMARY KEY,
    ad        text NOT NULL,
    uzmanlik  text,
    telefon   text,
    aktif     boolean NOT NULL DEFAULT true,
    notlar    text,
    olusturma timestamptz NOT NULL DEFAULT now()
);
-- Doktora yeni randevu bildirimi göndermek için sonradan eklendi.
ALTER TABLE doktorlar ADD COLUMN IF NOT EXISTS telefon text;
-- Google Takvim daveti bu adrese gider; hekim randevuyu kendi takviminde görür.
ALTER TABLE doktorlar ADD COLUMN IF NOT EXISTS eposta text;

CREATE TABLE IF NOT EXISTS randevular (
    id              serial PRIMARY KEY,
    kisi_id         integer NOT NULL REFERENCES kisiler(id) ON DELETE CASCADE,
    -- Doktor listesi boşken NULL kalır; klinik tek hekimliyse hiç doldurulmaz.
    -- Silinen doktorun randevusu kaybolmasın diye ON DELETE SET NULL.
    doktor_id       integer REFERENCES doktorlar(id) ON DELETE SET NULL,
    acil            boolean NOT NULL DEFAULT false,
    hizmet          text NOT NULL,
    baslangic       timestamptz NOT NULL,
    bitis           timestamptz NOT NULL,
    durum           text NOT NULL DEFAULT 'bekliyor'
                    CHECK (durum IN ('bekliyor', 'onayli', 'geldi', 'iptal')),
    google_event_id text,
    notlar          text,
    olusturma       timestamptz NOT NULL DEFAULT now(),
    CHECK (bitis > baslangic)
);
CREATE INDEX IF NOT EXISTS randevular_zaman_idx ON randevular (baslangic);

-- Doktor katmanı sonradan eklendi; mevcut kurulumlarda randevular tablosu zaten
-- var, o yüzden kolonlar ALTER ile geliyor. İndeks kolondan SONRA gelmeli.
ALTER TABLE randevular ADD COLUMN IF NOT EXISTS doktor_id integer REFERENCES doktorlar(id) ON DELETE SET NULL;
ALTER TABLE randevular ADD COLUMN IF NOT EXISTS acil boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS randevular_doktor_idx ON randevular (doktor_id, baslangic);

-- 'geldi' sonradan eklendi; mevcut kurulumlarda CHECK eski üç değeri taşıyor.
ALTER TABLE randevular DROP CONSTRAINT IF EXISTS randevular_durum_check;
ALTER TABLE randevular ADD CONSTRAINT randevular_durum_check
    CHECK (durum IN ('bekliyor', 'onayli', 'geldi', 'iptal'));

-- Giden hatırlatmalar. UNIQUE(randevu_id, tur): aynı randevu için aynı tür
-- hatırlatma ikinci kez gönderilemez — nöbetçi iki kez çalışsa bile.
CREATE TABLE IF NOT EXISTS hatirlatmalar (
    id            serial PRIMARY KEY,
    randevu_id    integer NOT NULL REFERENCES randevular(id) ON DELETE CASCADE,
    tur           text NOT NULL CHECK (tur IN ('24s', '1s')),
    planlanan     timestamptz NOT NULL,
    gonderildi    timestamptz,
    wa_message_id text,
    hata          text,
    UNIQUE (randevu_id, tur)
);
CREATE INDEX IF NOT EXISTS hatirlatmalar_bekleyen_idx
    ON hatirlatmalar (planlanan) WHERE gonderildi IS NULL;

CREATE TABLE IF NOT EXISTS kullanicilar (
    id           serial PRIMARY KEY,
    kullanici_adi text NOT NULL UNIQUE,
    ad           text,
    parola_hash  text NOT NULL,
    rol          text NOT NULL DEFAULT 'personel' CHECK (rol IN ('admin', 'personel')),
    aktif        boolean NOT NULL DEFAULT true,
    son_giris    timestamptz,
    olusturma    timestamptz NOT NULL DEFAULT now()
);

-- Kim ne yaptı. Randevu değiştiren, bilgi silen, kullanıcı ekleyen hep buraya düşer.
CREATE TABLE IF NOT EXISTS islem_kaydi (
    id           serial PRIMARY KEY,
    kullanici_id integer REFERENCES kullanicilar(id) ON DELETE SET NULL,
    kullanici_adi text,          -- kullanıcı silinse de iz kalsın
    eylem        text NOT NULL,
    detay        text,
    olusturma    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS islem_kaydi_zaman_idx ON islem_kaydi (olusturma DESC);

CREATE TABLE IF NOT EXISTS bilgi_tabani (
    id         serial PRIMARY KEY,
    baslik     text NOT NULL,
    icerik     text NOT NULL,
    kategori   text NOT NULL DEFAULT 'genel',
    aktif      boolean NOT NULL DEFAULT true,
    guncelleme timestamptz NOT NULL DEFAULT now()
);

-- Hizmet fiyatları. Fiyatın TEK kaynağı burasıdır; bilgi_tabani'nda "fiyatlar"
-- kategorisi yoktur. Aynı hizmetin iki yerde farklı fiyatla durması, ajanın
-- hangisini söyleyeceğinin belirsiz kalması demekti.
-- onceki_fiyat: panelde "% değişim" sütunu ve fiyat geçmişi için.
CREATE TABLE IF NOT EXISTS hizmetler (
    id           serial PRIMARY KEY,
    ad           text NOT NULL UNIQUE,
    fiyat        numeric(10, 2) NOT NULL CHECK (fiyat >= 0),
    onceki_fiyat numeric(10, 2),
    aktif        boolean NOT NULL DEFAULT true,
    guncelleme   timestamptz NOT NULL DEFAULT now()
);

-- Kampanyalar. DİKKAT: bu bir duyuru/gönderim aracı DEĞİLDİR — hasta fiyat
-- sorduğunda ajanın söyleyeceği indirimi tanımlar, kimseye kendiliğinden mesaj
-- göndermez. Toplu mesaj yasağı için bkz. README § Toplu mesaj yasağı.
-- hizmet_id NULL = tüm hizmetlere geçerli. bitis NULL = süresiz.
CREATE TABLE IF NOT EXISTS kampanyalar (
    id             serial PRIMARY KEY,
    ad             text NOT NULL,
    indirim_yuzde  integer NOT NULL CHECK (indirim_yuzde > 0 AND indirim_yuzde <= 100),
    hizmet_id      integer REFERENCES hizmetler(id) ON DELETE CASCADE,
    bitis          date,
    aktif          boolean NOT NULL DEFAULT true,
    olusturma      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS baglanti_saglik (
    servis           text PRIMARY KEY,
    durum            text NOT NULL DEFAULT 'saglikli',
    son_kontrol      timestamptz NOT NULL DEFAULT now(),
    son_basarili     timestamptz,
    hata             text,
    ardisik_hata     integer NOT NULL DEFAULT 0,
    uyari_gonderildi boolean NOT NULL DEFAULT false
);

-- Sistem doktorunun onarım girişimleri (app/doktor.py). saglik tespit eder,
-- doktor onarır; her girişim buraya yazılır, /doktor panelinde görünür.
CREATE TABLE IF NOT EXISTS sistem_onarim (
    id          bigserial PRIMARY KEY,
    servis      text NOT NULL,
    zaman       timestamptz NOT NULL DEFAULT now(),
    tetikleyici text NOT NULL,        -- 'otomatik' | 'manuel' | 'supervisor'
    basarili    boolean NOT NULL,
    mesaj       text
);
CREATE INDEX IF NOT EXISTS sistem_onarim_zaman_idx ON sistem_onarim (zaman DESC);

-- Klinikten kliniğe değişen ayarlar. Bunlar önce yalnız env'deydi; env yalnız
-- deploy sırasında (Coolify panelinden) girilebildiği için bir klinikte
-- unutulduğunda kod sessizce varsayılana düşüyordu — canlıda cumartesi
-- randevusu tam olarak böyle reddedildi. Artık personel panelden değiştirir,
-- env yalnız satır yoksa yedek kalır.
CREATE TABLE IF NOT EXISTS ayarlar (
    anahtar text PRIMARY KEY,
    deger   text NOT NULL
);
"""


def baglan() -> psycopg.Connection:
    """Bağlantı açar ve saat dilimini klinik saatine sabitler.

    Ajan ve panel saat dilimi olmayan zamanlar üretir ("14:00"). Postgres bunları
    oturumun TimeZone'una göre yorumlar; varsayılan UTC bırakılsaydı 14:00 randevu
    panelde 17:00 görünürdü. Konteyner TZ env'i postgresql.conf'u ezmediği için
    ayar bağlantı seviyesinde yapılıyor — hangi yoldan bağlanılırsa bağlanılsın geçerli.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL tanımlı değil — .env dosyasını kontrol et")

    tz = os.environ.get("TZ", "Europe/Istanbul")
    return psycopg.connect(url, autocommit=False, options=f"-c timezone={tz}")


def sema_kur(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SEMA)
    conn.commit()


if __name__ == "__main__":
    c = baglan()
    sema_kur(c)
    print("Şema kuruldu.")
