# Eğitim Merkezi — Devir Notu

**Son güncelleme:** 2026-08-07 · **Durum:** çalışır, 11 test yeşil (DB'siz atlananlar hariç)

Ajan eğitim alt-sisteminin kendi devir notu. Ana sistem için kök dizindeki
`HANDOFF.md`'ye bak.

## Bir cümlede

Klinik panelinden **ayrı**, kendi başına çalışan bir **vendor (kurulumcu)
konsolu**: ajanı yazarak eğitir, klinik sitesinden kazır, bilgi tabanını
düzenler/yayınlar ve **tek tıkla ajanı sınar** (satış demosu = eğitim doğrulama).

## Rol ayrımı (bu sistemin nedeni)

| | Vendor konsolu (`egitim/`, port 8001) | Müşteri paneli (`/egitim`, port 8000) |
|---|---|---|
| Yazarak eğit (aktif) | ✅ | ✅ |
| Site tarama (URL → taslak) | ✅ | ❌ |
| KB düzenle / aktifleştir / pasifleştir | ✅ | ❌ |
| Ajana sor (demo) | ✅ | ❌ |

Müşteri URL girmesin diye var: "müşteri url girmesine gerek yok, bizim ajanı
eğitip müşteriye vermamiz lazım." Müşteri panelinin `/egitim` rotası yalnız
yazarak-eğit'i çağırır; tarama ve düzenleme burada.

## Dosya haritası

```
egitim/__init__.py   lib: metni_ayristir (yaz→aktif), site_tara (URL→pasif),
                     _llm_cagir, _temizle, _json_ayistir. app.hafif/kb'ye bağımlı.
egitim/__main__.py   `python -m egitim` → LLM'siz parse self-check.
egitim/sunucu.py     standalone FastAPI (port 8001): giriş, konsol, Ajana sor,
                     egit/tara, KB düzenle. app.db/kb/kullanici/ajan/kural'ı yeniden kullanır.
egitim/sablon.html   tek sayfa, dark, inline CSS (klinik /static'sine bağımlı değil).
                     WhatsApp mockup "Ajana sor" + eğitim kartları + KB tablosu.
```

## Çalıştırma

```bash
.venv/bin/uvicorn egitim.sunucu:app --port 8001
# /giris (klinik admin hesabı) → konsol
# Demo: "Saatler nedir?" → kural katmanı cevaplar. "Randevu dene" → tam ajan.
```

Müşteri paneli ayrı: `.venv/bin/uvicorn app.main:app --port 8000` → Eğitim sekmesi.

## Ajana sor (demo / eğitim doğrulama)

`POST /sor` → `_ajan_sor(conn, gecmis, mesaj)` gerçek pipeline'ı çalıştırır,
**yan etkisiz** (görüşme DB'ye yazılmaz, WhatsApp'a mesaj gitmez):

1. `kural.cevap_dene` (LLM'siz) — fiyat/saat/adres net eşleşme
2. `hafif.cevap_dene` (ucuz LLM) — bilgi soruları
3. `ajan.cevap_uret` + `konum_ayikla` (tam ajan, tool-calling)

Sohbet bellekte (`dict[user_id]`, `egitim/sunucu.py:_sohbet`) — yeniden
başlatmada sıfırlanır, çok-worker'da paylaşılmaz. Üretim demosuna çıkarsa
Redis/DB'ye taşınmalı.

**Dikkat:** ajan gerçek araçları çağırır — "Randevu dene" **gerçek randevu
açabilir** (DB'ye yazar). Bu bilinçli ("simülasyon değil" çerçevesi, referans
ekranlarla aynı). Demo'da randevu açılmasını istemiyorsan `_ajan_sor`'da
randevu sinyali gelince kısa devre yapacak bir satır yeter.

## İki sert kural (değişmedi)

1. **Fiyat asla KB'ye girmez** — `metni_ayristir` fiyat görünce kayıt üretmez,
   uyarı döner. Fiyatın tek kaynağı `hizmetler` tablosu (klinik panelinde).
2. **Makine-kazınan içerik taslak (pasif) iner** — `site_tara` çıktısı
   `aktif=False`; vendor konsolunda elle onaylanır. Elle girilen (vendor yazımı
   + müşteri yazımı) `aktif=True`.

## Bilinçli sınırlar (ponytail)

- **Vendor = admin girişi.** Ayrı `EGITIM_PAROLA`/vendor-user yok; clinic admin
  DB'si paylaşılır. Ayrı vendor kimliği gerekirse `kullanicilar`'a `rol='satici'`.
- **İki app aynı `.hermes.md`'ye yazar.** `hermes_md_yaz` idempotent (içerik
  aynıysa dokunmaz); vendor kurulum anında, clinic nöbetçi sonradan — çakışma yok.
- **Şablon inline CSS.** Klinik `panel.css`'ini paylaşmaz; port/bağımlılık
  izolasyonu için. Güzelleşme istenirse `app/static` mount edilir.
- **`.hermes.md` adı korundu.** Hermes CLI kaldırıldı (sadece dosya adı kaldı);
  yeniden adlandırma bu işin dışı.

## Sıradaki olası adımlar

- Çok-kiracılık (birden çok klinik, her birine ayrı ajan) — hâlâ kapsam dışı.
- Demo'da randevu kilidi (gerçek randevu açılmasını engelle) — kullanıcı isterse.
- Vendor şablonuna klinik `panel.css`'i mount → görsel uyum.
