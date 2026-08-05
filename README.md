# Klinik WhatsApp Resepsiyonist Ajanı

Hasta WhatsApp'tan yazar; ajan klinik bilgi tabanından cevap verir, randevu ayarlar,
Google Takvim'e yazar. Her mesaj CRM'e kaydedilir. Personel Türkçe panelden yönetir.

**Yığın:** Hermes Agent (ajan) · OpenWA (WhatsApp) · FastAPI + HTMX (köprü + panel) ·
Postgres · Composio MCP (Google Takvim + Gmail)

**Devir notu: [HANDOFF.md](HANDOFF.md)** — durum, sıradaki adımlar, verilmiş kararlar.
Gereksinimler: [PRD.md](PRD.md) · Kusurlar: [KUSURLAR.md](KUSURLAR.md) · Testler: [tests/README.md](tests/README.md)

---

## Hızlı kurulum

```bash
./scripts/kurulum.sh          # .env üretir, Docker'ı kaldırır, şemayı kurar, oturum açar
```

Sonra `.env` içinde şunları doldur:

| Değişken | Ne için |
|---|---|
| `PANEL_PAROLA` | İlk kurulumda `admin` kullanıcısının parolası (en az 8 karakter). Sonrasında kullanıcılar panelden yönetilir |
| `ZAI_API_KEY` | Test ortamı modeli (GLM) |
| `OPENAI_API_KEY` | VPS'te `gpt-5.6-luna` için |
| `COMPOSIO_API_KEY`, `COMPOSIO_MCP_URL` | Google Takvim + Gmail |
| `YONETICI_EPOSTA`, `SMTP_*` | Bağlantı koptuğunda uyarı maili |

Köprüyü başlat:

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

| Ne | Nerede | Kim |
|---|---|---|
| Panel | http://localhost:8000 | Klinik personeli (kullanıcı adı + parola) |
| OpenWA dashboard (QR) | http://localhost:2785 | Klinik personeli |
| Hasta | WhatsApp | — |

**Son adım:** OpenWA dashboard'undan `klinik` oturumunun QR'ını telefonla okut.

## Model seçimi

`.env` içindeki iki satır belirler. Boş bırakılırsa `hermes-home/config.yaml`
geçerli olur (`gpt-5.6-luna`).

```bash
AJAN_PROVIDER=zai      AJAN_MODEL=glm-4.6          # test
AJAN_PROVIDER=openai   AJAN_MODEL=gpt-5.6-luna     # canlı
```

## Composio (Google Takvim + Gmail)

```bash
./scripts/composio-ac.sh      # .env'deki anahtarları config.yaml'a işler
```

## Testler

```bash
.venv/bin/python -m pytest
```

Hiçbiri LLM çağırmaz. `DATABASE_URL` yoksa DB testleri atlanır.

## VPS'e çıkış

```bash
# sunucuda, /opt/hermes-klinik altında
./scripts/kurulum.sh
sudo cp scripts/klinik-kopru.service /etc/systemd/system/
sudo systemctl enable --now klinik-kopru
sudo cp scripts/nginx-klinik.conf /etc/nginx/sites-available/klinik
sudo ln -s /etc/nginx/sites-available/klinik /etc/nginx/sites-enabled/
sudo certbot --nginx -d panel.klinigin-alanadi.com
echo "0 3 * * * /opt/hermes-klinik/scripts/yedekle.sh" | sudo crontab -
```

Güncelleme: `./scripts/vps-deploy.sh`

`.env`'de webhook adresini sunucuya göre ayarla:
`KOPRU_WEBHOOK_URL=http://172.17.0.1:8000/webhook/whatsapp` (Linux'ta docker0 köprüsü)
ve `KOPRU_HOST=172.17.0.1`.

**En az 2GB RAM gerekir** — whatsapp-web.js Chromium çalıştırır.

## Randevu hatırlatmaları ve toplu mesaj yasağı

Klinik iki otomatik mesaj gönderir: randevudan **24 saat önce** teyit isteği,
**1 saat kala** hatırlatma. Hasta "iptal" yazarsa ajan randevuyu iptal eder,
"evet" derse onaylar.

**Toplu mesaj gönderilemez — bu bir ayar değil, mimari bir kilit:**

| Kilit | Nasıl |
|---|---|
| Alıcı listesi alan fonksiyon yok | `app/hatirlatma.py` ve `app/openwa.py`'de tek alıcılı imza; `test_toplu_gonderim_fonksiyonu_yok` bunu her koşuda denetler |
| Her mesaj bir randevuya bağlı | Randevusu olmayan numaraya buradan mesaj gidemez |
| Randevu başına en çok 2 mesaj | `UNIQUE(randevu_id, tur)` — nöbetçi iki kez çalışsa da tekrar gitmez |
| Saatlik / günlük tavan | `GIDEN_SAATLIK_TAVAN=20`, `GIDEN_GUNLUK_TAVAN=100`; dolunca gönderim durur, kuyruk sonraki tura kalır |
| Gönderimler arası bekleme | `GIDEN_ARALIK_SN=8` — arka arkaya yığın görüntüsü vermez |
| Sessiz saat | 21:00–09:00 arası mesaj gitmez, sabaha ertelenir |
| Tur başına iş sınırı | Bir turda en çok 10 hatırlatma |

Ajanın skill'i de bunu biliyor: personel toplu mesaj isterse "Bu panelden
yapılamıyor" der.

## Ajanın sınırları

`hermes-home/SOUL.md` içinde yazılı ve tartışmaya kapalı: teşhis koymaz, ilaç
önermez, tıbbi tavsiye vermez, bilgi tabanında olmayanı uydurmaz, fiyat pazarlığı
yapmaz. Acil belirtilerde 112'ye yönlendirir.

Ajanın söyleyebileceği her şeyin kaynağı `.hermes.md` — panelden doldurulur,
anında geçerli olur.

## Bilinmesi gerekenler

- **Ayrı bir WhatsApp numarası kullanın.** OpenWA resmi olmayan bir istemci; ban
  riski gerçek ve geri dönüşü yok. İlk günler normal kullanıcı gibi davranın,
  toplu mesaj atmayın. `RATE_LIMIT_*` açık kalsın.
- **KVKK:** OpenWA'nın kendi dokümanı sağlık sektörü için "onaylı değil, Meta Cloud
  API kullanın" diyor. Hasta verisi kendi sunucunuzdaki Postgres'te; Composio'ya
  yalnız takvim/mail verisi gider. Klinik için aydınlatma metni gerekebilir.
  Cloud API'ye geçerken yalnız `app/openwa.py` değişir.
- **Yedek:** `scripts/yedekle.sh` günlük `pg_dump` alır, 30 gün saklar.

## Dosya haritası

```
app/main.py      webhook + panel + iç API
app/crm.py       kişi, görüşme, randevu (çakışma kuralları burada)
app/kb.py        bilgi tabanı → .hermes.md
app/ajan.py      hermes -z köprüsü
app/openwa.py    WhatsApp istemcisi + HMAC doğrulama
app/saglik.py    bağlantı nöbetçisi
app/hatirlatma.py randevu hatırlatmaları + giden mesaj kilitleri
app/kullanici.py  kullanıcılar, parolalar, işlem izi
hermes-home/     ajanın kimliği, yapılandırması, skill'leri (bu klasöre özel)
```
