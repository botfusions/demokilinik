# Gelişim planı

Henüz yapılmamış, ileride ele alınacak fikirler. Sıra önceliği göstermez.

## Google Takvim entegrasyonu (doktor müsaitlik/randevu görünümü)

**Fikir:** Doktorlar randevularını kendi telefonlarında native Google Takvim
uygulamasından, kendi renkleriyle görsün. Ayrı bir mobil app yazılmayacak.

**Model:** Klinik başına 1 paylaşılan Gmail hesabı → Composio ile bu hesaba
OAuth bağlanır (mevcut `app.composio.dev` akışı, `COMPOSIO_API_KEY` zaten
var) → her doktora Google Takvim'in 11 renkten birinden bir `renk` atanır →
randevu açılınca/iptal olunca o paylaşılan takvimde doktorun rengiyle event
oluşur/silinir. Doktor kendi telefonunda bu takvime "abone" olur, native
Google Takvim uygulaması zaten renklere göre gösterir — ekstra kurulum yok.

**Neden kolay:** Altyapı kısmen hazır:
- `randevular.google_event_id` kolonu zaten var ama hiç doldurulmuyor
  (`app/db.py`, `app/crm.py:randevu_durum_guncelle`, `app/templates/randevular.html`)
- `app/instagram.py`'deki `_cagir()` Composio tool-çağırma deseni aynen
  Google Calendar tool'ları için kullanılabilir (`GOOGLECALENDAR_CREATE_EVENT`,
  `_UPDATE_EVENT`, `_DELETE_EVENT` — kesin slug adları `--kesfet` ile
  doğrulanmalı, instagram.py'deki gibi)

**Yapılacaklar (taslak):**
1. `doktorlar` tablosuna `renk` kolonu (Google'ın 1-11 colorId paleti)
2. Doktorlar panelinde renk seçimi UI'ı
3. Yeni modül (örn. `app/gtakvim.py`) — `_cagir()` deseni, event
   oluştur/güncelle/sil fonksiyonları
4. `crm.py:randevu_olustur` ve iptal/durum güncelleme akışına bu çağrıları
   ekle, dönen event id'yi `randevu_durum_guncelle(..., google_event_id=...)`
   ile kaydet
5. Yeni env değişkenleri: paylaşılan hesabın Composio `user_id`'si (örn.
   `TAKVIM_KULLANICI`, `INSTAGRAM_KULLANICI` ile aynı desen), takvim id'si
   (`GOOGLE_TAKVIM_ID`, varsayılan `primary`)
6. `.env.example` + `README.md`'ye dokümantasyon
7. Doktor bağlantı sağlığı `app/saglik.py`'deki `_composio_kontrol` deseniyle
   izlenebilir (opsiyonel, mevcut instagram nöbetçisiyle aynı yapı)

**Tetikleyici:** Kullanıcı onayı + hangi klinikte Composio Gmail bağlantısı
kurulacağı netleşince başlanır.
