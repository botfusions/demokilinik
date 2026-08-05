#!/usr/bin/env bash
# VPS'te güncelleme. GitHub'dan çek, bağımlılıkları tazele, servisleri yeniden başlat.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ Güncelleme çekiliyor..."
git pull --ff-only

.venv/bin/pip install -q -r requirements.txt

docker compose pull
docker compose up -d

# Şema değişmişse uygula (CREATE TABLE IF NOT EXISTS — mevcut veriye dokunmaz)
set -a; source .env; set +a
.venv/bin/python -m app.db

sudo systemctl restart klinik-kopru
sleep 3
sudo systemctl --no-pager status klinik-kopru | head -5

echo
echo "→ Sağlık kontrolü:"
curl -s -o /dev/null -w "  panel: %{http_code}\n" localhost:8000/giris
docker compose ps --format '  {{.Name}}\t{{.Status}}'
