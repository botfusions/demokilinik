# Güvenlik Tarama Raporu — 16-08-2026

**Kapsam:** Tüm repo (`app/`, `egitim/`, `scripts/`, `docker-compose.yml`, kök HTML dosyaları)
**Yöntem:** Otomatik kod taraması + her bulgu için bağımsız yanlış-pozitif doğrulaması (≥8/10 güven eşiği)
**Sonuç:** 1 doğrulanmış bulgu (YÜKSEK), 1 eşik altı not (ORTA-yo)

---

## Bulgu 1: `/demo` kimliksiz erişim tüm panel verisini açıyor — `app/main.py`

* **Severity:** YÜKSEK (mekanizma) / bugün ORTA (veri sahte)
* **Kategori:** `authz-bypass` / veri ifşası
* **Doğrulama:** CONFIRMED — güven 9/10
* **Açıklama:** `GET /demo` (`main.py:356-366`) kimlik doğrulaması olmadan imzalı `{"d": 1}` izleyici cookie'si dağıtıyor. Bu cookie `personel()` korumasını tüm GET isteklerinde geçiyor (`main.py:207-215`) ve `yonetici()` içindeki rol + ikinci parola kontrollerini tamamen atlatıyor (`main.py:254-256`). Rotalar rol ne olursa olsun **filtresiz gerçek DB verisi** döndürüyor: `/hastalar` (isim + telefon, `main.py:782`), `/hastalar/{id}` (tam WhatsApp transkriptleri, `main.py:803`), `/takvim`, `/kullanicilar` (kullanıcı adları + hasta telefonları içeren denetim günlüğü).
* **Sömürü senaryosu:** İnternetteki herhangi biri panele gider (`/` adresi doğrudan `/demo`'ya yönlendiriyor, `main.py:225` — demo URL'sini bilmek bile gerekmiyor), izleyici cookie'sini alır ve tüm klinik veritabanını okur: hasta kimlikleri, telefonlar, görüşme geçmişleri. Bugün demoklinik.botfusions.com canlı ama veri sahte. **Tek koruma `DEMO_KAPALI` env değişkeni — `.env`, `.env.example` ve `docker-compose.yml`'in hiçbirinde tanımlı değil.** Gerçek klinik verisi geldiğinde operatörün bunu hatırlamasına bağlı; unutulursa sıfır beceriyle tam hasta verisi ihlali.
* **Öneri:**
  1. Fail-closed yap: `/demo` fallback'ini kaldır veya `DEMO_ACIK=1` opt-in'e çevir (varsayılan kapalı).
  2. `DEMO_KAPALI`'yı `.env.example` ve docker-compose `environment:` bloğuna ekle.
  3. İzleyici rolünden PII sayfalarını (`/hastalar*`, `/kullanicilar`, `/takvim`) rol bağımsız çıkar.

---

## Not (eşiğin altında, 7/10 — ama 1 satırlık düzeltme): sabit fallback cookie secret'i — `egitim/sunucu.py:49`

`URLSafeSerializer(os.environ.get("COOKIE_SECRET", "gelistirme"))` — ana uygulamanın aksine (`app/main.py:104` hard-fail yapar) vendor konsolu env değişkeni eksikse cookie'leri **git'te kamuya açık** `"gelistirme"` string'iyle imzalar. Console docker-compose dışında, elle `uvicorn egitim.sunucu:app` ile bağımsız çalıştırılmak üzere tasarlanmış (`:55` yorumu) — tam fallback'in sessizce devreye girdiği senaryo. Bilinen kaynak + `{"k": 1}` (ilk admin tahmin edilebilir) ile cookie sahtelenip tam vendor-admin erişimi alınır. Düzeltme: `egitim/sunucu.py`'de de `COOKIE_SECRET` yoksa `raise RuntimeError`.

---

## Temiz doğrulanan alanlar

- **SQL injection:** Tüm sorgular parametreli (`crm.py`, `db.py` dahil)
- **XSS:** Jinja autoescape her yerde açık, `|safe` yok; `panel.js` innerHTML sadece sabit SVG; kök HTML'ler statik mockup (ağ çağrısı yok)
- **Webhook auth:** Sabit süreli HMAC, boş secret'te fail-closed
- **İç API:** `X-Ic-Anahtar` kapılı + nginx `/api/` ve `/webhook/`'u dışarıdan blokluyor
- **Parola saklama:** scrypt (n=2^16) + `compare_digest`
- **CSRF:** Tüm oturum cookie'leri SameSite=Lax, durum değiştiren GET yok
- **Path traversal / eval / pickle / YAML / TLS:** Sorun yok

---

*Oluşturan: Claude Code güvenlik taraması, 2026-08-16*
