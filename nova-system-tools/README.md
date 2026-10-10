# NOVA read-only system tools

This is a loopback-only OpenAPI service for Open WebUI. It exposes fixed GET endpoints for CPU, RAM, NVIDIA GPU, disks, disk I/O, temperatures, process memory use, and current-boot warning/error logs.

It deliberately has no endpoint that accepts a command, path, process ID, query parameter, or request body. It cannot run a user-supplied shell command, modify a file, use `sudo`, kill a process, or listen outside `127.0.0.1`.

## Install after the home filesystem is writable

```bash
mkdir -p ~/nova/nova-system-tools ~/.config/systemd/user
cp server.py pyproject.toml ~/nova/nova-system-tools/
cp nova-system-tools.service ~/.config/systemd/user/
cd ~/nova/nova-system-tools && uv sync --no-install-package
systemctl --user daemon-reload
systemctl --user enable --now nova-system-tools.service
```

Verify it:

```bash
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/openapi.json
systemctl --user status nova-system-tools.service
```

## Add it to Open WebUI

In Open WebUI, add an external OpenAPI tool server pointing to:

```
http://127.0.0.1:8787/openapi.json
```

Enable the resulting tools only for the NOVA model/workspace. The endpoint is intentionally unauthenticated because it only listens on the local loopback interface. Do not change the binding to `0.0.0.0` or expose it through a reverse proxy without adding authentication.

## Security model

- Fixed endpoint allow-list; no arbitrary input is passed to commands.
- The only subprocesses are fixed, timeout-bound calls to `nvidia-smi` and `journalctl`.
- Process command lines are never returned.
- Logs are capped to 100 current-boot warning-or-worse lines and common secret assignments are redacted.
- The systemd unit blocks privilege escalation and gives the service a private temporary directory.
