#!/usr/bin/env bash
# Günlük yedek. Cron:
#   0 3 * * * /opt/hermes-klinik/scripts/yedekle.sh >> /var/log/klinik-yedek.log 2>&1

set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

YEDEK_DIZIN="${YEDEK_DIZIN:-/var/backups/klinik}"
mkdir -p "$YEDEK_DIZIN"
DAMGA=$(date +%Y%m%d-%H%M)

# Hasta verisi — asıl korunması gereken bu
docker compose exec -T postgres pg_dump -U klinik klinik_crm | gzip > "$YEDEK_DIZIN/klinik_crm-$DAMGA.sql.gz"

# WhatsApp oturumu: kaybolursa QR yeniden okutulur, ama yedeklemek 10 saniye kazandırır
tar czf "$YEDEK_DIZIN/openwa-oturum-$DAMGA.tar.gz" openwa-data/sessions 2>/dev/null || true

# 30 günden eskiyi at
find "$YEDEK_DIZIN" -name '*.gz' -mtime +30 -delete

echo "$(date '+%F %T') yedek alındı: $DAMGA ($(du -sh "$YEDEK_DIZIN" | cut -f1))"
