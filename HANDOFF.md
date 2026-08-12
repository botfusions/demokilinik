# Devir Notu

**Son güncelleme:** 2026-08-12 · **Durum:** **canlıda çalışıyor** —
`demoklinik.botfusions.com`, WhatsApp bağlı, gerçek randevu açıldı ve iptal
edildi, Google Takvim'e düşüyor. 356 test yeşil.

## 2026-08-12: Canlı env envanteri, geç kalma cevabı, hafif yol üretimde

### Canlıdaki env — tek doğru kayıt burası

Yerel `.env` ile Coolify env'i AYRI. Yerelde doğru olan bir değer canlıda
yoksa kod sessizce varsayılana düşer; bu bölüm o farkı görünür tutmak için var.
Kontrol yöntemi (SSH):

```
ssh root@5.182.33.26 'docker exec $(docker ps -q --filter name=ar26914sno6qomlwm7q0fmp2) env | cut -d= -f1 | sort'
```

Kodun okuduğu tüm değişkenleri çıkarmak için: `app/*.py` içinde `environ` araması.

**Canlıda DOLU (2026-08-12):** `AJAN_PROVIDER`, `AJAN_MODEL`, `AJAN_EFFORT`,
`OPENAI_API_KEY` (luna geçişi yapılmış), `COMPOSIO_API_KEY`, `TAKVIM_KULLANICI`,
`DATABASE_URL`, `COOKIE_SECRET`, `IC_API_ANAHTARI`, `WEBHOOK_SECRET`,
`OPENWA_*`, `PANEL_PAROLA`, `POSTGRES_*`, `TZ`, `SOURCE_COMMIT` (Coolify veriyor).

**Canlıda EKSİK — sonucuyla birlikte:**

| Eksik | Ne oluyor |
|---|---|
| `CALISMA_GUNLERI` | **Canlı hata.** Varsayılan `1,2,3,4,5` (Pzt-Cum). `.hermes.md` "hafta içi **ve cumartesi**" diyor → ajan cumartesi öneriyor, `randevu_olustur` 422 dönüyor, hasta boşa çevriliyor. Coolify'a `1,2,3,4,5,6` eklenmeli. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Sağlık uyarıları ve haftalık rapor hiçbir yere gitmiyor (`saglik.py:113` log'a yazıp dönüyor). WhatsApp oturumu düşerse kimse haber almaz. SMTP/`YONETICI_EPOSTA` da yok → ikinci kanal da kapalı. |
| `KLINIK_KONUM` | `[KONUM]` iğnesi ölü; adres yazıyla gidiyor, harita gitmiyor. |
| `KLINIK_ADI` | Uyarı/rapor başlığı "Klinik Paneli". Yerelde "Demo Klinik". |
| `KLINIK_WHATSAPP_NUMARASI` | Instagram yönlendirmesi numarasız. |
| `AJAN_1M_GIRIS_USD` / `_CIKIS_USD` | `maliyet_usd` hep NULL, hafif yolun kazancı ölçülemiyor. |
| `DOKTORA_BILDIRIM` | Varsayılan `0` → doktor bildirimi kapalı (bilinçli). |
| `INSTAGRAM_KULLANICI` | Nöbetçi `_acik()` kapısıyla temiz kapanıyor, hata döngüsü yok. |

### Canlı kodun hangi commit olduğunu anlamak

İki yol, ikisi de SSH'siz:
1. Panelin sağ altındaki `sürüm <kısa SHA>` etiketi (`SOURCE_COMMIT`, giriş
   sayfasında değil — `temel.html`'i genişleten sayfalarda).
2. `curl https://demoklinik.botfusions.com/openapi.json` → rota listesini
   yereldeki `@app.get/@app.post` listesiyle karşılaştır. Rota değiştirmeyen
   commit'ler (prompt/mantık) bu yolla görünmez.

### Kod değişiklikleri (commit 5595437)

- **Geç kalma cevabı:** hasta 14:00 hatırlatmasına "14:30 gibi geliyorum" yazınca
  ajan konu kilidinin ret cümlesini basıyordu. `SOUL.md`'ye "Hatırlatmaya gelen
  cevaplar" bölümü: hatırlatma sonrası her mesaj konu içi, geç kalma bildirimi
  `randevu_onayla` + kısa teyitle karşılanıyor, saat değiştirilmiyor.
- **Elle "Geldi":** `durum` CHECK'ine dördüncü değer + mevcut kurulumlar için
  ALTER. `geldi` iptal değil, saat dolu kalır. Panelde buton.
- **Hafif yol üretime bağlandı:** `app/hafif.py` yazılmış ama üretim WhatsApp
  yoluna hiç bağlanmamıştı — yalnız panelin test ekranından çağrılıyordu, bilgi
  sorularının tamamı araç şemasıyla tam ajana gidiyordu. Sıra artık
  `kural → hatırlatma → hafif → tam ajan`. Reasoning modelinde hafif yolun
  effort'u ayrı: `AJAN_HAFIF_EFFORT`, varsayılan `none`.
- **Testler ağa çıkıyordu:** hafif yol bağlanınca `.env`'deki gerçek anahtarla
  canlı sağlayıcıya HTTP atmaya başladılar (23s → 155s, 3 test canlı cevapla
  düştü). `tests/conftest.py` artık LLM anahtarlarını siliyor.

## 2026-08-11: WhatsApp canlıya alındı + Google Takvim

VPS'te OpenWA kuruldu (Coolify dışında, `coolify` ağında manuel container), QR
okutuldu, hat **905323314569**. Uçtan uca doğrulandı: hasta yazıyor → kural/hafif/
tam ajan → randevu → Google Takvim etkinliği → iptalde etkinlik siliniyor.

**Canlıda çıkan beş hata — hepsi dış dünyayla temas noktasında, hiçbiri testle
yakalanamazdı.** Yeni bir entegrasyon açarken bu listeye bak:

1. **LID adreslemesi.** WhatsApp göndereni artık numara yerine LID veriyor
   (`253201391558876@lid`). Kod domaini kırpıp `@c.us` ekleyince `send-text` 400
   döndü — ajan cevabı üretti, hastaya hiç ulaşmadı. `openwa.telefon_ayikla` artık
   LID'i contacts ucundan numaraya çeviriyor, çözemezse adresi bozmadan bırakıyor.
2. **İmajda `SOUL.md` yoktu.** `.dockerignore` `hermes-home/` klasörünü tümüyle
   dışlıyordu; ajan ve hafif yol "SOUL.md okunamadı" ile düşüyordu. Yalnız kural
   katmanı çalışıyordu (o DB'den okuyor, dosyaya bakmıyor).
3. **`effort` yanlış parametre.** OpenAI'de adı `reasoning_effort`.
4. **`temperature` yasak.** gpt-5.6-luna yalnız varsayılan 1'i kabul ediyor.
5. **Araçlarla reasoning birlikte olmuyor.** `/chat/completions`'ta function tools
   varken `reasoning_effort: "none"` göndermek ZORUNLU — parametreyi atlamak da
   400 döndürüyor.

3-5 tek bir 400 durum kodunun arkasındaydı; `ajan.py` artık sağlayıcının hata
gövdesini de logluyor, iki teşhis turu bu yüzden kaybedildi.

**Randevu akışı SOUL.md'ye geri kondu.** SOUL.md "akışın tamamı `randevu-al`
skill'inde" diyordu ama o skill Hermes kaldırılırken silinmişti (7eb2949) — ajan
aylardır akışsız, doğaçlama çalışıyormuş. Akış git geçmişinden çıkarılıp SOUL.md'ye
yazıldı, üstüne yeni davranış eklendi: **hastayı soruyla değil seçenekle karşıla**
(önce `en_erken_musait`, sonra hekim adıyla 2-3 somut saat), kararsız hastaya
iki seçenekli daraltma sorusu (sabah/öğleden sonra) ama hep saatlerle birlikte,
işlem→uzmanlık eşlemesi (implant → cerrahi, tel → ortodonti, çocuk → pedodonti).

**Google Takvim eklendi** (`app/gtakvim.py`) — detay aşağıda.

**Bu güncellemede (2026-08-07):** Hermes CLI bağımlılığı tamamen kaldırıldı — ajan artık
`app/ajan.py`'de elle yazılmış, framework'süz bir in-process tool-calling
döngüsü (doğrudan `/chat/completions`, MCP/JSON-RPC yok). Ekonomik hedef için
yeni bir LLM'siz kural katmanı (`app/kural.py`) eklendi: bilgi tabanı
eşleşmeleri ve hatırlatma "evet"/"iptal" cevapları artık hiç LLM çağırmadan
karşılanıyor. Ayrıca bir **iyileştirme öneri paneli** eklendi (`app/iyilestirme.py`,
panelde `/iyilestirme`): ajanın "personelimiz size dönecek" dediği sorular
(bilgi tabanı boşluğu) ve aynı hastanın art arda tekrarladığı sorular
(muhtemel tatminsizlik) salt-okunur listelenir — ajan hiçbir şeyi otomatik
değiştirmez, karar ve yazım hep personelde. Ayrıca **satış demosu** hazırlığı:
doktora yeni randevu bildirimi (`app/bildirim.py`, WhatsApp, KVKK'ya uygun
asgari içerik), panelde durum etiketleri (Planlandı/Teyit Edildi/İptal Edildi),
ve `scripts/demo-veri.py` (DemoDent kliniği + 4 doktor + fiyat listesi). Detay
aşağıdaki ilgili maddelerde.

Yeni bir oturum bu dosyayı okuyarak devam edebilir. Gereksinimler `PRD.md`'de,
düzeltilen kusurlar `KUSURLAR.md`'de, kullanım `README.md`'de. **Eğitim
alt-sisteminin devir notu ayrı:** `egitim/HANDOFF.md`.

---

## 2026-08-07: Eğitim merkezi ayrıldı + Ajana sor demosu

Eğitim alt-sistemi klinik panelinden **ayrıldı**, kendi `egitim/` paketine
taşındı: vendor (kurulumcu) konsolu (`egitim/sunucu.py`, port 8001) site tarama
+ KB düzenleme + yazarak-eğit yapar; müşteri panelinin `/egitim`'i yalnız
yazarak-eğit çağırır (URL/tarama müşteriden kalktı). Ayrıca vendor konsoluna
**"Ajana sor" demosu** eklendi: WhatsApp mockup'ta gerçek pipeline (kural →
hafif → ajan) yan etkisiz çalışır — eğitim doğrulama = satış demosu. Detay ve
sınırlar `egitim/HANDOFF.md`'de.

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
| Kendi ajanımız (Hermes yok, klasöre özel) | ✅ tool-calling döngüsü, sınırları tutuyor |
| Klinik araçları (kabuksuz) | ✅ 7 araç, in-process (`app/araclar.py`), kabuk/dosya erişimi yok |
| Kural katmanı (LLM'siz) | ✅ bilgi tabanı eşleşmesi + hatırlatma evet/iptal, hiç LLM çağırmaz |
| Hafif yol (bilgi soruları) | ✅ araçsız ucuz LLM, %68 daha az token |
| Köprü (webhook ↔ ajan ↔ WhatsApp) | ✅ OpenWA'nın gerçek HMAC imzası doğrulandı |
| Doktorlar + otomatik dağıtım | ✅ |
| Kullanıcılar + roller + işlem izi | ✅ |
| Randevu hatırlatmaları | ✅ kod hazır, canlıda hiç mesaj göndermedi |
| Instagram (yalnız bilgilendirme) | ✅ kod hazır, ⏳ gerçek IG hesabı bağlanmadı (Composio'daki iki hesap sandbox) |
| Konum iğnesi | ✅ `.env`'de `KLINIK_KONUM` doldurulunca çalışır |
| Panel yeni tasarım + Takvim + Fiyat/Kampanya | ✅ |
| Bilgi tabanı (ekle/düzenle) | ✅ panelde, **yalnız yönetici** |
| İyileştirme öneri paneli | ✅ panelde `/iyilestirme`, salt-okunur, yalnız yönetici |
| Demo verisi (DemoDent) | ✅ `scripts/demo-veri.py`, tekrar çalıştırılabilir |
| Composio (Takvim + Gmail) | ✅ anahtar geçerli, Gmail + Calendar ACTIVE |
| Google Takvim | ✅ randevu → etkinlik, hekim davetli, iptalde silinir |
| Doktora WhatsApp bildirimi | ⛔ **varsayılan kapalı** (`DOKTORA_BILDIRIM=0`) — hekim takvimde görüyor |
| WhatsApp QR | ✅ okutuldu, hat 905323314569 |
| VPS | ✅ canlı: demoklinik.botfusions.com (Coolify) |

## Sırada ne var

1. **Müşteri kurulumunda kliniğin KENDİ Google hesabını bağla.** Şu an Composio'ya
   bağlı hesap `cenk.tokgoz@gmail.com` — demoda sorun değil ama gerçek klinikte
   tüm randevular satıcının kişisel takvimine yazılır. Klinik hesabı bağlanır,
   `TAKVIM_KULLANICI` yeni connected account user_id'siyle değiştirilir.
2. **Gerçek Instagram hesabı.** Composio'daki iki IG hesabı sandbox
   (`is_composio_managed: true`), `resepta.botfusions` değil. Bağlamak için IG
   Business/Creator + bağlı Facebook Sayfası şart.
3. **Demo günü.** `scripts/demo-veri.py <telefon> <eposta>` (e-posta = demoyu
   yapanın Google hesabı, tüm demo hekimlerine yazılır → randevu onun takvimine
   düşer). Senaryo: "İmplant için randevu almak istiyorum" → ajan Dr. Deniz
   Kaya'yı önerir, 2-3 saat sunar, randevu açar → panelde "Planlandı" + Google
   Takvim'de etkinlik.
4. *(sonraya)* İptalden sonra yeniden randevu teklifi — `GELISIM-PLANI.md`.
5. *(sonraya)* Meta reklam modülü — PRD Faz 8.

## Çalıştırma

```bash
docker compose up -d
set -a && source .env && set +a
.venv/bin/uvicorn app.main:app --port 8000
```

Panel `http://localhost:8000` · geliştirme girişi: `admin` / `.env`'deki `PANEL_PAROLA`
OpenWA dashboard `http://localhost:2785`

```bash
.venv/bin/python -m pytest        # 356 test, hiçbiri LLM çağırmaz (conftest anahtarları siler)
```

## Bilinmesi gerekenler

**Testler ayrı veritabanı kullanır** (`klinik_crm_test`). Bu kasıtlı: `conftest`
TRUNCATE atıyor, canlıya yönelseydi bir `pytest` komutu hasta kayıtlarını silerdi.
Başka bir makinede ilk koşudan önce:
`docker compose exec postgres createdb -U klinik klinik_crm_test`

**Hatırlatma nöbetçisi varsayılan olarak açık.** QR okutulunca gerçek hastalara
mesaj gitmeye başlar. Test ederken `.env`'e `HATIRLATMA_NOBETCISI=0`.

**Fiyatın tek kaynağı `hizmetler` tablosu.** `bilgi_tabani`'nda "fiyatlar"
kategorisi yok — iki kaynak olsaydı ajan hangi fiyatı söyleyeceğini bilemezdi.
Eski kurulumda bir kez `scripts/fiyat-goc.py` çalıştırılmalı.

**Kampanya duyuru göndermez**, yalnız cevaptaki fiyata yansır.
`test_kampanya_gonderim_yapmaz` bunu denetler — toplu mesaj yasağının kardeşi.

**Ajan konu dışına çıkmaz.** `SOUL.md` § Konu kilidi: klinikle ilgisi olmayan
soruda sabit ret cümlesi. Konu **içi** listesi de orada yazılı (otopark, ödeme,
ulaşım dahil) — kapsamı daraltırken hastanın bilmeye hakkı olan şeyleri kesme.
Bilgi tabanında olmayan klinik sorusu ret değil, "personelimiz size dönecek".

**Instagram randevu açmaz, hatırlatma göndermez.** Kapsam bilinçli dar; nedeni
`app/instagram.py` başındaki açıklamada ve README § Instagram kanalı'nda.
`test_bu_kanaldan_randevu_olusmaz` bunu her koşuda denetler. Kanal `.env`'de
`INSTAGRAM_KULLANICI` boşken tamamen kapalıdır.

**Toplu mesaj mimari olarak yasak.** Detay `README.md` § Toplu mesaj yasağı.
`test_toplu_gonderim_fonksiyonu_yok` bu kuralı her koşuda denetler — kasıtlıdır,
"gereksiz" diye silinmemeli.

**Hermes CLI tamamen kaldırıldı.** `app/ajan.py` artık `hermes -z` subprocess'i
çağırmıyor — kendi yazdığımız, framework'süz bir tool-calling döngüsü
doğrudan `/chat/completions`'a `tools=[...]` ile gidiyor (bkz. aşağıdaki iki
madde). `hermes-home/` klasöründe yalnız `SOUL.md` kaldı (ajanın kimliği);
`config.yaml` ve `skills/` silindi, artık gerekmiyorlar. `HERMES_HOME` env
değişkeni de kalktı.

**Ekonomik hedef: "100 mesajın 100'ü LLM'ye" değil.** `app/kural.py` hafif
yoldan da önce çalışan, tamamen LLM'siz bir katman: (1) bilgi tabanındaki bir
başlıkla `difflib.SequenceMatcher` ile net eşleşen soru (fiyat, çalışma saati,
adres) doğrudan cevaplanır, eşik `KURAL_BENZERLIK_ESIGI` (varsayılan 0.72);
(2) hatırlatmaya tek kelimelik "evet"/"iptal" cevabı, hastanın **tam bir**
bekleyen randevusu varsa, `araclar.arac_calistir` üzerinden aynı `/api/*`
uçlarını çağırarak LLM'siz onaylanır/iptal edilir. Belirsiz her durum (0 ya
da 2+ randevu, tanınmayan kelime, randevu sinyali taşıyan mesaj) `None`
döner ve sıradaki katmana (hafif yol, sonra tam ajan) düşer — üç katman da
"şüphede LLM'e" yönünde.

**Bilgi soruları için üç katman, en ucuzdan en pahalıya.** Sıralama
`app/main.py`'de: kural katmanı (LLM'siz) → hafif yol (araçsız ucuz LLM,
`app/hafif.py`) → tam ajan (tool-calling, `app/ajan.py`). Hafif yolun üç
kapısı (mesajdaki randevu sinyali, geçmişte randevu konuşulmuş olması,
modelin kendi `[RANDEVU]` işareti) değişmedi — hâlâ "şüphede tam ajana" yönünde.
API hatası olursa sessizce bir sonraki katmana düşer, hasta farkı görmez.
`.env`'de `HAFIF_YOL=0` ile hafif yol tamamen kapatılır.

**Sinyal listesinde `saat` ve `gün` bilerek yok.** "Çalışma saatleriniz nedir"
tam da hafif yolda (ya da artık kural katmanında) kalmasını istediğimiz soru.
Randevu bağlamı kelime listesiyle değil, geçmiş kapısıyla yakalanıyor.
Listeyi genişletirken bunu boz.

**Ajanın kabuk yetkisi yok.** Randevu işlemleri `app/araclar.py`'deki 7 araçla
sınırlı, tam ajan (`app/ajan.py`) bunları in-process Python fonksiyonu olarak
çağırıyor — MCP/JSON-RPC protokolü yok, ayrı bir subprocess/sunucu yok.
Araçlar mevcut `/api/*` uçlarını çağırıyor, iş mantığı orada; araç katmanı
yalnız aktarıyor. `tests/test_araclar.py` liste genişlemesini ve modülde
`subprocess`/`os.system`/`open(` gibi kabuk/dosya erişimi izlerinin olmadığını
denetliyor.

**Google Takvim davetli modeliyle çalışır.** Etkinlik kliniğin bağlı Google
hesabının takvimine yazılır, hekimin e-postası (`doktorlar.eposta`) **davetli**
olarak eklenir — hekim randevuyu kendi telefonundaki takvimde, kendi rengiyle
görür, hiçbir kurulum yapmaz. Ayrı takvim + ACL yolu seçilmedi çünkü Composio'nun
Takvim araçlarında `colorId` alanı YOK (2026-08-11'de dört araçta doğrulandı),
yani "her hekime bir Google rengi" planı zaten yapılamıyordu. Kanca
`crm.randevu_olustur`/`randevu_iptal` içinde, çağıranlarda değil: randevu iki
yoldan açılıyor (ajan + panel), ikisi de takvime düşmeli. `TAKVIM_KULLANICI` ya da
`COMPOSIO_API_KEY` boşsa takvim tamamen sessizdir. **Takvim hatası randevuyu
düşürmez**, yalnız log'a uyarı düşer. `google_event_id`'nin dolu olması yazmanın,
boşalması silmenin başarılı olduğunun kanıtıdır — kod başarısızlıkta alanı
değiştirmiyor.

**Doktora WhatsApp bildirimi artık varsayılan KAPALI.** Kullanıcının kararı: hekim
randevuyu takvimde görüyor, WhatsApp bildirimi istenmedi. `doktorlar.telefon`
panelde yalnız klinik kaydı; o alana numara yazmak mesaj göndermeye başlatmaz.
Açmak isteyen `.env`'e `DOKTORA_BILDIRIM=1` yazar. Kod ve KVKK kuralı duruyor:

**Doktora bildirim tek alıcı, panelden ayarlanmaz.** `app/bildirim.py` sabit
kural: yeni randevu → doktorun (telefonu kayıtlıysa) WhatsApp'ına haber gider.
Mesaj metninde hastanın serbest metin notu (`randevular.notlar`) hiç yok —
kilit ekranında görünebileceği için KVKK gereği çıkarıldı. `test_bildirim.py`
bunu ve tek-alıcı kısıtını denetliyor. `doktorlar.telefon` kolonu bu iş için
sonradan eklendi (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).

**Durum etiketleri panelde relabel, DB'de değil.** `bekliyor/onayli/iptal`
DB'de aynı kalıyor; panelde "Planlandı/Teyit Edildi/İptal Edildi" görünüyor
(`durum_etiketi` Jinja filtresi, `app/main.py`). Daha ince durum (Teyit
Bekliyor, Geldi, Gelmedi) istenirse gerçek bir migration gerekir — kullanıcı
demo için bunu bilinçli olarak ertelendi dedi.

**Postgres 5434'te.** 5432/5433 makinede başka projelerde dolu.

**Python 3.13 gerekli** (kodda `X | None` var). Sistem `python3`'ü 3.9.

## Verilmiş kararlar — yeniden tartışılmayacak

| Karar | Gerekçe |
|---|---|
| `referans/crm-main.zip` (trycompai/crm) **kullanılmıyor** | B2B satış CRM'i; deal/pipeline domaini randevuya uymuyor, içinde ikinci bir ajan (Vercel eve) var, Vercel'e bağımlı. Kullanıcı onayladı. |
| WhatsApp = OpenWA + whatsapp-web.js | Baileys'ten düşük ban riski; webhook mimarisi her mesajı deterministik kaydediyor |
| Test ZAI/GLM, canlı OpenAI gpt-5.6-luna | Kullanıcının kararı; `.env`'de iki satır |
| Panel = FastAPI + HTMX tek servis | Ajan zaten Python (kendi in-process döngümüz); tek venv, tek systemd birimi |
| Hasta verisi kendi Postgres'imizde | KVKK; Composio'ya yalnız takvim/mail verisi gider |
| Grafiklerde tek hue (mavi) | Hepsi büyüklük gösteriyor, kimlik değil |

## Mimarinin kritik noktaları

**Çakışma doktor bazında.** İki hekim aynı saatte iki hastaya bakabilir, aynı hekim
bakamaz (`doktor_id IS NOT DISTINCT FROM`). Doktor tanımlı değilse sistem tek
hekimli klinik gibi çalışır — bu geriye uyumluluk kasıtlı.

**Ajan doktor seçmez.** "Farketmez" diyen hastada `doktor_id` gönderilmez, sistem
o saatte en az yüklü hekime dağıtır. Seçim kararlıdır (eşitlikte ada göre), ajan
iki kez sorunca fikir değiştirmez.

**Konuşma geçmişinin tek kaynağı Postgres.** Ajan her çağrıda son 10 görüşmeyi
mesaj geçmişine (`role: user/assistant`) koyar; kendi bir oturum/hafıza
mekanizması yok. İki yerde state tutmamak için.

**Ajanın söyleyebileceği her şey `.hermes.md`'de.** `bilgi_tabani` tablosundan
üretilir, panelden kaydedilince anında geçerli olur (restart gerekmez).

**İki ayrı yetki.** Panel cookie'si (personel) ve `X-Ic-Anahtar` (ajanın CRM'e
yazması). Biri diğerini açmaz — yönetici oturumu bile iç API'yi açmıyor.

## Dosya haritası

```
app/main.py       webhook + panel + iç API
app/crm.py        kişi, görüşme, randevu, doktor (çakışma kuralları burada)
app/kb.py         bilgi tabanı → .hermes.md
egitim/           ajan eğitim merkezi — vendor konsolu (yazarak-eğit + site kazıma + KB düzenle)
                  `uvicorn egitim.sunucu:app --port 8001`; müşteri paneli yalnız yazarak-eğit'i
                  çağırır (app/main.py `/egitim`). URL/tarama + KB düzenleme vendor'da.
app/kural.py      LLM'siz kural katmanı (bilgi tabanı eşleşmesi + hatırlatma cevabı)
app/hafif.py      bilgi soruları için araçsız, ucuz LLM çağrısı
app/ajan.py       tam ajan — tool-calling döngüsü (Hermes yok)
app/araclar.py    ajanın 7 randevu aracı, in-process (eski scripts/klinik-mcp.py)
app/openwa.py     WhatsApp istemcisi + HMAC doğrulama
app/hatirlatma.py randevu hatırlatmaları + giden mesaj kilitleri
app/instagram.py  Instagram DM — yalnız bilgilendirme, yoklamalı
app/hizmet.py     fiyat listesi + kampanyalar (gönderim YOK)
app/static/       panel.css, panel.js (tema + mobil çekmece)
app/kullanici.py  kullanıcılar, parolalar, işlem izi
app/saglik.py     bağlantı nöbetçisi (Composio + WhatsApp)
app/iyilestirme.py  salt-okunur öneri taraması (bkz. aşağı) — panelde /iyilestirme
app/bildirim.py   doktora yeni randevu bildirimi (WhatsApp, tek alıcı, VARSAYILAN KAPALI)
app/gtakvim.py    Google Takvim — randevu → etkinlik, hekim davetli, iptalde silinir
hermes-home/SOUL.md  ajanın kimliği (bu klasöre özel)
tests/            356 test, LLM'siz
scripts/          kurulum, vps-deploy, systemd, nginx, yedekleme
scripts/demo-veri.py  satış demosu için DemoDent verisi (idempotent)
```

## Son doğrulama — Hermes kaldırma + kural katmanı (2026-08-06)

Bu doğrulama pytest üzerinden yapıldı, canlı WhatsApp turu **denenmedi** —
bir sonraki oturum QR okutulduktan sonra en az bir gerçek mesajla üç katmanı
(kural → hafif → tam ajan) canlıda doğrulamalı.

- 323 test yeşil (291 eskisi + `test_araclar.py`, `test_kural.py`, `test_ajan.py`,
  `test_iyilestirme.py`, `test_bildirim.py`, `test_panel.py`'ye eklenen 2 rota testi)
- `scripts/demo-veri.py` gerçek test DB'sinde iki kez çalıştırıldı: ilk seferde
  4 doktor + 4 hizmet + 3 bilgi kaydı eklendi, ikinci seferde hepsi "zaten var"
  diyerek atlandı (idempotent doğrulandı)
- `app.main` gerçek env değişkenleriyle sorunsuz import ediliyor
- `test_bu_kanaldan_randevu_olusmaz`, `test_toplu_gonderim_fonksiyonu_yok`,
  `test_kampanya_gonderim_yapmaz` gibi mimari kısıt testleri hâlâ geçiyor —
  Hermes kaldırma bu sınırları bozmadı
- Tool-calling döngüsü sahte `httpx.post` ile test edildi: tool_call → tool
  sonucu → düz metin akışı doğru, `AJAN_MAX_TUR` aşılınca `CevapUretilemedi`
  fırlatıyor (sonsuz döngü koruması)
- Kural katmanı testleri: tam/yazım-hatalı eşleşme LLM'siz cevaplanıyor,
  alakasız/uzun/randevu-sinyalli mesaj `None` dönüp bir sonraki katmana düşüyor;
  hatırlatma evet/iptal yalnız tam bir bekleyen randevu varken LLM'siz işleniyor
- İyileştirme paneli: gerçek veriyle `/iyilestirme` render edildi (TestClient),
  bilgi tabanı boşluğu ve tekrarlanan soru satırları doğru göründü; sayfa
  yönetici → 200, personel → 403

## Önceki doğrulama — Hermes'li dönem (2026-08-06, canlı denendi)

- Ajan kabuksuz çalışıyor: doktor seçimi, uygunluk, randevu yazma ve iptal
  `terminal` kapalıyken MCP araçlarıyla yürüdü
- Hafif yol: çalışma saati, implant süresi ve otopark soruları 2.210 token'da
  doğru cevaplandı; konu dışı soru orada da reddedildi, "randevu almak
  istiyorum" tam ajana devredildi
- Panel 9 sayfa 200 dönüyor, rol ayrımı tutuyor (personel → 403)
- Fiyat/kampanya panelden kaydedilince ajan yeni fiyatı söylüyor
- Ajan konu dışı soruya sabit ret cümlesi veriyor, konu içi soruyu cevaplıyor
- Ajan bilgi tabanından cevap veriyor, teşhis/ilaç sorularını reddediyor
- Aynı saate 3 hasta → 3 farklı hekim; 4. → "müsait doktor yok"
- Randevu açılınca 24s + 1s hatırlatma planlanıyor; iptalde düşüyor
- Başkasının numarasıyla iptal denemesi → 403
