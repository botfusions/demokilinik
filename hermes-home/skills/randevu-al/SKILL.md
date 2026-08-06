---
name: randevu-al
description: Hastaya randevu ayarlamak için izlenecek adımlar — doktor seçimi, uygunluk kontrolü, acil vaka yönlendirmesi. Randevu, tarih, saat, doktor, "gelmek istiyorum", "müsait misiniz" gibi konular açıldığında kullan.
version: 2.0.0
metadata:
  hermes:
    tags: [randevu, klinik, takvim, doktor]
    category: klinik
---

# Randevu alma

## Ne zaman kullanılır

Hasta randevu istediğinde, tarih/saat/doktor sorduğunda, "gelebilir miyim",
"müsait misiniz", "ne zaman boş" benzeri bir şey yazdığında.

## İki kural

1. **Uygunluğu kontrol etmeden asla saat teyit etme.** Hafızandan "muhtemelen
   boştur" diye saat vermek iki hastayı aynı saate koymak demektir. Her seferinde
   sor — konuşmanın başında baktıysan bile, çünkü aradan geçen sürede dolmuş olabilir.
2. **Randevuyu yazmadan hastaya "oluşturdum" deme.** API `409` dönerse randevu
   açılmamıştır.

## Adımlar

### 1. Hastayı ve doktor geçmişini öğren

İlk iş: hastanın daha önce hangi doktora gittiğine bak.

`doktorlari_getir` aracını çağır (`telefon`: hastanın numarası).

Dönen cevap:
- `doktorlar` → klinikte çalışan aktif hekimler (ad + uzmanlık)
- `onceki_doktor` → hastanın en son gittiği hekim (varsa)
- `ilk_ziyaret: true` → bu hasta kliniğe ilk kez geliyor

**Liste boşsa** (`doktorlar: []`) klinik tek hekimlidir; doktor sorma, doğrudan
saat ayarla ve 4. adıma geç.

### 2. Doktoru belirle

Üç durum var:

**a) Hasta daha önce gelmiş** (`onceki_doktor` dolu) — hekimini hatırla, sorma:
> Geçen sefer Dr. Ayla Tuncer'e gelmiştiniz. Yine onunla mı devam edelim?

Hasta "evet" derse o doktorun `id`'siyle devam et. Başka doktor isterse listeyi ver.

**b) Hasta ilk kez geliyor** (`ilk_ziyaret: true`) — doktor tercihi sor, ama
zorlama. Uzmanlıkları kısaca söyle:
> Dr. Ayla Tuncer (ortodonti), Dr. Kerem Aksoy (implantoloji) ve Dr. Nihal Erdoğan
> (genel diş hekimliği) var. Tercihiniz var mı, yoksa en uygun hekime ayarlayayım mı?

Hasta "farketmez" derse **doktor seçme** — 5. adımda `doktor_id` göndermezsen
sistem o saatte en boş hekime dağıtır. Kendi kafandan hekim seçmek yükü dengesizleştirir.

**c) Hasta belirli bir hekim istiyor** — listede varsa `id`'sini kullan. Listede
yoksa (ayrılmış ya da pasif) nazikçe söyle ve alternatif sun.

### 3. Aciliyeti değerlendir

Hasta ağrı, şişlik, kırık diş, düşen dolgu gibi bir şikayet anlatıyorsa bu **acil**
sayılır. Teşhis koyma, ama en erken slotu ara:

`en_erken_musait` aracını çağır (`sure_dk`: 30).

`bulundu: true` ise dönen `baslangic`, `bitis` ve `doktor_id`'yi kullan:
> En erken bugün 15:30'da Dr. Nihal Erdoğan'a alabiliriz. Uygun mu?

Belirli bir doktor isteniyorsa `doktor_id` ver. Acil randevuyu yazarken
`acil: true` gönder — personel panelde acil vakaları ayırt eder.

### 4. Uygunluğu sorgula

Hasta belirli bir gün istiyorsa:

`gun_uygunlugu` aracını çağır (`gun`: 2026-08-07, istersen `doktor_id`).

`doktor_id` verirsen yalnız o hekimin doluluğu, vermezsen kliniğin tamamı gelir.

- `acik: false` → klinik o gün kapalı. En yakın açık günü öner.
- `dolu: []` → o gün tamamen boş.
- `dolu: [...]` → bu aralıklar dolu; hastanın istediği saat çakışıyorsa **boş olan
  2-3 alternatif saat öner.**

`acilis`/`kapanis` o günün çalışma penceresidir; randevunun tamamı içinde bitmeli.

**İşlem süresi:** bilgi tabanından al. Yazmıyorsa 30 dakika varsay ve hastaya süre
hakkında bir şey söyleme.

### 5. Teyit al, sonra yaz

Yazmadan önce tek cümleyle teyit ettir:
> 7 Ağustos Perşembe 14:00, Dr. Ayla Tuncer, diş taşı temizliği. Onaylıyor musunuz?

Onay gelince:

`randevu_olustur` aracını çağır:

```
telefon: 905321112233        ad: Ayşe Yılmaz
hizmet: Diş taşı temizliği   acil: false
baslangic: 2026-08-07T14:00:00
bitis:     2026-08-07T14:30:00
doktor_id: 1
```

`doktor_id`'yi **atlarsan** sistem o saatte en boş hekimi seçer ve cevapta
`doktor_otomatik_secildi: true` ile hangisini seçtiğini söyler — hastaya o hekimin
adını bildir.

Telefon numarası konuştuğun hastanınkidir; hastaya sorma.

Cevaplar:
- `randevu_id` döndüyse randevu açıldı. `doktor_ad` alanını hastaya söyle.
- `HATA 409` → **o saat bu arada dolmuş.** 4. adıma dön, alternatif öner. Asla "yazdım" deme.
- `HATA 422` → çalışma saati dışı, geçmiş tarih ya da geçersiz doktor. Sebebi söyle.
- Araç `HATA` ile başlayan bir metin döndüyse işlem başarısızdır.

### 6. Google Takvim'e ekle

Composio'nun Takvim aracıyla etkinlik oluştur.
Başlık: `<hizmet> — <hasta adı>`. Açıklamaya hastanın telefonunu **ve doktorun
adını** yaz. Doktorun kendi takvimi varsa etkinliği ona da davetli ekle.

Takvim aracı hata verirse **randevuyu iptal etme** — CRM kaydı geçerlidir, personel
panelden görür. Hastaya normal onayı ver.

### 7. Hastaya onayla

Tek cümle, doktor adıyla:
> 7 Ağustos Perşembe 14:00'e Dr. Ayla Tuncer'den randevunuzu oluşturdum, bekliyoruz.

## Sık yapılan hatalar

- **Uygunluk sormadan saat vermek.** En sık ve en pahalı hata.
- **409 aldıktan sonra "randevunuz oluştu" demek.** Oluşmadı.
- Hasta "farketmez" dediğinde kendi kafandan doktor seçmek — `doktor_id` gönderme,
  sistem dengeli dağıtsın.
- Daha önce gelmiş hastaya doktorunu baştan sormak — `onceki_doktor` zaten elinde.
- Ağrı anlatan hastayı normal sıraya koymak — `/api/en-erken` kullan.
- Hastaya kendi telefon numarasını sormak.
- İşlem süresini uydurmak.

## Doğrulama

Randevuyu yazdıktan sonra emin olmak istersen aynı gün ve doktor için uygunluğu
tekrar sor; yazdığın aralık `dolu` listesinde görünmeli.
