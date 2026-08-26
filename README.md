# Klinik WhatsApp Resepsiyonist Ajanı

Hasta WhatsApp'tan yazar; ajan klinik bilgi tabanından cevap verir, randevu ayarlar,
Google Takvim'e yazar. Her mesaj CRM'e kaydedilir. Personel Türkçe panelden yönetir.

**Yığın:** Kendi in-process ajanımız (tool-calling döngüsü, `app/ajan.py`) ·
OpenWA (WhatsApp) · FastAPI + HTMX (köprü + panel) · Postgres ·
Composio MCP (Google Takvim + Gmail)

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
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Bağlantı koptuğunda uyarı Telegram mesajı. @BotFather'dan bot açıp token al, bota mesaj atıp `https://api.telegram.org/bot<TOKEN>/getUpdates` ile chat_id'ni öğren |
| `RAPOR_ARALIK_SN` | Haftalık kullanım özeti aralığı (sn) — mesaj adedi + token, $ maliyet yok, yalnızca Telegram'a gider |

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

`.env` içindeki iki satır belirler:

```bash
AJAN_PROVIDER=zai      AJAN_MODEL=glm-4.6          # test
AJAN_PROVIDER=openai   AJAN_MODEL=gpt-5.6-luna     # canlı
```

## Google Takvim (doktorun cebinde randevu)

Randevu açılınca kliniğin Google Takvim'ine etkinlik yazılır ve **hekimin
e-postası davetli olarak eklenir** — Google etkinliği davetlinin kendi takvimine
düşürür, hekim randevuyu kendi telefonunda, kendi rengiyle görür. Randevu iptal
edilince etkinlik silinir (`randevular.google_event_id`).

**Hekim tarafında kurulum yoktur:** ayrı Google hesabı bağlama, ayrı takvim
oluşturma, abone olma yok. Tek gereken, hekimin e-postasının panelde (Doktorlar
sayfası) tanımlı olması.

```bash
# 1. Composio'da Google Calendar'ı kliniğin hesabıyla bağla
# 2. Araç adlarını doğrula (slug'lar belgelenmiş değil)
.venv/bin/python -m app.gtakvim --kesfet
# 3. .env: COMPOSIO_API_KEY, TAKVIM_KULLANICI (connected account user_id)
#    GOOGLE_TAKVIM_ID varsayılan "primary"
```

`COMPOSIO_API_KEY` ya da `TAKVIM_KULLANICI` boşsa takvim tamamen kapalıdır;
randevu akışı etkilenmez. Composio'ya ulaşılamazsa da randevu düşmez — CRM
kaydı geçerlidir, hataya yalnız log düşer, hastaya normal onay gider.

**Neden davetli, neden renk yok:** Composio'nun Takvim araçlarında `colorId`
alanı yok (`CREATE/UPDATE/PATCH_EVENT` — 2026-08-11'de doğrulandı), yani
"her hekime bir Google rengi" yapılamıyor. Davetli modelinde renk zaten hekimin
kendi telefonunda ayarlanır ve hekim yalnız kendi randevularını görür.

**Doktora WhatsApp bildirimi varsayılan olarak kapalıdır** (`DOKTORA_BILDIRIM=0`)
— hekim randevuyu takvimde görüyor. Panelde tanımlı doktor telefonu yalnız klinik
kaydıdır; `DOKTORA_BILDIRIM=1` yazılmadıkça o numaraya mesaj gitmez.

## Composio (Gmail)

Gmail bağlantısı sağlık nöbetçisi tarafından izlenir (`app/saglik.py`). Ajanın
kendi Composio aracı yoktur — takvim işini `app/gtakvim.py`, Instagram'ı
`app/instagram.py` doğrudan REST üzerinden yapar. `COMPOSIO_MCP_URL` Hermes
döneminden kalmadır, hiçbir kod okumaz.

## Fiyatlar ve kampanyalar

Panel → **Fiyat ve Kampanya** (yalnız yönetici).

**Fiyatın tek kaynağı `hizmetler` tablosudur.** Bilgi Tabanı'nda "Fiyatlar"
kategorisi bilerek yoktur: aynı hizmet iki yerde farklı fiyatla yazılsaydı
ajanın hangisini söyleyeceği belirsiz kalırdı. Mevcut kurulumda bir kez:

```bash
.venv/bin/python scripts/fiyat-goc.py --dene   # ne taşınacak, göster
.venv/bin/python scripts/fiyat-goc.py          # taşı
```

Sayı ayıklanamayan kayıt silinmez, `genel` kategorisine alınır ve raporlanır.

**Kampanya duyuru göndermez.** Yalnızca hasta fiyat sorduğunda ajanın söyleyeceği
indirimi belirler; kimseye kendiliğinden mesaj gitmez. Toplu gönderim bu
projede mimari olarak yasak (bkz. § Toplu mesaj yasağı) ve
`test_kampanya_gonderim_yapmaz` her koşuda `app/hizmet.py`'de gönderim izi
olmadığını denetler.

Kampanya seçim kuralları — ajan aynı soruya iki kez farklı fiyat söylemesin diye
kararlı: pasif ya da süresi geçmiş kampanya uygulanmaz; hizmete özel kampanya
"tüm hizmetler" kampanyasına baskındır; aynı düzeyde birden çok aday varsa
indirimi yüksek olan, eşitlikte önce tanımlanan seçilir.

Fiyat ya da kampanya kaydedilince `.hermes.md` anında yeniden üretilir —
restart gerekmez. Panelde "Ajan ne diyecek" sütunu, kaydetmeden önce hastaya
gidecek cümleyi gösterir.

## Konum gönderme

Hasta adres veya yol tarifi sorduğunda ajan, yazılı cevabın arkasından WhatsApp
harita iğnesi gönderir. Açmak için `.env`:

```
KLINIK_KONUM=40.9812,29.0578     # Google Haritalar'da pine sağ tıkla → iki sayı
```

Nasıl çalışıyor: ajan cevabının sonuna `[KONUM]` işareti koyar, köprü işareti
siler (hastaya da panele de gitmez) ve iğneyi arkasından atar. Koordinat
sabittir — **ajan koordinat üretmez**, uydurulmuş bir enlem/boylam hastayı
yanlış adrese gönderirdi.

`KLINIK_KONUM` boşsa iğne gitmez, adres yalnız yazıyla verilir; hiçbir şey
bozulmaz. Instagram'da iğne yok (Composio araç setinde karşılığı yok), orada
işaret yalnız temizlenir.

Not: bu OpenWA sürümü konum mesajında yalnız enlem/boylam kabul ediyor —
`name`/`address` alanları 400 dönüyor, yani iğnenin üstünde klinik adı yazmaz.

## Instagram kanalı (yalnız bilgilendirme)

Instagram DM'lerine ajan cevap verir, ama **yalnızca bilgi** verir:

| | Instagram | WhatsApp |
|---|---|---|
| SSS, fiyat, çalışma saatleri | ✅ | ✅ |
| Randevu açma / iptal | ❌ WhatsApp'a yönlendirir | ✅ |
| Randevu hatırlatması | ❌ hiç gitmez | ✅ |

Bu daraltma keyfi değil, iki teknik zorunluluk:

1. **Composio'nun Instagram araç setinde trigger yok** — gelen DM'i haber veren
   webhook yok, mesajlar yoklanıyor (varsayılan 30 sn). Bilgilendirme için sorun
   değil; randevu çakışması için olurdu.
2. **Instagram'da onaylı şablon yok** — 24 saatlik pencere dışında Meta serbest
   metni reddeder. Randevudan 24 saat önce gidecek hatırlatma her zaman pencere
   dışındadır, yani bu kanalda hatırlatma teknik olarak mümkün değil.

Açmak için:

```bash
# 1. Composio'da Instagram'ı bağla (Business/Creator hesap şart)
# 2. Araç adlarını doğrula — slug'lar belgelenmiş değil, Composio değiştirebilir
.venv/bin/python -m app.instagram --kesfet
# 3. .env: INSTAGRAM_KULLANICI, INSTAGRAM_HESAP_ID, KLINIK_WHATSAPP_NUMARASI
# 4. Servisi yeniden başlat
```

Çıktı beklenenden farklıysa `.env`'deki `IG_ARAC_*` satırlarını düzeltin — kod
değişmez. `INSTAGRAM_KULLANICI` boşken kanal tamamen kapalıdır: nöbetçi hiç
başlamaz, Composio çağrısı yapılmaz, sağlık uyarısı çıkmaz.

**Taşıma seçimi (2026-08-19):** `INSTAGRAM_SAGLAYICI=unipile` ile Composio
yoklaması yerine Unipile webhook'u kullanılır (`POST /webhook/unipile`,
`UNIPILE_*` env'leri; kurulum ve webhook kaydı için HANDOFF 2026-08-19
bölümü). Kapsam kilidi iki yolda da aynıdır.

**İki kanalın bütçesi ayrıdır.** Giden mesaj tavanları (`GIDEN_SAATLIK_TAVAN`)
yalnız WhatsApp mesajlarını sayar — amacı WhatsApp numarasının kapanmasını
önlemek, ve Instagram cevapları o numaradan çıkmıyor. Yoğun bir Instagram günü
gerçek randevu hatırlatmalarını durduramaz.

**İki nöbetçi birden var, ikisi de gerekli:** `composio` bağlantının canlılığına
bakar (OAuth düşerse yakalar), `instagram` yoklama döngüsünün canlılığına bakar.
İkincisi olmasaydı bağlantı ACTIVE kalıp döngü sessizce ölebilir ve DM'ler
cevapsız birikirdi. İkisi de panelde **Bağlantı Durumu** altında görünür.

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
| Alıcı listesi alan fonksiyon yok | `app/hatirlatma.py`, `app/openwa.py` ve `app/devri.py`'de tek alıcılı imza; `test_toplu_gonderim_fonksiyonu_yok` bunu her koşuda denetler. Tek beyaz listeli istisna `devri.personel_bildir` — numaraları panelden yönetilen personel devir bildirimi; oraya hasta listesi eklenirse test kırılır |
| Her mesaj bir randevuya bağlı | Randevusu olmayan numaraya buradan mesaj gidemez |
| Randevu başına en çok 2 mesaj | `UNIQUE(randevu_id, tur)` — nöbetçi iki kez çalışsa da tekrar gitmez |
| Saatlik / günlük tavan | `GIDEN_SAATLIK_TAVAN=20`, `GIDEN_GUNLUK_TAVAN=100`; dolunca gönderim durur, kuyruk sonraki tura kalır |
| Gönderimler arası bekleme | `GIDEN_ARALIK_SN=8` — arka arkaya yığın görüntüsü vermez |
| Sessiz saat | 21:00–09:00 arası mesaj gitmez, sabaha ertelenir |
| Tur başına iş sınırı | Bir turda en çok 10 hatırlatma |

Ajanın kimliği de bunu biliyor: personel toplu mesaj isterse "Bu panelden
yapılamıyor" der.

**Devir bildirimi aynı kilitlerden geçer** (`app/devri.py:personel_bildir`).
Hasta yetkili isterse, telefonu kayıtlı aktif personele WhatsApp bildirimi gider.
Bildirimde yalnız hasta adı, telefon, devir nedeni ve "Panelden devralın." vardır —
hastanın serbest metni (KVKK) asla girmez. Saatlik/günlük tavan doluysa bildirim
gönderilmez ve loglanır; sessiz saatte ertelenmez, **düşürülür** (devir kaydı
panelde durur). Aynı hasta için `DEVIR_BILDIRIM_ARALIK_DK=15` içinde ikinci
bildirim gitmez.

## Ajanın sınırları

`hermes-home/SOUL.md` içinde yazılı ve tartışmaya kapalı: teşhis koymaz, ilaç
önermez, tıbbi tavsiye vermez, bilgi tabanında olmayanı uydurmaz, fiyat pazarlığı
yapmaz. Acil belirtilerde 112'ye yönlendirir.

Ajanın söyleyebileceği her şeyin kaynağı `.hermes.md` — panelden doldurulur,
anında geçerli olur.

## Mimari — üç katman, en ucuzdan en pahalıya

Her gelen mesaj otomatik olarak LLM'e gitmez. Sıralama (`app/main.py`):

1. **Kural katmanı** (`app/kural.py`, LLM'siz) — bilgi tabanındaki bir başlıkla
   net eşleşen soru (fiyat, çalışma saati, adres) doğrudan cevaplanır; hatırlatmaya
   tek kelimelik "evet"/"iptal" cevabı da burada LLM'siz işlenir.
2. **Hafif yol** (`app/hafif.py`, ucuz LLM) — kural katmanı eşleşmezse, bilgi
   soruları için araç şeması olmadan doğrudan `/chat/completions` çağrılır.
3. **Tam ajan** (`app/ajan.py`, tool-calling) — randevu açma/değiştirme/iptal gibi
   araç gerektiren işler burada; 7 aracı (`app/araclar.py`) çağırabilen elle
   yazılmış bir tool-calling döngüsü, framework yok.

Hedef: "100 mesajın 100'ü LLM'ye" değil, çoğu kural/API katmanında LLM'siz biter.

## Bilinmesi gerekenler

- **Ayrı bir WhatsApp numarası kullanın.** OpenWA resmi olmayan bir istemci; ban
  riski gerçek ve geri dönüşü yok. İlk günler normal kullanıcı gibi davranın,
  toplu mesaj atmayın. `RATE_LIMIT_*` açık kalsın.
- **KVKK:** OpenWA'nın kendi dokümanı sağlık sektörü için "onaylı değil, Meta Cloud
  API kullanın" diyor. Hasta verisi kendi sunucunuzdaki Postgres'te; Composio'ya
  yalnız takvim/mail verisi gider. Klinik için aydınlatma metni gerekebilir.
  Cloud API'ye geçerken yalnız `app/openwa.py` değişir.
- **Yedek:** `scripts/yedekle.sh` günlük `pg_dump` alır, 30 gün saklar.

## Doktora yeni randevu bildirimi

Randevu oluşunca, doktorun telefonu `Doktorlar` sayfasında kayıtlıysa, kendisine
WhatsApp'tan bir bildirim gider (`app/bildirim.py`). Kural sabit ve tek: yeni
randevu → bildir; panelden ayarlanan bir "bildirim tercihleri" ekranı yok,
kapatmak için `.env`'de `DOKTORA_BILDIRIM=0` yeterli.

**KVKK — veri minimizasyonu.** Bildirim metni yalnız hizmet adı, tarih/saat ve
hastanın adı/telefonunu içerir; hastanın serbest metin notu (şikayet/semptom)
hiç girmez — bu metin doktorun telefonunun kilit ekranında görünebilir.

## Demo verisi

Satış demosu için `DemoDent Ağız ve Diş Sağlığı Kliniği`: 4 doktor (İmplant,
Ortodonti, Estetik, Çocuk diş), ilgili fiyat listesi, birkaç genel bilgi kaydı.

```bash
.venv/bin/python scripts/demo-veri.py 905321112233   # demoda telefonu tutan kişinin numarası
```

Tekrar çalıştırılabilir (aynı isim varsa atlanır). Doktorların hepsine aynı
telefon yazılır — hangi doktor seçilirse seçilsin bildirim aynı telefona gider.

## Panelde durum etiketleri ve /rapor

Randevu durumu DB'de beş değer (`bekliyor`/`onayli`/`geldi`/`gelmedi`/`iptal`);
panelde `durum_etiketi` Jinja filtresiyle etiketlenir (`app/main.py`).
**`gelmedi` işaretlemesi elle yapılır** — "saati geçti, geldi denmedi → gelmedi"
otomasyonu bilerek yok: personel butona basmayı unuttuğunda gelen hasta gelmedi
sayılır ve raporun tamamı yalan olur. Ana sayfadaki **bekleyen işaretleme kutusu**
tarihi geçmiş ama hâlâ `bekliyor`/`onayli` duran randevuları tek ekranda listeler.
Gelmeyen randevu slotu işlemeye devam eder — çakışma sorgusunda ve dolulukta sayılır.
Randevuyu kim açtığı `randevular.kaynak`'ta durur: panel formu `panel`,
ajan (`/api/randevu`) `ajan` yazar.

**`/rapor` — kayıp ve kurtarma raporu** (yalnız yönetici, ay seçilebilir;
`app/kazanc.py`). Üç blok: ayın alınan/teyit/gelen/gelmeyen/iptal sayıları,
gelmeme oranı ve gelmeyenin TL karşılığı; hatırlatmaya "iptal" cevabı verip slotu
erken boşaltan randevular; ajanın açtığı randevular ve kaçtı mesai dışında.
Fiyat eşleştirmesi `randevular.hizmet` serbest metniyle `hizmetler.ad` arasında
büyük/küçük harf duyarsızdır; eşleşmeyen kayıtlar **"fiyatı bilinmiyor"** olarak
ayrı sayılır ve TL toplamına girmez — ortalama fiyatla doldurulmaz.

## Dosya haritası

```
app/main.py      webhook + panel + iç API
app/crm.py       kişi, görüşme, randevu (çakışma kuralları burada)
app/kb.py        bilgi tabanı → .hermes.md
egitim/          ajan eğitim merkezi — vendor konsolu (yazarak-eğit + site kazıma + KB düzenle)
                 `uvicorn egitim.sunucu:app --port 8001`; müşteri panelinin `/egitim`'i
                 yalnız yazarak-eğit'i çağırır (URL/tarama vendor'da)
app/kural.py     LLM'siz kural katmanı (bilgi tabanı eşleşmesi + hatırlatma cevabı)
app/hafif.py     bilgi soruları için araçsız, ucuz LLM çağrısı
app/ajan.py      tam ajan — tool-calling döngüsü
app/araclar.py   ajanın 7 randevu aracı (/api/* uçlarına ince katman)
app/bildirim.py  doktora yeni randevu bildirimi (WhatsApp, tek alıcı)
app/iyilestirme.py  salt-okunur öneri taraması (bilgi tabanı boşluğu, tekrar soru)
app/openwa.py    WhatsApp istemcisi + HMAC doğrulama
app/saglik.py    bağlantı nöbetçisi
app/hatirlatma.py randevu hatırlatmaları + giden mesaj kilitleri
app/devri.py     insan devri: personel bildirimi + otomatik geri alma
app/kazanc.py    /rapor — kayıp ve kurtarma raporu (yalnız yönetici)
app/kullanici.py  kullanıcılar, parolalar, işlem izi
hermes-home/SOUL.md  ajanın kimliği (bu klasöre özel)
scripts/demo-veri.py  satış demosu için DemoDent verisi
```
