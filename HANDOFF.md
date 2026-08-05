# Devir Notu

**Son güncelleme:** 2026-08-06 · **Durum:** çalışır, 171 test yeşil, QR bekliyor

Yeni bir oturum bu dosyayı okuyarak devam edebilir. Gereksinimler `PRD.md`'de,
düzeltilen kusurlar `KUSURLAR.md`'de, kullanım `README.md`'de.

---

## Bir cümlede

Bir **müşteri kliniğine** teslim edilecek WhatsApp resepsiyonist ajanı: hasta
WhatsApp'tan yazar, ajan klinik bilgi tabanından cevaplar, doktor seçip randevu
açar, Google Takvim'e yazar; personel Türkçe panelden yönetir.

## Nerede duruyor

| Katman | Durum |
|---|---|
| Postgres + OpenWA (Docker) | ✅ çalışıyor |
| CRM + bilgi tabanı | ✅ |
| Panel (dark, grafikli) | ✅ |
| Hermes ajanı (klasöre özel) | ✅ bilgi tabanından cevap veriyor, sınırları tutuyor |
| Köprü (webhook ↔ ajan ↔ WhatsApp) | ✅ OpenWA'nın gerçek HMAC imzası doğrulandı |
| Doktorlar + otomatik dağıtım | ✅ |
| Kullanıcılar + roller + işlem izi | ✅ |
| Randevu hatırlatmaları | ✅ kod hazır, canlıda hiç mesaj göndermedi |
| Composio (Takvim + Gmail) | ⏳ **anahtar bekliyor** — bu olmadan takvim/mail adımı çalışmaz |
| WhatsApp QR | ⏳ **müşteri okutacak** |
| VPS | ⏳ scriptler hazır, çıkılmadı |

## Sırada ne var

1. **QR okutmak.** `http://localhost:2785` → `klinik` oturumu. Klinik için **ayrı
   bir numara** — OpenWA resmi olmayan istemci, ban geri alınamıyor.
2. **Composio.** Hesap aç, Google Takvim + Gmail bağla, `.env`'e `COMPOSIO_API_KEY`
   ve `COMPOSIO_MCP_URL` yaz, `./scripts/composio-ac.sh` çalıştır.
3. **VPS.** `README.md` § VPS'e çıkış. `.env`'de `AJAN_PROVIDER=openai`,
   `AJAN_MODEL=gpt-5.6-luna`, `OPENAI_API_KEY` doldur. En az 2GB RAM.
4. *(sonraya)* Meta reklam modülü — PRD Faz 8.

## Çalıştırma

```bash
docker compose up -d
set -a && source .env && set +a
.venv/bin/uvicorn app.main:app --port 8000
```

Panel `http://localhost:8000` · geliştirme girişi: `admin` / `.env`'deki `PANEL_PAROLA`
OpenWA dashboard `http://localhost:2785`

```bash
.venv/bin/python -m pytest        # 171 test, hiçbiri LLM çağırmaz
```

## Bilinmesi gerekenler

**Testler ayrı veritabanı kullanır** (`klinik_crm_test`). Bu kasıtlı: `conftest`
TRUNCATE atıyor, canlıya yönelseydi bir `pytest` komutu hasta kayıtlarını silerdi.
Başka bir makinede ilk koşudan önce:
`docker compose exec postgres createdb -U klinik klinik_crm_test`

**Hatırlatma nöbetçisi varsayılan olarak açık.** QR okutulunca gerçek hastalara
mesaj gitmeye başlar. Test ederken `.env`'e `HATIRLATMA_NOBETCISI=0`.

**Toplu mesaj mimari olarak yasak.** Detay `README.md` § Toplu mesaj yasağı.
`test_toplu_gonderim_fonksiyonu_yok` bu kuralı her koşuda denetler — kasıtlıdır,
"gereksiz" diye silinmemeli.

**Ajan bu klasöre özel.** `HERMES_HOME=./hermes-home`; global `~/.hermes` hiç
kullanılmıyor ve kullanılmamalı.

**Postgres 5434'te.** 5432/5433 makinede başka projelerde dolu.

**Python 3.13 gerekli** (kodda `X | None` var). Sistem `python3`'ü 3.9.

## Verilmiş kararlar — yeniden tartışılmayacak

| Karar | Gerekçe |
|---|---|
| `referans/crm-main.zip` (trycompai/crm) **kullanılmıyor** | B2B satış CRM'i; deal/pipeline domaini randevuya uymuyor, içinde ikinci bir ajan (Vercel eve) var, Vercel'e bağımlı. Kullanıcı onayladı. |
| WhatsApp = OpenWA + whatsapp-web.js | Baileys'ten düşük ban riski; webhook mimarisi her mesajı deterministik kaydediyor |
| Test ZAI/GLM, canlı OpenAI gpt-5.6-luna | Kullanıcının kararı; `.env`'de iki satır |
| Panel = FastAPI + HTMX tek servis | Hermes zaten Python; tek venv, tek systemd birimi |
| Hasta verisi kendi Postgres'imizde | KVKK; Composio'ya yalnız takvim/mail verisi gider |
| Grafiklerde tek hue (mavi) | Hepsi büyüklük gösteriyor, kimlik değil |

## Mimarinin kritik noktaları

**Çakışma doktor bazında.** İki hekim aynı saatte iki hastaya bakabilir, aynı hekim
bakamaz (`doktor_id IS NOT DISTINCT FROM`). Doktor tanımlı değilse sistem tek
hekimli klinik gibi çalışır — bu geriye uyumluluk kasıtlı.

**Ajan doktor seçmez.** "Farketmez" diyen hastada `doktor_id` gönderilmez, sistem
o saatte en az yüklü hekime dağıtır. Seçim kararlıdır (eşitlikte ada göre), ajan
iki kez sorunca fikir değiştirmez.

**Konuşma geçmişinin tek kaynağı Postgres.** `hermes -z` her çağrıda son 10
görüşmeyi prompt'ta alır; Hermes session/memory kullanılmıyor. İki yerde state
tutmamak için.

**Ajanın söyleyebileceği her şey `.hermes.md`'de.** `bilgi_tabani` tablosundan
üretilir, panelden kaydedilince anında geçerli olur (restart gerekmez).

**İki ayrı yetki.** Panel cookie'si (personel) ve `X-Ic-Anahtar` (ajanın CRM'e
yazması). Biri diğerini açmaz — yönetici oturumu bile iç API'yi açmıyor.

## Dosya haritası

```
app/main.py       webhook + panel + iç API
app/crm.py        kişi, görüşme, randevu, doktor (çakışma kuralları burada)
app/kb.py         bilgi tabanı → .hermes.md
app/ajan.py       hermes -z köprüsü
app/openwa.py     WhatsApp istemcisi + HMAC doğrulama
app/hatirlatma.py randevu hatırlatmaları + giden mesaj kilitleri
app/kullanici.py  kullanıcılar, parolalar, işlem izi
app/saglik.py     bağlantı nöbetçisi (Composio + WhatsApp)
hermes-home/      ajanın kimliği (SOUL.md), yapılandırması, 3 skill
tests/            171 test, LLM'siz
scripts/          kurulum, vps-deploy, systemd, nginx, yedekleme, composio
```

## Son doğrulama (2026-08-06)

- 171 test yeşil
- Panel 6 sayfa 200 dönüyor, rol ayrımı tutuyor (personel → 403)
- Ajan bilgi tabanından cevap veriyor, teşhis/ilaç sorularını reddediyor
- Aynı saate 3 hasta → 3 farklı hekim; 4. → "müsait doktor yok"
- Randevu açılınca 24s + 1s hatırlatma planlanıyor; iptalde düşüyor
- Başkasının numarasıyla iptal denemesi → 403
