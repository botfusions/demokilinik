# Yeni müşteri kurulumu — demodan gelen ilk klinik için

Sıra bu tablodadır; adım atlanırsa sonraki adım patlar. "Kim" sütunu o işi
yapan taraf: **biz** = Botfusions (vendor), **klinik** = müşteri personeli.

| # | Adım | Kim | Ne yapılır | Doğrulama |
|---|------|-----|------------|-----------|
| 1 | Klinik bilgi formu | klinik | Klinik adı, adres + harita konumu (lat/lon), çalışma saatleri, hizmet listesi (ad, süre, fiyat, garanti), doktor listesi (ad, uzmanlık, e-posta) | Formda boş alan kalmamış |
| 2 | Yeni deploy | biz | Bu repodan Coolify'da yeni proje, yeni alt domain (örn. `klinikadi.botfusions.com`), boş Postgres | `/openapi.json` dönüyor |
| 3 | Env'ler | biz | `PANEL_PAROLA` (yeni, klinik bilmesin), `OPENAI_API_KEY`, `COMPOSIO_API_KEY` (bizim hesap), `TELEGRAM_*` (teknik uyarı kanalımız), `TZ=Europe/Istanbul`, `DEMO_KAPALI=1` (demo izleyicisi kapansın) | Container ayakta, log temiz |
| 4 | Admin + personel | biz → klinik | `PANEL_PAROLA` ile ilk `admin` açılır; klinik personel kullanıcılarını biz açarız, parolaları ilk girişte klinik değiştirir | Herkes panelden giriş yapıyor |
| 5 | Bilgi tabanı | klinik (panel `/bilgi`) | 1. adımdaki form panelden girilir: hizmet/fiyat/süre, çalışma saatleri, adres. Konum koordinatı env/ayar tarafında biz | Ajan "fiyat ne kadar"ı tabandan cevaplıyor |
| 6 | Doktorlar | klinik (panel `/doktorlar`) | Doktorlar eklenir; **e-posta zorunlu** — randevu davetleri bu adrese gider | Kayıtlı doktorlar listesi dolu |
| 7 | WhatsApp hattı | klinik + biz | Müşterinin kendi klinik numarası: OpenWA ekranında QR okutulur (telefonda WhatsApp → Bağlı cihazlar) | Bizim test numarasından mesaj → cevap geliyor |
| 8 | Klinik takvimi | klinik + biz | Kliniğin Google hesabı (tercih: klinik açtığı yeni Gmail) bizim Composio hesabına connected account olarak bağlanır; `TAKVIM_KULLANICI` = o kliniğin user_id'si | Takvim sayfasında `takvim bağlı` rozeti |
| 9 | Uçtan uca test | biz | Bilinmeyen numaradan: karşılama → fiyat sorusu → randevu (ajan **ad soyad soruyor mu**) → onay → takvimde etkinlik + doktora davet maili → hatırlatma | Tümü tek turda geçti |
| 10 | Devreye alma | biz | Kalan demo verisi temizlenir (kisiler/randevular), haftalık rapor kanalı açılır, 1 hafta boyunca log izlenir | İlk gerçek hafta hatasız |

## Mimari kararlar (her müşteride aynı, tartışılmaz)

- **Tek Composio hesabı (bizimki).** Her klinik = bir connected account + kendi
  `TAKVIM_KULLANICI` user_id'si. Müşteriye Composio anahtarı verilmez, açmaz.
- **Tek Google organizatör takvimi** (kliniğin Gmail'i). Doktorlar hiçbir şey
  bağlamaz; davet maili gelir, kabul edince kendi takvimine düşer.
- **WhatsApp = müşterinin kendi hattı** (OpenWA QR). Numara müşteride kalır;
  ayrılırsa QR'ı düşürür, sistem bizde kalır.
- **Instagram opsiyonel** (Unipile): isterse klinik IG hesabı bağlanır; kapsam
  dardır — bilgilendirme, randevu açmaz (mimari kural, testle çakılı).

## Demo ile farklar (yeni müşteriye geçerken değişen)

| Demo | Müşteri |
|------|---------|
| `DEMO_KAPALI` yok → QR'dan izleyici girebilir | `DEMO_KAPALI=1` → yalnız parola ile |
| Sahte doktorlar, demo randevular | Gerçek doktor + boş randevu defteri |
| WhatsApp hattı bizim test numarası | Müşterinin klinik numarası |
| Takvim Berk'in Gmail'i | Kliniğin kendi Gmail'i |
| Telegram'a klinik bildirimi yok (yalnız vendor teknik kanalı) | Aynı — bildirim kanalı satışla ayrıca kararlaştırılır |
