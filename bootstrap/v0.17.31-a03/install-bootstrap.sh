#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE_FINGERPRINT="43139dcdfd543197961369c2d3163a38ae763a4be8b642d186779b4bd8d36b41"
APP_ROOT="${BONUSKA_APP_ROOT:-/opt/Bonuska}"
PUBLIC_IP="${BONUSKA_PUBLIC_IP:-31.77.56.66}"
REGRESSION_PDF="${BONUSKA_VR016877_PDF:-/tmp/bonuska-regression-vr016877.pdf}"
OLD_REGRESSION_PDF="${BONUSKA_LP109691_PDF:-/tmp/bonuska-regression-lp109691.pdf}"
NGINX_SITE="${BONUSKA_NGINX_SITE:-/etc/nginx/sites-available/bonuska}"
BACKUP_ROOT="${BONUSKA_A03_BOOTSTRAP_BACKUP_ROOT:-/opt/Bonuska_update_agent_bootstrap_backups}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PARSER="${APP_ROOT}/backend/bonuska/parsers/pdf_invoice.py"
APPEND_FILE="${SCRIPT_DIR}/pdf_invoice_v01731_a03_append.py"
AGENT_FILE="${SCRIPT_DIR}/bonuska-update-agent.py"
AGENT_UNIT="${SCRIPT_DIR}/bonuska-update-agent.service"
NGINX_SNIPPET_SOURCE="${SCRIPT_DIR}/bonuska-update-agent.nginx"

log() {
  printf '[v0.17.31-a03 bootstrap] %s\n' "$*"
}

die() {
  printf '[v0.17.31-a03 bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

rollback_on_error() {
  local rc=$?
  if [[ "${BOOTSTRAP_MUTATED:-0}" == "1" ]]; then
    printf '[v0.17.31-a03 bootstrap] INSTALL_FAILED=YES\n' >&2
    printf '[v0.17.31-a03 bootstrap] AUTOMATIC_BOOTSTRAP_ROLLBACK=STARTED\n' >&2
    BONUSKA_APP_ROOT="${APP_ROOT}"     BONUSKA_A03_BOOTSTRAP_BACKUP_ROOT="${BACKUP_ROOT}"       bash "${SCRIPT_DIR}/rollback-bootstrap.sh" || true
  fi
  exit "${rc}"
}
trap rollback_on_error ERR

[[ "${EUID}" -eq 0 ]] || die "run as root"
for cmd in python3 docker curl nginx openssl systemctl sha256sum; do
  command -v "${cmd}" >/dev/null 2>&1 || die "required command missing: ${cmd}"
done
for path in "${PARSER}" "${APPEND_FILE}" "${AGENT_FILE}" "${AGENT_UNIT}" "${NGINX_SNIPPET_SOURCE}" "${NGINX_SITE}"; do
  [[ -f "${path}" ]] || die "required file missing: ${path}"
done
[[ -f "${REGRESSION_PDF}" ]] || die "required regression PDF missing: ${REGRESSION_PDF}"

if systemctl list-unit-files bonuska-update-agent.service --no-legend 2>/dev/null | grep -q bonuska-update-agent; then
  die "bonuska-update-agent already exists; refusing ambiguous bootstrap"
fi
[[ ! -e /usr/local/lib/bonuska-update-agent/agent.py ]] || die "agent files already exist"

log "Checking exact v0.17.31 a02 source fingerprint"
cd "${APP_ROOT}"
CURRENT_FINGERPRINT="$(python3 backend/scripts/code_fingerprint.py --root "${APP_ROOT}" --fingerprint-only)"
printf 'BASE_FINGERPRINT_EXPECTED=%s\n' "${BASE_FINGERPRINT}"
printf 'BASE_FINGERPRINT_ACTUAL=%s\n' "${CURRENT_FINGERPRINT}"
[[ "${CURRENT_FINGERPRINT}" == "${BASE_FINGERPRINT}" ]] || die "source fingerprint is not exact v0.17.31 a02"

grep -q '^0\.17\.31$' VERSION || die "VERSION is not 0.17.31"
grep -q 'BONUSKA_V01731_A03_HEADERLESS_CONTINUATION_BEGIN' "${PARSER}" && die "parser corrective already present"

log "Checking production readiness before writes"
curl -fsS --max-time 10 http://127.0.0.1:8000/health/ready >/tmp/bonuska-a03-ready-before.json
printf 'READINESS_BEFORE=PASS\n'

FREE_BYTES="$(df -B1 --output=avail "${APP_ROOT}" | tail -n1 | tr -d ' ')"
MIN_FREE=$((2 * 1024 * 1024 * 1024))
(( FREE_BYTES >= MIN_FREE )) || die "less than 2 GiB free: ${FREE_BYTES}"
printf 'DISK_PREFLIGHT=PASS available=%s required=%s\n' "${FREE_BYTES}" "${MIN_FREE}"

if ! nginx -V 2>&1 | grep -q -- '--with-http_auth_request_module'; then
  die "Nginx auth_request module is unavailable"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_ROOT}/v0.17.31-a03_${STAMP}"
mkdir -p "${BACKUP_DIR}"
cp -a "${PARSER}" "${BACKUP_DIR}/pdf_invoice.py"
cp -a "${NGINX_SITE}" "${BACKUP_DIR}/nginx-site.conf"
printf '%s\n' "${NGINX_SITE}" > "${BACKUP_DIR}/nginx-site-path.txt"
mkdir -p "${BACKUP_ROOT}"
printf '%s\n' "${BACKUP_DIR}" > "${BACKUP_ROOT}/LATEST"
BOOTSTRAP_MUTATED=1
printf 'BOOTSTRAP_BACKUP=%s\n' "${BACKUP_DIR}"

log "Applying strict headerless continuation-page safeguard"
cat "${APPEND_FILE}" >> "${PARSER}"
python3 -m py_compile "${PARSER}"
grep -q 'BONUSKA_V01731_A03_HEADERLESS_CONTINUATION_END' "${PARSER}"
printf 'PARSER_SOURCE_PATCH=PASS\n'

log "Installing host-side Update Agent"
install -d -m 0700 /usr/local/lib/bonuska-update-agent
install -m 0700 "${AGENT_FILE}" /usr/local/lib/bonuska-update-agent/agent.py

install -d -m 0700 /etc/bonuska-update-agent
if [[ ! -f /etc/bonuska-update-agent/signing.key ]]; then
  openssl rand -base64 48 | tr -d '\n' > /etc/bonuska-update-agent/signing.key
fi
chmod 0600 /etc/bonuska-update-agent/signing.key

cat > /etc/bonuska-update-agent/config.json <<JSON
{
  "public_ip": "${PUBLIC_IP}",
  "app_root": "${APP_ROOT}"
}
JSON
chmod 0600 /etc/bonuska-update-agent/config.json
touch /etc/bonuska-update-agent/enabled
chmod 0600 /etc/bonuska-update-agent/enabled

install -m 0644 "${AGENT_UNIT}" /etc/systemd/system/bonuska-update-agent.service
install -m 0644 "${NGINX_SNIPPET_SOURCE}" /etc/nginx/snippets/bonuska-update-agent.conf

# One-time export copy for transferring the signing key to the user's workstation.
cp -a /etc/bonuska-update-agent/signing.key /root/Bonuska_Update_Signing_Key.txt
chmod 0600 /root/Bonuska_Update_Signing_Key.txt

log "Adding Update Agent locations to the existing HTTPS Bonuska server block"
python3 - "${NGINX_SITE}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
include_line = "    include /etc/nginx/snippets/bonuska-update-agent.conf;\n"

if "/etc/nginx/snippets/bonuska-update-agent.conf" in text:
    raise SystemExit("Update Agent include already exists")

lines = text.splitlines(keepends=True)
server_start = None
depth = 0
candidates = []

for i, line in enumerate(lines):
    clean = line.split("#", 1)[0]
    if server_start is None and re.match(r"^\s*server\s*\{", clean):
        server_start = i
        depth = clean.count("{") - clean.count("}")
        continue

    if server_start is not None:
        depth += clean.count("{") - clean.count("}")
        if depth == 0:
            block = "".join(lines[server_start : i + 1])
            if re.search(r"\blisten\s+443(?:\s|;)", block):
                score = 0
                if "proxy_pass http://127.0.0.1:8000" in block:
                    score += 10
                if "server_name 31.77.56.66" in block:
                    score += 5
                candidates.append((score, server_start, i))
            server_start = None

if not candidates:
    raise SystemExit("HTTPS server{} block not found")

candidates.sort(reverse=True)
_, start, end = candidates[0]
lines.insert(end, include_line)
path.write_text("".join(lines), encoding="utf-8")
PY

nginx -t
systemctl reload nginx
printf 'NGINX_UPDATE_AGENT_BRIDGE=PASS\n'

systemctl daemon-reload
systemctl enable --now bonuska-update-agent.service

for _ in $(seq 1 20); do
  if curl -fsS --max-time 3 http://127.0.0.1:8765/health >/tmp/bonuska-a03-agent-health.json 2>/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1:8765/health >/tmp/bonuska-a03-agent-health.json
printf 'UPDATE_AGENT_LOCAL_HEALTH=PASS\n'

log "Rebuilding Bonuska with parser corrective"
cd "${APP_ROOT}"
docker compose build bonuska
docker compose up -d bonuska

READY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/health/ready >/tmp/bonuska-a03-ready-after.json 2>/dev/null; then
    READY=1
    break
  fi
  sleep 3
done
[[ "${READY}" -eq 1 ]] || die "Bonuska did not become ready after rebuild"
printf 'READINESS_AFTER=PASS\n'

log "Running exact ВР016877 parser regression inside production image"
docker cp "${REGRESSION_PDF}" bonuska:/tmp/bonuska-regression-vr016877.pdf >/dev/null
docker exec -i bonuska python - <<'PY'
from decimal import Decimal
from pathlib import Path
from bonuska.parsers.pdf_invoice import parse_invoice

invoice = parse_invoice(Path("/tmp/bonuska-regression-vr016877.pdf"))
items = list(invoice.items)
rows = {int(item.row_no) for item in items}
total = sum((item.amount for item in items), Decimal("0.00"))

assert len(items) == 482, len(items)
assert rows == set(range(1, 483)), (min(rows), max(rows), len(rows))
assert 332 in rows and 357 in rows
assert total == Decimal("93677502.64"), total

print("VR016877_ITEMS=482")
print("VR016877_TOTAL=93677502.64")
print("VR016877_ROWS_332_357=PRESENT")
print("VR016877_REGRESSION=PASS")
PY

if [[ -f "${OLD_REGRESSION_PDF}" ]]; then
  log "Running ЛП109691 anti-regression"
  docker cp "${OLD_REGRESSION_PDF}" bonuska:/tmp/bonuska-regression-lp109691.pdf >/dev/null
  docker exec -i bonuska python - <<'PY'
from decimal import Decimal
from pathlib import Path
from bonuska.parsers.pdf_invoice import parse_invoice

invoice = parse_invoice(Path("/tmp/bonuska-regression-lp109691.pdf"))
items = list(invoice.items)
total = sum((item.amount for item in items), Decimal("0.00"))

assert len(items) == 32, len(items)
assert total == Decimal("7989783.44"), total

print("LP109691_ITEMS=32")
print("LP109691_TOTAL=7989783.44")
print("LP109691_REGRESSION=PASS")
PY
else
  printf 'LP109691_REGRESSION=SKIPPED file=%s\n' "${OLD_REGRESSION_PDF}"
fi

log "Checking unauthenticated Update Center is not exposed"
HTTP_CODE="$(curl -ksS -o /tmp/bonuska-a03-update-anon.html -w '%{http_code}' --max-time 10 "https://${PUBLIC_IP}/system/update-agent/" || true)"
printf 'UPDATE_AGENT_ANON_HTTP=%s\n' "${HTTP_CODE}"
[[ "${HTTP_CODE}" != "200" ]] || die "Update Agent page is accessible without Bonuska authentication"
printf 'UPDATE_AGENT_AUTH_GATE=PASS\n'

TARGET_FINGERPRINT="$(python3 backend/scripts/code_fingerprint.py --root "${APP_ROOT}" --fingerprint-only)"
printf 'TARGET_FINGERPRINT=%s\n' "${TARGET_FINGERPRINT}"

printf 'SIGNING_KEY_EXPORT=/root/Bonuska_Update_Signing_Key.txt\n'
printf 'UPDATE_AGENT_URL=https://%s/system/update-agent/\n' "${PUBLIC_IP}"
printf 'SCHEMA_EXPECTED=29\n'
printf 'ROUTES_EXPECTED=138\n'
printf 'BOOTSTRAP_INSTALL=PASS\n'

BOOTSTRAP_MUTATED=0
trap - ERR
