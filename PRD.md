# PRD — Klinik WhatsApp Resepsiyonist Ajanı

**Sürüm:** 1.0 · **Tarih:** 2026-08-05 · **Durum:** inşaya hazır

---

## 1. Ne yapıyoruz

Bir kliniğe WhatsApp üzerinden çalışan resepsiyonist ajan. Hasta WhatsApp'tan yazar; ajan klinik bilgi tabanından cevap verir, randevu talebini Google Takvim'e yazar, onay maili gönderir. Her mesaj CRM'e kaydedilir. Klinik personeli Türkçe bir panelden bilgi tabanını ve randevuları yönetir.

**Yarın VPS'e çıkacak.** İleride aynı ajana Meta reklam yönetimi gibi modüller eklenecek.

### Kim ne görür

| Rol | Nereden | Ne görür |
|---|---|---|
| Hasta | WhatsApp | Sadece ajanın mesajlarını. Panel yok. |
| Klinik personeli | Panel `:8000`, şifreyle | Bilgi tabanı, randevular, hastalar, görüşme geçmişi, bağlantı durumu |
| Klinik personeli | OpenWA dashboard `:2785` | QR okutma, WhatsApp oturum durumu |

---

## 2. Mimari

```
Hasta telefonu
   │ WhatsApp
   ▼
OpenWA (Docker, whatsapp-web.js motoru)     :2785 — dashboard + REST API
   │ webhook: POST /webhook/whatsapp  (X-OpenWA-Signature: sha256=<hex>)
   ▼
Köprü + Panel (FastAPI, tek servis)          :8000
   1. HMAC imzasını ham gövde üzerinden doğrula → geçersizse 401
   2. 200 dön (hemen) → işi BackgroundTasks'a at
   3. kişi upsert + gelen mesajı Postgres'e yaz (wa_message_id ile idempotent)
   4. son 10 görüşmeyi prompt'a koy → `hermes -z` çağır
   5. yanıtı Postgres'e yaz
   6. OpenWA REST ile yanıtı gönder
   ▼
Hermes ajanı  (HERMES_HOME=./hermes-home, gpt-5.6-luna / medium)
   ├─ .hermes.md ......... klinik bilgi tabanı (bilgi_tabani tablosundan üretilir)
   ├─ SOUL.md ............ resepsiyonist kimliği ve sınırları
   ├─ skills/randevu-al .. randevu akışı
   └─ MCP: Composio ...... Google Takvim + Gmail
   ▼
Postgres (Docker)  ── klinik_crm ── openwa (ayrı veritabanı)
```

**Neden köprü, neden Hermes'in kendi gateway'i değil:** her mesaj köprüde deterministik olarak kaydedilir. Ajanın "CRM'e yazmayı hatırlamasına" bağlı değil. Ayrıca OpenWA'nın dashboard'u, webhook filtreleri ve rate limiter'ı hazır gelir.

**Oturum yönetimi yok:** `hermes -z` her çağrıda son 10 görüşmeyi prompt'ta alır. Geçmişin tek kaynağı Postgres — servis restart'ında hiçbir şey kaybolmaz.
*Atlandı: Hermes session/resume — DB zaten geçmişi tutuyor; iki yerde state tutmak senkron sorunu demek. 10 mesajlık bağlam yetmezse artır.*

---

## 3. Veri modeli — `klinik_crm`

```sql
kisiler          id, telefon UNIQUE, ad, ilk_temas, son_temas, personel_notu
gorusmeler       id, kisi_id FK, yon('gelen'|'giden'), mesaj, kanal,
                 wa_message_id UNIQUE NULL, maliyet_usd, olusturma
randevular       id, kisi_id FK, hizmet, baslangic, bitis,
                 durum('bekliyor'|'onayli'|'iptal'), google_event_id, notlar, olusturma
bilgi_tabani     id, baslik, icerik, kategori, aktif, guncelleme
baglanti_saglik  servis PK, durum, son_kontrol, son_basarili, hata,
                 ardisik_hata, uyari_gonderildi
```

`bilgi_tabani` tek gerçek kaynak. `kb.py` bu tablodan `.hermes.md` üretir. Panelden bilgi girilince dosya yeniden yazılır; **restart gerekmez** — `hermes -z` her çağrıda dosyayı okur.

---

## 4. Modül sözleşmeleri

Testler bu imzalara yazıldı. İnşa bunlara uymak zorunda.

### `app/db.py`
```python
baglan() -> Connection              # DATABASE_URL'den, psycopg
sema_kur(conn) -> None              # CREATE TABLE IF NOT EXISTS × 5
```

### `app/crm.py`
```python
class RandevuCakismasi(Exception): ...
class GecmisTarih(Exception): ...
class CalismaSaatiDisi(Exception): ...

kisi_upsert(conn, telefon, ad=None) -> int       # var olan telefonda yeni kişi açmaz, son_temas günceller
gorusme_ekle(conn, kisi_id, yon, mesaj, wa_message_id=None, maliyet_usd=None) -> int | None
                                                  # aynı wa_message_id ikinci kez gelirse None döner, kayıt açmaz
gorusme_gecmisi(conn, kisi_id, limit=10) -> list[dict]   # eskiden yeniye sıralı
randevu_olustur(conn, kisi_id, hizmet, baslangic, bitis, notlar=None) -> int
                                                  # çakışma/geçmiş tarih/çalışma saati dışı → exception
randevu_iptal(conn, randevu_id) -> None           # durum='iptal', saat yeniden açılır
randevular_listele(conn, gun=None) -> list[dict]  # baslangic'e göre artan
calisma_saati_icinde(baslangic, bitis) -> bool    # CALISMA_GUNLERI / CALISMA_SAATLERI env'inden
```

Çakışma kuralı: yalnız `durum != 'iptal'` randevular çakışır. Sınır teması (`mevcut.bitis == yeni.baslangic`) çakışma **değildir**.

### `app/kb.py`
```python
bilgi_ekle(conn, baslik, icerik, kategori) -> int
bilgi_pasiflestir(conn, bilgi_id) -> None
hermes_md_uret(conn) -> str          # yalnız aktif kayıtlar, kategoriye göre gruplu markdown
hermes_md_yaz(conn, yol) -> None     # idempotent: aynı içerik iki kez yazılırsa dosya değişmez
```

### `app/openwa.py`
```python
imza_dogrula(ham_govde: bytes, imza_basligi: str, gizli: str) -> bool
                                     # 'sha256=<hex>', hmac.compare_digest, ham gövde üzerinden
telefon_ayikla(chat_id: str) -> str  # '905321234567@c.us' -> '905321234567'
mesaj_gonder(telefon: str, metin: str) -> str    # POST /api/sessions/{S}/messages/send-text, messageId döner
oturum_durumu() -> str               # GET /api/sessions/{S} -> 'ready' | 'disconnected' | ...
```

### `app/ajan.py`
```python
prompt_hazirla(gecmis: list[dict], mesaj: str) -> str
cevap_uret(gecmis, mesaj) -> tuple[str, float | None]    # (yanıt, maliyet_usd)
                                     # subprocess: hermes -z <prompt> --usage-file <tmp>
                                     # HERMES_HOME ve cwd proje köküne sabit
                                     # zaman aşımı/exit≠0 → CevapUretilemedi
class CevapUretilemedi(Exception): ...
```

### `app/saglik.py`
```python
kontrol_sonucu_isle(conn, servis: str, basarili: bool, hata: str | None = None) -> str | None
    # None      → aksiyon yok
    # 'uyari'   → iki ardışık hatadan sonra, uyarı bir kez
    # 'duzeldi' → uyarı gönderilmişken servis geri geldi
uyari_maili_gonder(konu: str, govde: str) -> None    # smtplib
```

### `app/main.py`
```
POST /webhook/whatsapp     # imza doğrula → 200 → BackgroundTasks
GET  /giris, POST /giris   # tek parola + imzalı cookie
GET  /                     # panel: özet + bağlantı banner'ı
GET  /bilgi, POST /bilgi   # bilgi tabanı CRUD → .hermes.md yeniden yazılır
GET  /randevular
GET  /hastalar, GET /hastalar/{id}
POST /api/randevu          # ajanın curl ile çağırdığı iç uç — X-Ic-Anahtar ile korumalı
GET  /api/uygunluk         # ajanın boş saat sorgusu
```

---

## 5. Fazlar

Her faz bağımsız çalıştırılabilir. Bir faz, kabul kriteri yeşil olmadan kapanmaz.

### Faz 0 — İskelet ve altyapı
`docker-compose.yml`: postgres:17 + OpenWA (`ENGINE_TYPE=whatsapp-web.js`, `DATABASE_TYPE=postgres`, `AUTO_START_SESSIONS=true`, `RATE_LIMIT_*` açık). `db.py` ile 5 tablo. `requirements.txt`.
**Kabul:** `docker compose up -d` → OpenWA dashboard `:2785`'te açılıyor; `pytest tests/test_crm.py` yeşil.

### Faz 1 — CRM + bilgi tabanı
`crm.py` ve `kb.py` sözleşmeye göre.
**Kabul:** `pytest tests/test_crm.py tests/test_kb.py tests/test_randevu.py` yeşil.

### Faz 2 — Panel
FastAPI + HTMX, arayüz tamamen Türkçe, 5 sayfa (§1 tablosu). Erişim: `.env`'de `PANEL_PAROLA` + imzalı cookie.
*Atlandı: kullanıcı hesapları/roller — klinikte 2-3 kişi var.*
**Kabul:** `pytest tests/test_panel.py` yeşil; şifresiz erişim `/giris`'e yönleniyor; bilgi eklenince `.hermes.md` değişiyor.

### Faz 3 — Hermes kurulumu, klasöre sabitleme
```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```
`HERMES_HOME=./hermes-home` projenin `.env`'ine ve `scripts/kurulum.sh`'a yazılır — **global `~/.hermes` hiç kullanılmaz.**

`hermes-home/config.yaml`:
```yaml
model:
  default: "openai/gpt-5.6-luna"
  reasoning_effort: medium
terminal:
  backend: local
agent:
  max_turns: 30
memory:
  memory_enabled: false     # geçmişin kaynağı Postgres
```

`hermes-home/SOUL.md` — resepsiyonist kimliği. Sert sınırlar: teşhis koymaz, ilaç önermez, tıbbi tavsiye vermez; fiyat/randevu/klinik bilgisi dışına çıkmaz; bilgi tabanında olmayan hiçbir şeyi uydurmaz, bilmiyorsa "personelimiz size dönecek" der; kısa ve sade Türkçe yazar.

**Kabul:** `HERMES_HOME=./hermes-home hermes config check` temiz; `hermes -z "fiyatlarınız nedir?"` bilgi tabanından cevap veriyor; `ls ~/.hermes` değişmemiş.

### Faz 4 — Köprü
`openwa.py`, `ajan.py`, `main.py` webhook ucu. OpenWA'da webhook kaydı: `events: ["message.received"]`, `secret`, `filters: isGroup=false` (grup mesajları elenir). Zaman aşımı/hata → hastaya "birazdan dönüş yapılacak" + panelde işaret.
**Kabul:** `pytest tests/test_webhook.py` yeşil. QR okutulup test telefonundan soru sorulunca doğru cevap geliyor; iki mesaj da `gorusmeler`'de.

### Faz 5 — Composio MCP + randevu skill'i
```yaml
mcp_servers:
  composio:
    url: "https://<composio-mcp-url>"
    headers:
      Authorization: "Bearer ${COMPOSIO_API_KEY}"
```
`skills/randevu-al/SKILL.md`: uygun saat sor → `GET /api/uygunluk` → Takvim etkinliği → onay maili → `POST /api/randevu`.
**Kabul:** WhatsApp'tan "yarın 15:00'e randevu" → Takvim etkinliği + onay maili + panelde randevu. Aynı saate ikinci talep → doluluk bildirimi.

### Faz 6 — Bağlantı sağlık takibi
Composio'nun ücretsiz katmanında bağlantılar düşüyor; WhatsApp oturumu da uzun sessizlikte kopabilir. Sessiz kopma = randevu yazılamaz ve kimse fark etmez.

`saglik.py` — FastAPI'nin `asyncio` döngüsünde 10 dakikada bir:
- Composio bağlı hesap durumu; token süresi dolmuşsa önce yenileme denenir (düzelirse kimse rahatsız edilmez, sadece log)
- OpenWA `GET /api/sessions/{S}` ile oturum durumu
- İki ardışık hatadan sonra: panelde kırmızı banner + "Yeniden bağlan" butonu **ve** `YONETICI_EPOSTA`'ya tek mail. Düzelene kadar tekrar mail yok; düzelince "bağlantı geri geldi" maili.

*Atlandı: Prometheus, Sentry, ayrı monitoring servisi — tek tablo + tek döngü yetiyor.*

**Kabul:** `pytest tests/test_saglik.py` yeşil; anahtar bilerek bozulunca 20 dk içinde banner + tek mail; düzeltilince banner kayboluyor.

### Faz 7 — VPS'e çıkış
GitHub repo. `.gitignore`: `.env`, `openwa-data/`, `hermes-home/sessions|memories`. `scripts/vps-deploy.sh`: git pull → `docker compose up -d` → uygulama restart. systemd birimi. nginx + Let's Encrypt; **dışarıya yalnız panel açık**, OpenWA dashboard'u localhost'ta kalır (SSH tüneliyle erişilir). Günlük `pg_dump` cron'u.
**Kabul:** VPS'te QR okutulup WhatsApp'tan cevap alınıyor; reboot sonrası her şey kendiliğinden ayağa kalkıyor; yedek dosyası oluşuyor.

### Faz 8 (sonraya) — Meta reklam modülü
Composio'nun Meta Ads araçları aynı MCP bağlantısına eklenir, ayrı skill yazılır. Bu fazda kod yazılmaz.

---

## 6. Testler

`tests/` altında pytest — **hiçbiri LLM çağırmaz.** Detay: `tests/README.md`.

| Dosya | Kapsam |
|---|---|
| `test_crm.py` | kişi upsert tekilliği, görüşme bağlama, wa_message_id idempotansı |
| `test_kb.py` | `.hermes.md` üretimi: aktif/pasif ayrımı, kategori başlıkları, idempotent yazma |
| `test_randevu.py` | çakışma, sınır teması, geçmiş tarih, çalışma saati dışı, iptal sonrası yeniden açılma |
| `test_webhook.py` | HMAC 401/200, gövde manipülasyonu, tekrar teslimatta tek kayıt, telefon ayıklama |
| `test_panel.py` | kimlik doğrulama, bilgi ekleme → `.hermes.md`, randevu sıralaması |
| `test_saglik.py` | tek hata sessiz, iki hata uyarı, uyarı tekrarlanmaz, düzelme bildirimi |

LLM'li uçtan uca akış test edilmez — Faz 4/5 kabul kriterlerinde manuel smoke test.

---

## 7. Uçtan uca doğrulama (insan eliyle)

1. `docker compose up -d && pytest` → hepsi yeşil
2. OpenWA dashboard'undan QR okut → oturum `ready`
3. Panelden bir hizmet + fiyat + çalışma saati gir
4. Test telefonundan "fiyatlarınız ne kadar?" → doğru cevap; iki mesaj da Görüşmeler'de
5. "Yarın 15:00'e randevu istiyorum" → Takvim etkinliği + onay maili + panelde randevu
6. Aynı saate ikinci randevu → doluluk bildirimi
7. Composio anahtarını boz → 20 dk içinde banner + tek mail

---

## 8. Riskler ve bilinçli kabuller

- **Ban riski.** OpenWA resmi olmayan bir istemci (whatsapp-web.js). Klinik için **ayrı numara** kullanılacak, ilk günler ısıtma yapılacak (normal kullanıcı gibi davran, gün bir'de yayına başlama), toplu mesaj yok, `RATE_LIMIT_*` açık. Numara kısıtlanırsa geri alma yolu yok.
- **Sağlık/KVKK.** OpenWA'nın kendi dokümanı sağlık ve GDPR kapsamındaki kullanımlar için "onaylı değil, Meta Cloud API kullanın" diyor. Karar bilinçli alındı. Hasta verisi kendi VPS'imizdeki Postgres'te; Composio'ya yalnız takvim/mail verisi gidiyor. Klinik için KVKK aydınlatma metni gerekebilir — bu projenin kapsamı dışında, müşteriye bildirilecek. **Cloud API'ye geçiş yolu açık:** yalnız `openwa.py` değişir, köprünün geri kalanı aynı kalır.
- **Ajan sınırları.** `SOUL.md` teşhis/ilaç/tıbbi tavsiyeyi yasaklar; bilgi tabanında olmayanı uydurmaz, personele yönlendirir.
- **Model.** `gpt-5.6-luna` düşük maliyet katmanı (1.05M context, $0.20/$1.20 per 1M). Randevu akışında yetersiz kalırsa `config.yaml`'da tek satırla `gpt-5.6-terra`.
- **RAM.** whatsapp-web.js ~400MB/oturum + Postgres + OpenWA + FastAPI → VPS'te **en az 2GB RAM**.
- **Kullanılmayan malzeme.** `referans/crm-main.zip` (trycompai/crm) kullanılmadı: B2B satış CRM'i, deal/pipeline domaini randevuya uymuyor, içinde ikinci bir ajan (eve) var, Vercel'e bağımlı parçaları VPS'e uyarlamak yarına yetişmez. Referans olarak duruyor.
