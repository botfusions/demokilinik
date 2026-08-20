# Mini PRD — İnsan Devri (Human Handover)

**Tarih:** 16-08-2026 · **Repo:** `github.com/botfusions/demokilinik`
**Bağımsız iş.** Meta App Review'u beklemez, WhatsApp'ta bugün değer üretir.

---

## 1. Problem

Asistan bir soruyu çözemediğinde ya da hasta "bir yetkiliyle konuşmak
istiyorum" dediğinde **yapabileceği hiçbir şey yok.** Kodda insan devri diye bir
kavram yok; personelin panelden hastaya yazması da mümkün değil.

Satış görüşmelerinde "peki bot çözemezse ne oluyor?" sorusunun cevabı şu an yok.
Ayrıca Meta App Review'un merkezinde de bu akış var — o iş başladığında hazır olur.

## 2. Kapsam

**Var:** WhatsApp (bugünkü canlı kanal). Tasarım kanal-bağımsız olacak ki
Instagram/Messenger eklendiğinde aynı mekanizma çalışsın.

**Yok:** Canlı sohbet arayüzü (WebSocket, yazıyor göstergesi), çoklu operatör
kuyruğu, vardiya yönetimi. Bunlar ayrı ürün.

## 3. Mevcut durum — kodda ne var, ne yok

| | Durum |
|---|---|
| `gorusmeler` tablosu, panelde görüşme listesi (`templates/hasta.html`) | ✅ var, salt-okunur |
| `openwa.mesaj_gonder(telefon, metin)` | ✅ var |
| **Panelden mesaj gönderme** | ❌ **yok** — `mesaj_gonder` yalnızca webhook akışında (`main.py:1167`) çağrılıyor |
| Kural tabanlı kelime yakalama (`kural.py:29` `_ONAY_KELIMELERI` deseni) | ✅ var, aynı desen kullanılacak |
| Telegram bildirimi (`saglik.py`) | ✅ var, personeli uyarmak için kullanılabilir |

**En büyük parça panel tarafı** — cevap kutusu ve gönderim endpoint'i sıfırdan.

## 4. Yapılacaklar

### 4.1 Veri

`kisiler` tablosuna tek kolon:

```sql
ALTER TABLE kisiler ADD COLUMN insan_devri_at timestamptz NULL;
```

`NULL` = asistan cevaplıyor. Dolu = insan devrede, asistan susuyor.

### 4.2 Devri başlatma

**Hastadan:** `kural.py` içine yeni bir kelime kümesi. LLM çağrısı yok.

```
_DEVIR_KELIMELERI = {
  "yetkili", "insan", "canlı destek", "biriyle görüşmek", "müşteri temsilcisi",
  "gerçek kişi", "talk to a person", "human", "real person", "customer service"
}
```

Yakalandığında: `insan_devri_at = now()`, hastaya sabit cevap:
> "Sizi klinik personelimize aktarıyorum, en kısa sürede dönüş yapılacak."

**Personelden:** panelde "Devral" düğmesi — hasta istemese de personel araya
girebilmeli.

### 4.3 Asistanın susması

`main.py:_mesaji_isle` içinde, `gorusme_ekle(gelen)` sonrası:

```
insan_devri_at doluysa → mesajı kaydet, ajanı ÇAĞIRMA, cevap gönderme
```

Mesaj yine de kaydedilir; personel panelde görür. Kaydetmeden geçmek yanlış olur.

### 4.4 Panel — cevap kutusu

Hasta sayfasına (`templates/hasta.html`) görüşme listesinin altına metin kutusu +
"Gönder" düğmesi. Yeni endpoint:

```
POST /hastalar/{kisi_id}/mesaj   (dependencies=[Depends(personel)])
  → gorusme_ekle(conn, kisi_id, "giden", metin)
  → openwa.mesaj_gonder(telefon, metin)
```

Önce kaydet sonra gönder — WhatsApp'a ulaşılamasa da personel ne yazdığını
görsün (`_mesaji_isle`'deki aynı sıra).

### 4.5 Devri bitirme

Panelde "Devri bitir" düğmesi → `insan_devri_at = NULL`. Asistan tekrar devreye
girer.

### 4.6 Görünürlük

- Hasta listesinde ve görüşme başlığında **rozet**: "İnsan devrede"
- Panel ana sayfada sayaç: "Bekleyen devir: N"
- Devir başladığında **Telegram bildirimi** (`saglik.py`'deki mevcut kanal):
  "Hasta X insan devri istedi — panel/hastalar/123"

## 5. Verilen kararlar

**K1 — Otomatik geri alma: VAR.** Personel cevap vermezse asistan sonsuza kadar
susar ve hasta terk edilmiş olur; bu, botun cevap vermesinden daha kötüdür.
**2 saat** cevapsız kalırsa devir otomatik düşer ve hastaya gider:

> "Personelimize şu an ulaşamadık, çalışma saatlerinde size dönecekler. Bu arada
> ben yardımcı olabilirim."

Sayaç, devrin başladığı andan değil **son personel mesajından** işler; personel
yazıp sustuysa süre yeniden başlar.

**K2 — Mesai dışı bilgilendirme: VAR.** Devir talebi çalışma saatleri dışında
gelirse aktarım mesajına dönüş zamanı eklenir ("yarın 09:00'dan itibaren").
Çalışma günleri ve saatleri `ayarlar` tablosunda mevcut — env'den okuma, oradan al.

**K3 — Hatırlatmalar devam eder.** Devir, asistanın **cevap üretmesini** durdurur;
zamanlanmış randevu hatırlatmasını değil. `hatirlatma.py` akışına dokunulmaz.

## 6. Kesin sınırlar

1. Ajanın kendisine dokunulmaz — devir kontrolü `main.py` akışında, `ajan.py`
   içinde değil.
2. Kanal-bağımsız: gönderim fonksiyonu kanala göre seçilecek şekilde yazılsın
   (bugün `openwa`, yarın `meta`). Kanal `gorusmeler.kanal` alanında zaten var.
3. LLM ile devir tespiti yok — kelime listesi yeterli, ucuz ve kesin.
4. Şema değişikliği tek kolon.

## 7. Testler

| # | Test |
|---|---|
| T1 | "yetkiliyle görüşmek istiyorum" → `insan_devri_at` doluyor, sabit cevap gidiyor |
| T2 | Devir açıkken gelen mesaj kaydediliyor ama ajan çağrılmıyor |
| T3 | Panelden gönderilen mesaj `gorusmeler`'e "giden" olarak yazılıyor |
| T4 | Gönderim hata verse de kayıt duruyor |
| T5 | "Devri bitir" → `insan_devri_at` NULL, ajan tekrar cevaplıyor |
| T6 | 2 saat cevapsız → devir otomatik düşüyor, bilgi mesajı gidiyor (K1) |
| T7 | Devir açıkken zamanlanmış hatırlatma yine gidiyor (K3) |
| T8 | Devir kelimesi randevu cümlesinin içinde geçerse yanlış tetiklenmiyor (ör. "insan gibi konuşuyorsun") |

T8 önemli: kelime listesi fazla geniş olursa normal sohbette tetiklenir.

## 8. Bitti sayılma koşulu

> Hasta WhatsApp'tan "yetkiliyle görüşmek istiyorum" yazar → asistan susar,
> hastaya aktarım mesajı gider, panelde rozet çıkar, Telegram'a bildirim düşer →
> personel panelden yazar, mesaj hastaya ulaşır → "Devri bitir" denince asistan
> tekrar devreye girer.

## 9. Tahmin

2-3 gün. Dağılımı: panel cevap kutusu + endpoint ~1 gün, devir mantığı + kurallar
~0.5 gün, otomatik geri alma + bildirim ~0.5 gün, testler ~0.5 gün.
