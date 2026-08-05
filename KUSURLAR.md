# İnşa Sırasında Bulunan ve Düzeltilen Kusurlar

2026-08-05, Faz 0-7 inşası. Hepsi düzeltildi ve regresyon testiyle kilitlendi.

---

## 1. Testler canlı veritabanını siliyordu — KRİTİK

**Ne oluyordu:** `tests/conftest.py` her testten önce `TRUNCATE` atıyor ve
`DATABASE_URL`'i hedefliyordu. VPS'te bir `pytest` komutu kliniğin **tüm hasta
kayıtlarını, randevularını ve görüşme geçmişini** silerdi.

**Nasıl fark edildi:** Faz 7'de son doğrulama yaparken ajan "çalışma saatleri
bilgi tabanında girilmemiş" dedi — oysa girmiştim. Testler silmişti.

**Neden tehlikeliydi:** Sessizdi. Testler yeşil geçiyordu; kaybı ancak veriye
bakınca görüyordunuz. Yedekten dönmek dışında telafisi yoktu.

**Düzeltme:** Testler artık ayrı bir `_test` veritabanı kullanıyor. `TEST_DATABASE_URL`
verilmemişse `DATABASE_URL`'in adına `_test` ekliyor. O veritabanı yoksa testler
**atlanıyor** — sessizce canlıya düşmüyor.

**Kanıt:** Canlı veriye 5 kayıt girildi, tüm test paketi koşturuldu, 5 kayıt yerinde.

`tests/conftest.py:_test_veritabani_url()`

---

## 2. Randevu saatleri 3 saat kayıyordu

**Ne oluyordu:** Ajan 14:00'e randevu yazıyor, panelde 17:00 görünüyordu.
Postgres bağlantısı UTC'deydi; saat dilimi taşımayan zamanlar ("14:00") UTC olarak
yorumlanıyordu.

**Neden tehlikeliydi:** Hasta 14:00'e geliyor, personel 17:00 bekliyor. Ajan doğru
saati söylediği için kimse hatayı ajanda aramazdı.

**Denenip işe yaramayan:** Docker'da `TZ` ve `PGTZ` ortam değişkenleri — postgres
image'ı `postgresql.conf`'u ezmiyor.

**Düzeltme:** Saat dilimi bağlantı seviyesinde sabitlendi
(`options="-c timezone=Europe/Istanbul"`). Hangi yoldan bağlanılırsa bağlanılsın geçerli.

`app/db.py:baglan()` · test: `test_randevu.py::test_saat_kaymasi_yok`

---

## 3. Gönderim başarısız olunca ajanın cevabı kayboluyordu

**Ne oluyordu:** Sıra `ajan cevap üret → WhatsApp'a gönder → kaydet` şeklindeydi.
WhatsApp'a ulaşılamazsa exception atılıyor, kayıt hiç yapılmıyordu. Ajanın ürettiği
cevap tamamen yok oluyordu.

**Nasıl fark edildi:** Webhook'u gerçek HTTP ile test ederken OpenWA konteyneri
düşüktü. Gelen mesaj DB'ye yazılmış, giden yazılmamıştı.

**Neden tehlikeliydi:** Personel panelde hastanın sorusunu görüyor, ajanın ne cevap
verdiğini göremiyordu. Hasta da bir şey almadığı için ikinci kez yazıyordu.

**Düzeltme:** Sıra `kaydet → gönder` oldu. Gönderim başarısız olsa da cevap panelde
görünüyor.

`app/main.py:_mesaji_isle()` · test: `test_webhook.py::test_gonderim_coksede_cevap_kaydedilir`

---

## 4. OpenWA API'si oturum adını kabul etmiyordu

**Ne oluyordu:** `.env`'de okunabilir `OPENWA_SESSION=klinik` tutuluyordu ama OpenWA'nın
tüm uçları UUID istiyor: `Validation failed (uuid is expected)`.

**Düzeltme:** `oturum_id()` adı `GET /api/sessions` üzerinden UUID'ye çeviriyor ve
önbelleğe alıyor. `.env` okunabilir kalıyor; oturum silinip yeniden kurulursa servis
restart'ında UUID kendiliğinden yenileniyor.

`app/openwa.py:oturum_id()` · testler: `test_webhook.py` içinde 3 test

---

## 5. Ajan logları repoya sızıyordu — KVKK

**Ne oluyordu:** `.gitignore` yalnız `hermes-home/sessions/` ve `memories/`'i
engelliyordu. `hermes-home/logs/agent.log`, `cache/`, `auth.lock` commit'e giriyordu.
`agent.log` içinde **hasta mesajları** geçebilir.

**Neden tehlikeliydi:** Repo GitHub'a gidecek. Hasta verisinin repoya girmesi KVKK
ihlali; geçmişten silmek de commit geçmişi yazmayı gerektirir.

**Düzeltme:** `.gitignore` beyaz listeye çevrildi — `hermes-home/*` engelli, yalnız
`config.yaml`, `SOUL.md` ve `skills/` açık.

`.gitignore`

---

## 6. İç API hata durumunda HTML dönüyordu

**Ne oluyordu:** Genel hata yakalayıcı tüm `HTTPException`'ları HTML sayfasına
çeviriyordu. Ajan `/api/randevu`'ya istek atıp `409` (o saat dolu) alınca cevabı
`<h1>409</h1>` olarak görüyordu.

**Neden tehlikeliydi:** Ajan "o saat dolu" ile "sunucu çöktü" arasındaki farkı
göremezdi. Hastaya "randevunuz oluştu" diyebilirdi — oluşmamışken.

**Düzeltme:** `/api/` ile başlayan yollar artık `{"hata": "..."}` JSON döner.

`app/main.py:yonlendir_ya_da_hata()`

---

## 7. scrypt OpenSSL'in bellek sınırını aşıyordu

**Ne oluyordu:** Parola hash'i `n=2^17` ile ~134MB iş belleği istiyor, OpenSSL'in
varsayılan `maxmem` sınırı 32MB. Uygulama açılışta `ValueError: memory limit
exceeded` ile çöküyordu.

**Düzeltme:** `n=2^16` (~67MB) + açık `maxmem=192MB`. Hash süresi ~107ms — panel
girişinde görünmez, sözlük saldırısı için pahalı.

`app/kullanici.py`

---

## 8. Testler yeni tabloları temizlemiyordu

**Ne oluyordu:** `conftest` TRUNCATE listesi `doktorlar`, `kullanicilar`,
`islem_kaydi` eklendiğinde güncellenmemişti. Testler birbirinin verisini görüyor,
id'ler beklenmedik yerden geliyordu.

**Düzeltme:** Liste güncellendi ve yanına not düşüldü: yeni tablo eklendiğinde
buraya da eklenmeli.

`tests/conftest.py`

---

## Kusur sayılmayan ama takılınan noktalar

Bunlar bizim hatamız değildi; OpenWA'nın kurulum gereklilikleriydi. Hepsi
`docker-compose.yml` içinde yorumlarıyla duruyor:

| Belirti | Sebep | Çözüm |
|---|---|---|
| Konteyner restart döngüsünde | Postgres'le `DATABASE_SYNCHRONIZE=true` yasak | `false` |
| `Destination address is not allowed` | SSRF koruması webhook hedefini bloke ediyor | `SSRF_ALLOWED_HOSTS` |
| `Target closed`, engine timeout | Chromium ek argümanlar olmadan açılmıyor | 4 ek `PUPPETEER_ARGS` + `WWEBJS_AUTH_TIMEOUT_MS` |
| Port çakışması | 5432/5433 makinede başka projelerde dolu | 5434 |
| `TypeError: unsupported operand \|` | Sistem python3'ü 3.9 | venv 3.13 ile kuruldu |
