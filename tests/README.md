# Testler

**Hiçbir test LLM çağırmaz.** LLM'e bağlı test yavaş, pahalı ve kararsızdır — üstelik
asıl kırılgan yerler (randevu çakışması, webhook imzası, bilgi tabanı senkronu) zaten
LLM'siz test edilebilir. Ajanın cevap kalitesi PRD §7'deki manuel smoke test ile bakılır.

## Çalıştırma

```bash
docker compose up -d
cp .env.example .env        # DATABASE_URL'i doldur
set -a && source .env && set +a
pytest
```

`DATABASE_URL` yoksa Postgres gerektiren testler **atlanır** (fail etmez), böylece
docker ayakta olmadan da anlamlı çıktı alırsın.

## Dosyalar

| Dosya | Neyi korur |
|---|---|
| `test_crm.py` | Aynı telefonun ikinci kez kişi açmaması; tekrar gelen WhatsApp olayının ikinci kayıt üretmemesi |
| `test_randevu.py` | Çakışma, sınır teması (11:00 biten + 11:00 başlayan çakışmaz), geçmiş tarih, çalışma saati/günü, iptal sonrası saatin yeniden açılması |
| `test_kb.py` | Pasifleştirilmiş bir fiyatın `.hermes.md`'ye sızmaması; yazmanın idempotent olması |
| `test_webhook.py` | HMAC'in **ham gövde** üzerinden doğrulanması; gövde oynanırsa reddedilmesi; grup mesajlarının yoksayılması; ajan çökse bile hastanın sessiz kalmaması |
| `test_panel.py` | Parolasız erişimin engellenmesi; panel cookie'sinin iç API'yi açmaması; çakışmada 409 dönmesi |
| `test_saglik.py` | Tek hatanın sessiz, iki ardışık hatanın uyarı vermesi; uyarının tekrarlanmaması; düzelmenin bir kez bildirilmesi |

## Sözleşme

Testler `PRD.md` §4'teki modül imzalarına yazıldı. İnşa sırasında bir imzayı değiştirmek
istersen önce PRD'yi güncelle — testler oradan türetildi, tersi değil.
