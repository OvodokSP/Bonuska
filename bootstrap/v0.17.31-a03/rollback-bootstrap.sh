#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${BONUSKA_APP_ROOT:-/opt/Bonuska}"
BACKUP_ROOT="${BONUSKA_A03_BOOTSTRAP_BACKUP_ROOT:-/opt/Bonuska_update_agent_bootstrap_backups}"
STATE_FILE="${BACKUP_ROOT}/LATEST"

log() {
  printf '[v0.17.31-a03 rollback] %s\n' "$*"
}

die() {
  printf '[v0.17.31-a03 rollback] ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || die "run as root"
[[ -f "${STATE_FILE}" ]] || die "bootstrap backup pointer not found: ${STATE_FILE}"

BACKUP_DIR="$(cat "${STATE_FILE}")"
[[ -d "${BACKUP_DIR}" ]] || die "bootstrap backup directory missing: ${BACKUP_DIR}"

PARSER="${APP_ROOT}/backend/bonuska/parsers/pdf_invoice.py"
NGINX_SITE="$(cat "${BACKUP_DIR}/nginx-site-path.txt")"

log "Stopping Update Agent"
systemctl disable --now bonuska-update-agent.service >/dev/null 2>&1 || true

log "Restoring PDF parser"
cp -a "${BACKUP_DIR}/pdf_invoice.py" "${PARSER}"

log "Restoring Nginx site"
cp -a "${BACKUP_DIR}/nginx-site.conf" "${NGINX_SITE}"

rm -f /etc/nginx/snippets/bonuska-update-agent.conf
rm -f /etc/systemd/system/bonuska-update-agent.service
rm -rf /usr/local/lib/bonuska-update-agent
rm -rf /etc/bonuska-update-agent
systemctl daemon-reload

log "Validating and reloading Nginx"
nginx -t
systemctl reload nginx

log "Rebuilding Bonuska from restored source"
cd "${APP_ROOT}"
docker compose build bonuska
docker compose up -d bonuska

log "Waiting for readiness"
READY=0
for _ in $(seq 1 40); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 3
done
[[ "${READY}" -eq 1 ]] || die "Bonuska did not become ready after rollback"

FINGERPRINT="$(python3 backend/scripts/code_fingerprint.py --root "${APP_ROOT}" --fingerprint-only)"
log "ROLLBACK_FINGERPRINT=${FINGERPRINT}"
log "BOOTSTRAP_ROLLBACK=PASS"
