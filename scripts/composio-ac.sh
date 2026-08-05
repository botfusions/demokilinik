#!/usr/bin/env bash
# Composio MCP bağlantısını açar (Google Takvim + Gmail).
#
# Önce https://app.composio.dev üzerinden:
#   1. Hesap aç, Google Calendar ve Gmail araçlarını bağla (OAuth ekranı müşterinin
#      Google hesabıyla tamamlanır)
#   2. API anahtarını ve MCP URL'ini al
#   3. .env içine COMPOSIO_API_KEY ve COMPOSIO_MCP_URL yaz
# Sonra bu scripti çalıştır.

set -euo pipefail
cd "$(dirname "$0")/.."

set -a; source .env; set +a

if [ -z "${COMPOSIO_API_KEY:-}" ] || [ -z "${COMPOSIO_MCP_URL:-}" ]; then
  echo "HATA: .env içinde COMPOSIO_API_KEY ve COMPOSIO_MCP_URL dolu olmalı." >&2
  exit 1
fi

CONFIG=hermes-home/config.yaml

if grep -q "^mcp_servers:" "$CONFIG"; then
  echo "mcp_servers zaten tanımlı — $CONFIG dosyasını elle düzenleyin."
  exit 0
fi

cat >> "$CONFIG" <<YAML

mcp_servers:
  composio:
    url: "${COMPOSIO_MCP_URL}"
    headers:
      Authorization: "Bearer ${COMPOSIO_API_KEY}"
YAML

echo "Composio config.yaml'a eklendi."
echo "Doğrulama:"
HERMES_HOME="$PWD/hermes-home" hermes mcp 2>&1 | head -20
