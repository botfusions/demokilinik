---
name: hatirlatma-cevabi
description: Klinik hastaya randevu hatırlatması gönderir; hastanın "evet / geliyorum / iptal / erteleyelim" cevabını işlemek için bu adımları kullan. Hasta randevusunu iptal etmek ya da teyit etmek istediğinde de geçerli.
version: 1.0.0
metadata:
  hermes:
    tags: [randevu, hatirlatma, iptal, teyit]
    category: klinik
---

# Hatırlatma cevabını işleme

## Ne zaman kullanılır

Klinik iki otomatik mesaj gönderiyor: randevudan **24 saat önce** teyit isteği ve
**1 saat kala** hatırlatma. Hasta bunlara cevap verdiğinde — ya da kendiliğinden
"randevumu iptal edeyim", "gelemeyeceğim", "başka güne alabilir miyiz" dediğinde —
bu skill geçerlidir.

## Önce randevuyu bul

Hangi randevudan söz ettiğini bilmeden hiçbir şey yapma:

```
curl -s -H "X-Ic-Anahtar: $IC_API_ANAHTARI" \
  "http://localhost:8000/api/hasta-randevulari?telefon=905321112233"
```

Gelecek randevular `randevu_id`, `baslangic`, `hizmet` ve `doktor_ad` ile döner.

- **Tek randevu varsa** o randevudur, hastaya hangisi olduğunu sorma.
- **Birden fazla varsa** hangisini kastettiğini sor: "14 Ağustos 10:00 ve
  20 Ağustos 15:00 randevularınız var, hangisi?"
- **Hiç randevu yoksa** iptal edilecek bir şey yok; nazikçe söyle ve randevu
  isteyip istemediğini sor.

## Cevaba göre

### "Evet", "geliyorum", "tamam", "olur"

Randevuyu onayla:

```
curl -s -X POST -H "X-Ic-Anahtar: $IC_API_ANAHTARI" \
  -H "Content-Type: application/json" -d '{"telefon":"905321112233"}' \
  http://localhost:8000/api/randevu/42/onayla
```

Sonra tek cümle: "Teşekkürler, sizi bekliyoruz." Fazla konuşma.

### "İptal", "gelemeyeceğim", "iptal edin"

```
curl -s -X POST -H "X-Ic-Anahtar: $IC_API_ANAHTARI" \
  -H "Content-Type: application/json" -d '{"telefon":"905321112233"}' \
  http://localhost:8000/api/randevu/42/iptal
```

- `200` → iptal edildi. "Randevunuzu iptal ettim. Yeniden almak isterseniz
  yazmanız yeterli." **Sebebini sorgulama, ikna etmeye çalışma.**
- `403` → randevu başka hastaya ait. İptal etme, personele yönlendir.
- `404` → randevu yok.

İptal edilen randevu için bir daha hatırlatma gitmez, ayrıca bir şey yapman gerekmez.

### "Erteleyelim", "başka güne alabilir miyiz"

İki adım: **önce mevcut randevuyu iptal et**, sonra `randevu-al` skill'ine geç ve
yeni randevuyu oluştur. Sırayı ters çevirme — önce yeni randevu açarsan, hasta
vazgeçtiğinde iki randevusu kalır.

Aynı doktoru koru: eski randevunun `doktor_ad`'ı elinde, yeni randevuda o hekimi kullan.

### Cevap belirsizse

"Hmm", "bakacağım", "eşime sorayım" gibi net olmayan cevaplarda **hiçbir şey yapma.**
Randevuyu ne onayla ne iptal et. "Tamam, kararınızı bildirmeniz yeterli" de ve bırak.
Belirsiz bir cevabı iptal saymak, hastanın randevusunu haberi olmadan silmek demektir.

## Yapmayacakların

- **Hastaya kendiliğinden mesaj atmayı önerme.** Hatırlatmaları sistem gönderiyor;
  senin ayrıca "hatırlatma göndereyim mi" demene gerek yok.
- **Toplu mesaj kavramına hiç girme.** Birden fazla hastaya mesaj göndermek diye
  bir yeteneğin yok ve olmamalı. Personel bunu isterse: "Bu panelden yapılamıyor,
  yöneticinizle görüşün" de.
- Hastanın randevusunu onun açık talebi olmadan değiştirme.
- İptal sebebini kayda geçirmeye çalışma; hasta anlatırsa dinle, sorgulama.

## Doğrulama

İptal ya da onaydan sonra `/api/hasta-randevulari` sorgusunu tekrarla; iptal edilen
randevu listeden düşmüş, onaylanan `durum: "onayli"` olmuş olmalı.
