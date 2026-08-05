#!/usr/bin/env bash
# İlk kurulum — hem yerel makine hem VPS için. Tekrar çalıştırılabilir.

set -euo pipefail
cd "$(dirname "$0")/.."
KOK="$PWD"

echo "→ Klinik resepsiyonist ajanı kuruluyor: $KOK"

# ── 1. .env ─────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  for degisken in POSTGRES_PASSWORD COOKIE_SECRET IC_API_ANAHTARI OPENWA_API_KEY \
                  WEBHOOK_SECRET OPENWA_API_KEY_PEPPER; do
    deger=$(openssl rand -hex 32)
    sed -i.bak "s|^${degisken}=.*|${degisken}=${deger}|" .env && rm -f .env.bak
  done
  # DATABASE_URL üretilen parolayla eşleşmeli
  parola=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
  sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://klinik:${parola}@localhost:5434/klinik_crm|" .env && rm -f .env.bak

  echo "  .env oluşturuldu, sırlar üretildi."
  echo "  ⚠ Elle doldurulacaklar: PANEL_PAROLA, ZAI_API_KEY veya OPENAI_API_KEY,"
  echo "    COMPOSIO_*, YONETICI_EPOSTA, SMTP_*"
else
  echo "  .env zaten var, dokunulmadı."
fi

set -a; source .env; set +a

# ── 2. Python ───────────────────────────────────────────────
if [ ! -d .venv ]; then
  # 3.10+ şart: kodda `X | None` tip birleşimi var
  PY=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)
  "$PY" -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "  Python ortamı hazır ($(.venv/bin/python -V))."

# ── 3. Hermes ───────────────────────────────────────────────
if ! command -v hermes >/dev/null 2>&1; then
  echo "  Hermes kuruluyor..."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "  Hermes: $(hermes --version 2>&1 | head -1)"

# Ajan bu klasöre özel — global ~/.hermes kullanılmaz
export HERMES_HOME="$KOK/hermes-home"
hermes config check >/dev/null 2>&1 && echo "  Ajan yapılandırması geçerli."

# ── 4. Docker ───────────────────────────────────────────────
docker compose up -d
echo "  Postgres ve OpenWA ayakta."

# Postgres hazır olana kadar bekle
for _ in $(seq 1 30); do
  docker compose exec -T postgres pg_isready -U klinik >/dev/null 2>&1 && break
  sleep 2
done

.venv/bin/python -m app.db
echo "  Veritabanı şeması kuruldu."

# ── 5. WhatsApp oturumu ─────────────────────────────────────
echo "→ WhatsApp oturumu hazırlanıyor..."
sleep 10

SID=$(curl -s "${OPENWA_URL}/api/sessions" -H "X-Api-Key: ${OPENWA_API_KEY}" \
      | .venv/bin/python -c "
import json,sys,os
for o in json.load(sys.stdin):
    if o['name'] == os.environ['OPENWA_SESSION']:
        print(o['id']); break
" || true)

if [ -z "$SID" ]; then
  SID=$(curl -s -X POST "${OPENWA_URL}/api/sessions" \
        -H "X-Api-Key: ${OPENWA_API_KEY}" -H "Content-Type: application/json" \
        -d "{\"name\":\"${OPENWA_SESSION}\"}" \
        | .venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['id'])")
  echo "  Oturum oluşturuldu: $SID"
fi

# Webhook kaydı — grup mesajları filtrelenir
VARSA=$(curl -s "${OPENWA_URL}/api/sessions/${SID}/webhooks" -H "X-Api-Key: ${OPENWA_API_KEY}" \
        | .venv/bin/python -c "import json,sys; print(len(json.load(sys.stdin)))")

if [ "$VARSA" = "0" ]; then
  curl -s -X POST "${OPENWA_URL}/api/sessions/${SID}/webhooks" \
    -H "X-Api-Key: ${OPENWA_API_KEY}" -H "Content-Type: application/json" \
    -d "{\"url\":\"${KOPRU_WEBHOOK_URL:-http://host.docker.internal:8000/webhook/whatsapp}\",
         \"events\":[\"message.received\"],
         \"secret\":\"${WEBHOOK_SECRET}\",
         \"filters\":{\"conditions\":[{\"field\":\"isGroup\",\"operator\":\"is\",\"value\":false}]}}" \
    >/dev/null
  echo "  Webhook kaydedildi."
fi

curl -s --max-time 240 -X POST "${OPENWA_URL}/api/sessions/${SID}/start" \
  -H "X-Api-Key: ${OPENWA_API_KEY}" >/dev/null || true

echo
echo "════════════════════════════════════════════════════════"
echo " Kurulum tamam."
echo
echo " 1. Köprüyü başlat:  .venv/bin/uvicorn app.main:app --port 8000"
echo " 2. Panel:           http://localhost:8000  (PANEL_PAROLA ile)"
echo " 3. QR okut:         http://localhost:2785  → klinik oturumu → QR"
echo
echo " ⚠ Klinik için AYRI bir WhatsApp numarası kullanın."
echo "   İlk günler normal kullanıcı gibi davranın, toplu mesaj atmayın."
echo "════════════════════════════════════════════════════════"
