#!/usr/bin/env python3
"""Bonuska host-side Update Agent.

Security model:
- listens only on 127.0.0.1;
- browser access is expected to be protected by Nginx auth_request against
  Bonuska's existing System Administrator Command Center;
- accepts no shell command or arbitrary executable path;
- installs only Bonuska_Update_*.zip packages;
- requires SHA-256 plus a host/local HMAC-SHA256 signature;
- validates ZIP path safety and internal SHA256SUMS before execution;
- runs only the package's fixed install-server.sh / verify-server.sh /
  rollback-server.sh entrypoints.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import uuid
import zipfile


BIND = "127.0.0.1"
PORT = 8765
STATE_ROOT = Path("/var/lib/bonuska-update-agent")
JOBS_ROOT = STATE_ROOT / "jobs"
CONFIG_PATH = Path("/etc/bonuska-update-agent/config.json")
SIGNING_KEY_PATH = Path("/etc/bonuska-update-agent/signing.key")
ENABLED_PATH = Path("/etc/bonuska-update-agent/enabled")
MAX_REQUEST_BYTES = 80 * 1024 * 1024
MAX_ZIP_BYTES = 40 * 1024 * 1024
PACKAGE_RE = re.compile(r"^Bonuska_Update_v[0-9A-Za-z._-]+\.zip$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
JOB_LOCK = threading.Lock()
ACTIVE_JOB_ID: str | None = None


def _load_config() -> dict:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    public_ip = str(data.get("public_ip") or "").strip()
    app_root = str(data.get("app_root") or "/opt/Bonuska").strip()
    if not public_ip:
        raise RuntimeError("public_ip missing in agent config")
    return {
        "public_ip": public_ip,
        "app_root": app_root,
    }


def _load_key() -> bytes:
    raw = SIGNING_KEY_PATH.read_text(encoding="ascii").strip()
    key = base64.b64decode(raw, validate=True)
    if len(key) < 32:
        raise RuntimeError("signing key is too short")
    return key


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(temp, 0o600)
    temp.replace(path)


def _job_state_path(job_id: str) -> Path:
    return JOBS_ROOT / job_id / "state.json"


def _job_log_path(job_id: str) -> Path:
    return JOBS_ROOT / job_id / "job.log"


def _set_job(job_id: str, **changes) -> dict:
    path = _job_state_path(job_id)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"job_id": job_id}
    data.update(changes)
    data["updated_at_epoch"] = time.time()
    _atomic_json(path, data)
    _atomic_json(STATE_ROOT / "latest.json", data)
    return data


def _latest_state() -> dict:
    path = STATE_ROOT / "latest.json"
    if not path.exists():
        return {
            "status": "idle",
            "agent": "bonuska-update-agent",
            "enabled": ENABLED_PATH.exists(),
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    data["enabled"] = ENABLED_PATH.exists()
    return data


def _append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(message)
        if message and not message.endswith("\n"):
            fh.write("\n")


def _run_logged(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    timeout: int,
) -> int:
    _append_log(log_path, "$ " + " ".join(args))
    with log_path.open("a", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    return int(proc.returncode)


def _safe_extract(zip_path: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=False)

    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if not infos:
            raise RuntimeError("empty ZIP")

        roots: set[str] = set()

        for info in infos:
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or not p.parts:
                raise RuntimeError(f"unsafe ZIP member: {info.filename!r}")
            roots.add(p.parts[0])

            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise RuntimeError(f"symlink ZIP member is forbidden: {info.filename!r}")

        if len(roots) != 1:
            raise RuntimeError("release ZIP must contain exactly one top-level directory")

        zf.extractall(target)

    root_name = next(iter(roots))
    package_dir = target / root_name
    package_dir = package_dir.resolve()
    target_resolved = target.resolve()

    if target_resolved not in package_dir.parents:
        raise RuntimeError("extracted package escaped job directory")
    if not package_dir.is_dir():
        raise RuntimeError("top-level package directory missing")

    for name in (
        "install-server.sh",
        "verify-server.sh",
        "rollback-server.sh",
        "SHA256SUMS.txt",
    ):
        if not (package_dir / name).is_file():
            raise RuntimeError(f"required package file missing: {name}")

    return package_dir


def _verify_internal_checksums(package_dir: Path, log_path: Path) -> None:
    manifest = package_dir / "SHA256SUMS.txt"
    package_root = package_dir.resolve()

    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue

        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[0]):
            raise RuntimeError("invalid SHA256SUMS entry")

        rel_text = parts[1].lstrip("*").strip()
        rel = PurePosixPath(rel_text)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise RuntimeError(f"unsafe SHA256SUMS path: {rel_text!r}")

        resolved = (package_dir / Path(*rel.parts)).resolve()
        if resolved != package_root and package_root not in resolved.parents:
            raise RuntimeError(f"SHA256SUMS path escaped package: {rel_text!r}")

    result = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS.txt"],
        cwd=str(package_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        check=False,
    )
    _append_log(log_path, result.stdout)
    if result.returncode != 0:
        raise RuntimeError("internal SHA256SUMS verification failed")


def _worker(job_id: str, package_dir: Path) -> None:
    global ACTIVE_JOB_ID

    log_path = _job_log_path(job_id)
    cfg = _load_config()
    env = dict(os.environ)
    env["BONUSKA_PUBLIC_IP"] = cfg["public_ip"]

    try:
        _set_job(job_id, status="installing", phase="install")
        rc = _run_logged(
            ["bash", str(package_dir / "install-server.sh")],
            cwd=package_dir,
            env=env,
            log_path=log_path,
            timeout=2400,
        )
        if rc != 0:
            _set_job(
                job_id,
                status="failed",
                phase="install",
                message=f"install-server.sh exited with {rc}; package installer owns automatic rollback",
            )
            return

        _set_job(job_id, status="verifying", phase="verify")
        rc = _run_logged(
            ["bash", str(package_dir / "verify-server.sh")],
            cwd=package_dir,
            env=env,
            log_path=log_path,
            timeout=1800,
        )
        if rc == 0:
            _set_job(
                job_id,
                status="pass",
                phase="complete",
                message="install + verify completed successfully",
            )
            return

        _append_log(log_path, "VERIFY_FAILED: starting fixed package rollback entrypoint")
        _set_job(job_id, status="rolling_back", phase="rollback")
        rollback_rc = _run_logged(
            ["bash", str(package_dir / "rollback-server.sh")],
            cwd=package_dir,
            env=env,
            log_path=log_path,
            timeout=1800,
        )
        _set_job(
            job_id,
            status="rolled_back" if rollback_rc == 0 else "rollback_failed",
            phase="rollback",
            message=(
                f"verify exited with {rc}; rollback exit={rollback_rc}"
            ),
        )
    except subprocess.TimeoutExpired as exc:
        _append_log(log_path, f"TIMEOUT: {exc}")
        _set_job(job_id, status="failed", phase="timeout", message=str(exc))
    except Exception as exc:
        _append_log(log_path, f"ERROR: {type(exc).__name__}: {exc}")
        _set_job(job_id, status="failed", phase="agent", message=str(exc))
    finally:
        with JOB_LOCK:
            ACTIVE_JOB_ID = None


def _accept_package(payload: dict) -> dict:
    global ACTIVE_JOB_ID

    if not ENABLED_PATH.exists():
        raise RuntimeError("Update Agent is disabled")

    zip_name = str(payload.get("zip_name") or "").strip()
    zip_b64 = str(payload.get("zip_b64") or "").strip()
    sha_text = str(payload.get("sha256") or "").strip()
    sig_text = str(payload.get("signature") or "").strip().lower()

    if not PACKAGE_RE.fullmatch(zip_name):
        raise RuntimeError("package filename is not allowed")
    if not zip_b64:
        raise RuntimeError("ZIP payload is empty")

    expected_sha = sha_text.split()[0].lower() if sha_text else ""
    if not SHA_RE.fullmatch(expected_sha):
        raise RuntimeError("invalid SHA-256 file")
    if not SHA_RE.fullmatch(sig_text):
        raise RuntimeError("invalid HMAC-SHA256 signature")

    try:
        zip_bytes = base64.b64decode(zip_b64, validate=True)
    except Exception as exc:
        raise RuntimeError("invalid base64 ZIP payload") from exc

    if not zip_bytes or len(zip_bytes) > MAX_ZIP_BYTES:
        raise RuntimeError(
            f"ZIP size outside allowed range: {len(zip_bytes)} bytes"
        )

    actual_sha = hashlib.sha256(zip_bytes).hexdigest()
    if not hmac.compare_digest(actual_sha, expected_sha):
        raise RuntimeError(
            f"outer SHA-256 mismatch: actual={actual_sha} expected={expected_sha}"
        )

    expected_sig = hmac.new(_load_key(), zip_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig_text):
        raise RuntimeError("package HMAC signature verification failed")

    with JOB_LOCK:
        if ACTIVE_JOB_ID is not None:
            raise RuntimeError(f"another update job is active: {ACTIVE_JOB_ID}")

        job_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
        ACTIVE_JOB_ID = job_id

    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(job_dir, 0o700)

    zip_path = job_dir / zip_name
    zip_path.write_bytes(zip_bytes)
    os.chmod(zip_path, 0o600)

    log_path = _job_log_path(job_id)
    _append_log(log_path, f"PACKAGE={zip_name}")
    _append_log(log_path, f"SHA256={actual_sha}")
    _append_log(log_path, "OUTER_SHA256=PASS")
    _append_log(log_path, "HMAC_SIGNATURE=PASS")

    try:
        package_dir = _safe_extract(zip_path, job_dir / "extracted")
        _verify_internal_checksums(package_dir, log_path)
        _append_log(log_path, "INTERNAL_SHA256SUMS=PASS")
        _set_job(
            job_id,
            status="accepted",
            phase="preflight",
            package=zip_name,
            sha256=actual_sha,
            package_dir=str(package_dir),
        )
    except Exception:
        with JOB_LOCK:
            ACTIVE_JOB_ID = None
        raise

    thread = threading.Thread(
        target=_worker,
        args=(job_id, package_dir),
        daemon=True,
        name=f"bonuska-update-{job_id}",
    )
    thread.start()

    return _latest_state()


PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bonuska Update Center</title>
<style>
:root{font-family:Inter,Arial,sans-serif;color:#203040;background:#f4f7fa}
body{margin:0}.wrap{max-width:1000px;margin:0 auto;padding:24px}
header,.card{background:#fff;border:1px solid #dfe7ee;border-radius:16px;box-shadow:0 4px 18px rgba(33,53,73,.05)}
header{padding:20px 24px;margin-bottom:16px}.card{padding:20px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 6px}h2{font-size:16px;margin:0 0 12px}
p{margin:6px 0;color:#64788a}.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
label{display:grid;gap:6px;font-size:12px;font-weight:700}input[type=file]{padding:10px;border:1px solid #cad6e0;border-radius:10px;background:#fbfcfd}
button{border:0;border-radius:10px;padding:11px 16px;background:#0f4c81;color:#fff;font-weight:700;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}.status{padding:12px;border-radius:10px;background:#eef4f8;white-space:pre-wrap}
pre{max-height:420px;overflow:auto;background:#101923;color:#dce8f3;padding:14px;border-radius:12px;font-size:12px}
.good{color:#167447}.bad{color:#a51e32}.warn{color:#9b6800}
small{color:#708395}@media(max-width:760px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
<header>
<h1>Bonuska Update Center</h1>
<p>Host-side агент обновлений. Доступ к этой странице проверяется через сессию System Administrator Бонуски.</p>
</header>
<section class="card">
<h2>Установить подписанный пакет</h2>
<p>Нужны три файла: release ZIP, его <code>.sha256</code> и локальная HMAC-подпись <code>.sig</code>. Агент не принимает произвольные команды Linux.</p>
<div class="grid">
<label>ZIP<input id="zip" type="file" accept=".zip"></label>
<label>SHA-256<input id="sha" type="file" accept=".sha256,.txt"></label>
<label>HMAC signature<input id="sig" type="file" accept=".sig,.txt"></label>
</div>
<p><button id="install">Проверить и установить</button></p>
<small>После принятия пакета установка выполняется host-side. Перезапуск контейнера не прерывает сам Update Agent.</small>
</section>
<section class="card">
<h2>Состояние</h2>
<div id="status" class="status">Загрузка…</div>
</section>
<section class="card">
<h2>Журнал последней операции</h2>
<pre id="log">—</pre>
</section>
</div>
<script>
async function fileText(el){const f=el.files[0];if(!f)throw new Error("Файл не выбран");return (await f.text()).trim()}
async function fileBase64(el){
 const f=el.files[0];if(!f)throw new Error("ZIP не выбран");
 const buf=new Uint8Array(await f.arrayBuffer());let binary="";
 const chunk=0x8000;for(let i=0;i<buf.length;i+=chunk){binary+=String.fromCharCode(...buf.subarray(i,i+chunk))}
 return {name:f.name,b64:btoa(binary)}
}
async function refresh(){
 try{
  const s=await (await fetch("api/status",{cache:"no-store"})).json();
  const cls=s.status==="pass"?"good":(String(s.status).includes("fail")?"bad":"warn");
  document.getElementById("status").innerHTML='<span class="'+cls+'">'+JSON.stringify(s,null,2)+'</span>';
  if(s.job_id){
    const t=await (await fetch("api/jobs/"+encodeURIComponent(s.job_id)+"/log",{cache:"no-store"})).text();
    document.getElementById("log").textContent=t;
  }
 }catch(e){document.getElementById("status").textContent="Ошибка статуса: "+e}
}
document.getElementById("install").onclick=async()=>{
 const b=document.getElementById("install");b.disabled=true;
 try{
  const z=await fileBase64(document.getElementById("zip"));
  const sha=await fileText(document.getElementById("sha"));
  const sig=await fileText(document.getElementById("sig"));
  const r=await fetch("api/install",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({zip_name:z.name,zip_b64:z.b64,sha256:sha,signature:sig})});
  const data=await r.json();if(!r.ok)throw new Error(data.error||JSON.stringify(data));
  await refresh();
 }catch(e){alert(String(e))}finally{b.disabled=false}
};
refresh();setInterval(refresh,3000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "BonuskaUpdateAgent/1"

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, body_text: str, content_type: str) -> None:
        body = body_text.encode("utf-8", errors="replace")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path in ("/", ""):
            self._send_text(200, PAGE, "text/html; charset=utf-8")
            return

        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "agent": "bonuska-update-agent",
                    "enabled": ENABLED_PATH.exists(),
                },
            )
            return

        if path == "/api/status":
            self._send_json(200, _latest_state())
            return

        match = re.fullmatch(r"/api/jobs/([0-9A-Za-z_-]+)/log", path)
        if match:
            log_path = _job_log_path(match.group(1))
            if not log_path.exists():
                self._send_text(404, "log not found", "text/plain; charset=utf-8")
                return
            data = log_path.read_text(encoding="utf-8", errors="replace")
            self._send_text(200, data[-250_000:], "text/plain; charset=utf-8")
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/install":
            self._send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0

        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "request size outside allowed range"})
            return

        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            state = _accept_package(payload)
            self._send_json(202, state)
        except Exception as exc:
            self._send_json(
                400,
                {
                    "error": str(exc),
                    "type": type(exc).__name__,
                },
            )

    def log_message(self, fmt: str, *args) -> None:
        # Keep request metadata in journald without printing bodies or secrets.
        message = fmt % args
        print(f"{self.client_address[0]} {message}", flush=True)


def main() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_ROOT, 0o700)
    os.chmod(JOBS_ROOT, 0o700)

    _load_config()
    _load_key()

    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(
        f"Bonuska Update Agent listening on http://{BIND}:{PORT} "
        f"enabled={ENABLED_PATH.exists()}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
