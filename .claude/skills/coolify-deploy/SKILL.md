---
name: coolify-deploy
description: Bir projeyi VPS'teki Coolify'ya bağlayıp subdomain + SSL ile deploy et. Tetiklenir: "coolify", "coolify'a bağla", "coolify deploy", "subdomain aç", "vps'e coolify ile yükle". Tek-servis application tuzağı, postgres manuel coolify network, env/domain formatı tuzaklarını bilir.
---

# Coolify deploy

Bir projeyi Coolify'ya bağlayıp `subdomain.botfusions.com` altında HTTPS ile yayınla.

## Önkoşullar

1. **Kod GitHub'da** (public repo veya Coolify GitHub App'i bağlı).
2. **DNS:** subdomain'in A record'u VPS IP'sine baksın.
   ```bash
   dig +short subdomain.botfusions.com A @1.1.1.1   # VPS IP dönmeli
   ```
   Farklı/yanlış IP dönüyorsa önce DNS'i düzelt — SSL alınamaz.
3. **VPS erişimi:** `ssh root@<ip>` (port 22). Coolify kurulu mu kontrol et:
   ```bash
   docker ps | grep coolify   # coolify + coolify-realtime + coolify-db görmeli
   ls /data/coolify           # kurulu
   ```

## Tuzak 1 — Coolify compose'un TAMAMINI çalıştırmıyor

**En yaygın ve gizli hata.** Coolify, repodaki `docker-compose.yml`'i olduğu gibi çalıştırmaz; çoğu zaman **tek servisli "Application"** (yalnızca Dockerfile/ana image) kurar. Compose'taki `postgres`, `openwa`, `redis` gibi yan servisler **kullanılmaz** → app bunlara bağlanamaz → crash loop (restart limit).

Belirtisi: app crash loop, ama manuel `docker compose up` sağlıklı çalışıyor.

Kontrol et (gerçek compose dosyası):
```bash
cat /data/coolify/applications/<uuid>/docker-compose.yaml
```
Sadece bir servis varsa → yan servisleri kendin kur (Tuzak 2).

## Tuzak 2 — yan servisleri manuel `coolify` network'ünde çalıştır

Coolify app container'ı `coolify` ağında. postgres vb. de **aynı ağda, servis adıyla** olmalı ki app `@postgres:5432` gibi ulaşabilsin.

postgres örneği (mevcut volume'le, data korunur):
```bash
PW=<postgres parolası>
docker run -d --name postgres --network coolify --restart unless-stopped \
  -e POSTGRES_USER=klinik -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=klinik_crm \
  -e TZ=Europe/Istanbul \
  -v <pgdata-volume>:/var/lib/postgresql/data \
  postgres:17-alpine
```
- `--network coolify` ŞART (yoksa app ulaşamaz).
- `--restart unless-stopped` → reboot'ta kalkar (Coolify yönetiminde değil).
- Bu servisler **Coolify panelinde görünmez**; `docker ps` ile izlenir.

## Tuzak 3 — Domains alanında `https://` ŞART

Coolify'da **Domains** alanına `subdomain.botfusions.com` yazarsan (scheme'siz) Coolify onu **path** sanar → Traefik label `Host(``)` boş kalır → `404`.

Doğru: **`https://subdomain.botfusions.com`** (scheme'li). Kaydet → otomatik redeploy → Traefik doğru route + Let's Encrypt SSL.

Doğrula:
```bash
docker inspect <app-container> --format '{{range .Config.Labels}}{{.}}{{println}}{{end}}' | grep Host
# Host(`subdomain.botfusions.com`) && PathPrefix(`/`)  -> doğru
```

## Tuzak 4 — env: uzun satırlar kesilir

Coolify → Environment'e değer yapıştırırken **uzun satırlar** (`DATABASE_URL=postgresql://...64hex...@host`) sıklıkla eksik girer. Tüm KEY'leri teker teker ekle, sonra varlığını denetle:
```bash
F=/data/coolify/applications/<uuid>/.env
for k in POSTGRES_PASSWORD DATABASE_URL COOKIE_SECRET ...; do
  grep -q "^$k=" "$F" && echo "$k OK" || echo "$k *** EKSİK ***"
done
```
- `DATABASE_URL` host'u **servis adı** olmalı (`@postgres:5432`), `localhost` değil — container ağı içinde.
- compose'ta `environment:` override varsa `${VAR}` interpolasyonu Coolify'da güvenilmez; tam değeri env'e yaz.

## Sıra

1. DNS doğrula (önkoşul 2).
2. Coolify'da resource oluştur, GitHub repo + branch bağla.
3. Domains: `https://subdomain...` (scheme'li).
4. Environment: tüm sırlar + servis-adlı DATABASE_URL (uzun satırları teker teker).
5. Deploy → crash loop olursa: gerçek compose dosyasını oku (Tuzak 1), yan servisleri manuel kur (Tuzak 2).
6. Doğrula (aşağı).

## Doğrulama

Lokal makineden DNS cache yanıltabilir; **VPS içinden** dene:
```bash
ssh root@<ip> 'curl -sI https://subdomain.botfusions.com'   # HTTP/2 ... -> SSL+routing OK
ssh root@<ip> 'curl -sL https://subdomain.botfusions.com/ | grep -i title'
```
- `405` (HEAD) → SSL var, route çalışıyor (GET dene).
- `404` → domain/Traefik yanlış (Tuzak 3).
- app crash loop → yan servis eksik (Tuzak 1/2) veya env (Tuzak 4).

Crash sebebini bul: image'ı Coolify env'iyle manuel çalıştır,
```bash
docker run --rm --env-file /data/coolify/applications/<uuid>/.env <image> 2>&1 | tail
```
(bu yan servisleri vermez, ama import/env hatasını gösterir; connection hatası normaldir.)
