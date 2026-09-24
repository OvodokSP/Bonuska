# Bonuska v0.17.31 a03 transition bootstrap

This directory is a **transition kit**, not a replacement for the normal
Bonuska release-builder.

It is intentionally designed for one exact starting point:

- Bonuska application version: `0.17.31`
- SQLite schema: `29`
- routes before bootstrap: `138`
- exact source fingerprint:

```text
43139dcdfd543197961369c2d3163a38ae763a4be8b642d186779b4bd8d36b41
```

The bootstrap has two independent goals:

1. Fix the PDF continuation-page defect demonstrated by `ВР016877.pdf`.
2. Install a host-side signed Update Agent so future normal
   `Bonuska_Update_*.zip` packages can be applied through the web interface
   without arbitrary browser shell access.

## Why this is a bootstrap kit

The current v0.17.31 a02 web Command Center can diagnose the host and validate
an update package, but the Docker application must not directly receive root
access to `/opt/Bonuska`, Docker Compose, Nginx, or host rollback operations.

The agent therefore runs as a separate host systemd service. The browser-facing
path is:

```text
https://<Bonuska host>/system/update-agent/
```

Nginx protects that path with `auth_request` against the existing
`/system/command-center` page. In other words, the existing Bonuska System
Administrator session remains the browser access boundary.

The agent itself listens only on `127.0.0.1:8765`.

## Package authenticity

SHA-256 alone is not an authorization mechanism because an attacker who can
upload a ZIP can also generate a SHA-256 file for that ZIP.

The host agent additionally requires an HMAC-SHA256 signature generated with a
48-byte secret. The secret is:

- stored on the host at `/etc/bonuska-update-agent/signing.key`, mode 0600;
- exported once for the owner at
  `/root/Bonuska_Update_Signing_Key.txt`;
- copied to the owner's workstation;
- never mounted into the Bonuska container;
- never exposed by the Update Agent web UI.

A stolen browser System Administrator session can therefore not manufacture a
new authorized root-level update package.

## Parser corrective safety rule

The existing structured PDF parser remains primary.

The appended fallback is accepted only when all conditions hold:

- it finds a strict sequential item series `1..N`;
- the primary parser's rows are a subset of that series;
- the fallback adds rows rather than deleting/replacing primary rows;
- every missing row has the expected service term and at least price + row
  amount;
- the reconstructed row total exactly matches the PDF `Итого, руб` value to
  one kopeck.

This prevents the previous false-row defect where text such as
`94 дБ ... 0,75 Вт` could be interpreted as item 94.

For the supplied `ВР016877.pdf`, the verified target is:

```text
items = 482
rows  = 1..482
total = 93 677 502.64
rows 332..357 = present
```

The installer requires that PDF at:

```text
/tmp/bonuska-regression-vr016877.pdf
```

and runs the regression against the rebuilt production image before reporting
success.

If `/tmp/bonuska-regression-lp109691.pdf` is also present, it additionally
requires:

```text
items = 32
total = 7 989 783.44
```

## Bootstrap installation

Copy both regression PDFs to the VPS first. `ВР016877.pdf` is mandatory;
`ЛП109691.pdf` is recommended.

Then run the pinned bootstrap checkout as root.

The installer performs:

1. exact source fingerprint check;
2. current readiness check;
3. free-disk check;
4. parser + Nginx rollback backup;
5. parser append and Python compile;
6. Update Agent/key/systemd installation;
7. authenticated Nginx bridge installation and `nginx -t`;
8. Bonuska Docker rebuild;
9. readiness;
10. exact `ВР016877` parser regression;
11. optional `ЛП109691` regression;
12. anonymous-access security check;
13. final source fingerprint output.

Any error after the first write invokes `rollback-bootstrap.sh`.

## After bootstrap

Copy the signing key to a secure local directory, for example:

```powershell
New-Item -ItemType Directory -Force C:\Projects\Bonuska\secrets | Out-Null
scp root@<HOST>:/root/Bonuska_Update_Signing_Key.txt C:\Projects\Bonuska\secrets\bonuska-update-signing-key.txt
```

After confirming the local copy, the one-time export file under `/root` can be
deleted. Do **not** delete `/etc/bonuska-update-agent/signing.key`.

For every future normal Bonuska release package:

```powershell
.\sign-update.ps1 -Zip C:\Projects\Bonuska\incoming\Bonuska_Update_vX.Y.Z_aNN.zip
```

This creates/validates:

- `Bonuska_Update_....zip.sha256`
- `Bonuska_Update_....zip.sig`

Then login to Bonuska as System Administrator and open:

```text
/system/update-agent/
```

Upload ZIP + SHA + SIG. The agent performs the fixed flow:

```text
outer SHA
-> HMAC signature
-> ZIP path/symlink safety
-> internal SHA256SUMS
-> package install-server.sh
-> package verify-server.sh
-> PASS

verify failure
-> package rollback-server.sh
-> rollback result
```

The agent does not accept a shell command, arbitrary executable path, or
arbitrary script name.

## Corporate environment

Do not install this personal-VPS bootstrap automatically in the corporate
environment.

The corporate default remains: web self-update disabled until explicitly
approved by corporate IT/security. The corporate migration/update packages keep
their independent controlled deployment workflow.

## Files

- `install-bootstrap.sh` — one-time personal VPS bootstrap.
- `verify-bootstrap.sh` — post-install verification.
- `rollback-bootstrap.sh` — returns parser/Nginx to exact pre-bootstrap copy
  and removes the agent.
- `pdf_invoice_v01731_a03_append.py` — narrow parser continuation safeguard.
- `bonuska-update-agent.py` — localhost host-side signed package executor.
- `bonuska-update-agent.service` — systemd unit.
- `bonuska-update-agent.nginx` — Nginx locations protected by Bonuska
  System Administrator auth.
- `sign-update.ps1` — local HMAC package signing helper.
- `sync-local.ps1` — applies the same parser corrective to the canonical
  Windows source after server verification.

## Important operational rule

Do not start normal work on a newly installed package until its Update Agent
status is `pass` and the package's own `verify-server.sh` has completed.
