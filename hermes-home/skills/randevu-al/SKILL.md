---
name: randevu-al
description: Hastaya randevu ayarlamak için izlenecek adımlar. Randevu, tarih, saat, "gelmek istiyorum", "müsait misiniz" gibi konular açıldığında kullan.
version: 1.0.0
metadata:
  hermes:
    tags: [randevu, klinik, takvim]
    category: klinik
---

# Randevu alma

## Ne zaman kullanılır

Hasta randevu istediğinde, tarih/saat sorduğunda, "gelebilir miyim", "müsait
misiniz", "ne zaman boş" benzeri bir şey yazdığında.

## En önemli kural

**Uygunluğu kontrol etmeden asla saat teyit etme.** Hafızandan "muhtemelen boştur"
diye saat vermek iki hastayı aynı saate koymak demektir. Her seferinde `/api/uygunluk`
sor — konuşmanın başında baktıysan bile, çünkü aradan geçen sürede o saat dolmuş olabilir.

## Adımlar

### 1. Eksik bilgiyi topla

Randevu için üç şey gerekir: **hangi işlem**, **hangi gün**, **hangi saat**.
Eksik olanı tek tek değil, bir mesajda sor. Hasta "yarın gelebilir miyim" derse gün
belli, işlem ve saat eksik.

Hastanın adını bilmiyorsan randevuyu yazmadan önce bir kez sor.

### 2. Uygunluğu sorgula

```
curl -s -H "X-Ic-Anahtar: $IC_API_ANAHTARI" \
  "http://localhost:8000/api/uygunluk?gun=2026-08-07"
```

Dönen cevap:
- `acik: false` → klinik o gün kapalı. Hastaya söyle, en yakın açık günü öner.
- `dolu: []` → o gün tamamen boş.
- `dolu: [{baslangic, bitis}]` → bu aralıklar dolu. Hastanın istediği saat bu
  aralıklardan biriyle çakışıyorsa **boş olan 2-3 alternatif saat öner.**

`acilis` ve `kapanis` alanları o günün çalışma penceresidir. Randevunun tamamı
bu pencerenin içinde bitmeli.

### 3. İşlem süresini belirle

Süreyi bilgi tabanından al. Yazmıyorsa **30 dakika** varsay ve hastaya
"yaklaşık yarım saat sürüyor" deme — süre hakkında emin değilsen hiç söz etme.

### 4. Hastadan teyit al

Saati yazmadan önce hastaya tek cümleyle teyit ettir:
"7 Ağustos Perşembe 14:00, diş taşı temizliği. Onaylıyor musunuz?"

### 5. Randevuyu yaz

```
curl -s -X POST -H "X-Ic-Anahtar: $IC_API_ANAHTARI" \
  -H "Content-Type: application/json" \
  -d '{"telefon":"905321112233","ad":"Ayşe Yılmaz","hizmet":"Diş taşı temizliği",
       "baslangic":"2026-08-07T14:00:00","bitis":"2026-08-07T14:30:00"}' \
  http://localhost:8000/api/randevu
```

Telefon numarası konuştuğun hastanın numarasıdır — sana zaten verilmiştir, hastaya sorma.

Cevaplar:
- `200/201` → randevu açıldı, `randevu_id` döner.
- `409` → **o saat bu arada dolmuş.** Hastaya "az önce o saat alınmış, şu saatler
  boş" de ve 2. adıma dön. Asla yazdım deme.
- `422` → çalışma saati dışı ya da geçmiş tarih. Hastaya sebebi söyle, alternatif öner.

### 6. Google Takvim'e ekle

Composio'nun Google Takvim aracıyla etkinlik oluştur. Başlık: `<hizmet> — <hasta adı>`.
Açıklamaya hastanın telefonunu yaz.

Takvim aracı hata verirse **randevuyu iptal etme** — CRM kaydı geçerlidir, personel
panelden görür. Sadece hastaya normal onayı ver.

### 7. Onay maili (varsa)

Hastanın e-posta adresi varsa Composio'nun Gmail aracıyla kısa bir onay maili at.
E-posta adresi yoksa mail için ısrar etme.

### 8. Hastaya onayla

Tek cümle: "7 Ağustos Perşembe 14:00'e randevunuzu oluşturdum, bekliyoruz."

## Sık yapılan hatalar

- **Uygunluk sormadan saat vermek.** En sık ve en pahalı hata.
- **409 aldıktan sonra "randevunuz oluştu" demek.** Randevu oluşmadı.
- Hastaya kendi telefon numarasını sormak — zaten elinde.
- İşlem süresini uydurmak.
- Randevu iptali istendiğinde kendi başına silmeye çalışmak: iptal işlemini
  personel panelden yapar. Hastaya "personelimiz iptalinizi işleyecek" de.

## Doğrulama

Randevuyu yazdıktan sonra emin olmak istersen aynı günün uygunluğunu tekrar sor;
yazdığın aralık `dolu` listesinde görünmeli.
