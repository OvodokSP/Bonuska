#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${BONUSKA_APP_ROOT:-/opt/Bonuska}"
PUBLIC_IP="${BONUSKA_PUBLIC_IP:-31.77.56.66}"
REGRESSION_PDF="${BONUSKA_VR016877_PDF:-/tmp/bonuska-regression-vr016877.pdf}"
OLD_REGRESSION_PDF="${BONUSKA_LP109691_PDF:-/tmp/bonuska-regression-lp109691.pdf}"
PARSER="${APP_ROOT}/backend/bonuska/parsers/pdf_invoice.py"

log() {
  printf '[v0.17.31-a03 verify] %s\n' "$*"
}

die() {
  printf '[v0.17.31-a03 verify] ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || die "run as root"
[[ -f "${PARSER}" ]] || die "parser missing"
[[ -f "${REGRESSION_PDF}" ]] || die "regression PDF missing: ${REGRESSION_PDF}"

grep -q 'BONUSKA_V01731_A03_HEADERLESS_CONTINUATION_BEGIN' "${PARSER}" || die "parser marker missing"
grep -q 'BONUSKA_V01731_A03_HEADERLESS_CONTINUATION_END' "${PARSER}" || die "parser end marker missing"
python3 -m py_compile "${PARSER}"
printf 'PARSER_SOURCE=PASS\n'

cd "${APP_ROOT}"
FINGERPRINT="$(python3 backend/scripts/code_fingerprint.py --root "${APP_ROOT}" --fingerprint-only)"
printf 'FINGERPRINT=%s\n' "${FINGERPRINT}"

systemctl is-active --quiet bonuska-update-agent.service || die "Update Agent service is not active"
systemctl is-enabled --quiet bonuska-update-agent.service || die "Update Agent service is not enabled"
printf 'UPDATE_AGENT_SYSTEMD=PASS\n'

curl -fsS --max-time 5 http://127.0.0.1:8765/health | python3 -m json.tool
printf 'UPDATE_AGENT_LOCAL_HEALTH=PASS\n'

[[ -f /etc/bonuska-update-agent/signing.key ]] || die "server signing key missing"
[[ -f /etc/bonuska-update-agent/enabled ]] || die "Update Agent enable marker missing"
KEY_MODE="$(stat -c '%a' /etc/bonuska-update-agent/signing.key)"
[[ "${KEY_MODE}" == "600" ]] || die "signing key mode is ${KEY_MODE}, expected 600"
printf 'UPDATE_AGENT_SIGNING_KEY=PASS\n'

grep -q '/etc/nginx/snippets/bonuska-update-agent.conf' /etc/nginx/sites-available/bonuska || die "Nginx include missing"
nginx -t
printf 'NGINX_UPDATE_AGENT_BRIDGE=PASS\n'

curl -fsS --max-time 10 http://127.0.0.1:8000/health/ready | python3 -m json.tool
printf 'BONUSKA_READINESS=PASS\n'

docker exec -i bonuska python - <<'PY'
import sqlite3
from bonuska.database.schema import SCHEMA_VERSION
import main

print(f"APP_VERSION={main.APP_VERSION}")
print(f"PATCH_LEVEL={main.PATCH_LEVEL}")
print(f"SCHEMA_VERSION={SCHEMA_VERSION}")
print(f"ROUTES={len(main.app.routes)}")

assert main.APP_VERSION == "0.17.31"
assert int(SCHEMA_VERSION) == 29
assert len(main.app.routes) == 138
PY

DB_PATH="${APP_ROOT}/data/bonuska.db"
[[ -f "${DB_PATH}" ]] || die "SQLite database missing: ${DB_PATH}"
INTEGRITY="$(python3 - "${DB_PATH}" <<'PY'
import sqlite3
import sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(db.execute("PRAGMA integrity_check").fetchone()[0])
db.close()
PY
)"
[[ "${INTEGRITY}" == "ok" ]] || die "SQLite integrity_check=${INTEGRITY}"
printf 'SQLITE_INTEGRITY=ok\n'

FK_ERRORS="$(python3 - "${DB_PATH}" <<'PY'
import sqlite3
import sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(len(db.execute("PRAGMA foreign_key_check").fetchall()))
db.close()
PY
)"
[[ "${FK_ERRORS}" == "0" ]] || die "foreign_key_errors=${FK_ERRORS}"
printf 'FOREIGN_KEY_ERRORS=0\n'

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
assert rows == set(range(1, 483))
assert total == Decimal("93677502.64"), total
print("VR016877_ITEMS=482")
print("VR016877_TOTAL=93677502.64")
print("VR016877_REGRESSION=PASS")
PY

if [[ -f "${OLD_REGRESSION_PDF}" ]]; then
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
fi

HTTP_CODE="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 "https://${PUBLIC_IP}/system/update-agent/" || true)"
printf 'UPDATE_AGENT_ANON_HTTP=%s\n' "${HTTP_CODE}"
[[ "${HTTP_CODE}" != "200" ]] || die "anonymous browser request unexpectedly received 200"
printf 'UPDATE_AGENT_AUTH_GATE=PASS\n'

printf 'UPDATE_AGENT_URL=https://%s/system/update-agent/\n' "${PUBLIC_IP}"
printf 'BOOTSTRAP_VERIFY=PASS\n'
