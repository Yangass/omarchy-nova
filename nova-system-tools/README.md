# NOVA system tools

NOVA's read-only diagnostics service: CPU, memory, GPU, disks, disk I/O,
temperatures, process memory use and current-boot warning/error logs, for the
MACHINE view and for the answers NOVA's chat gives about this computer.

It is installed and started by NOVA (`tools/nova_install.py`, as the systemd user
service `nova-system-tools.service`); it is not meant to be set up on its own.

## Security model

- Listens only on `127.0.0.1:8787` and serves only the desktop user: each connection
  is checked against the kernel's socket table, every request must be addressed to
  `127.0.0.1`/`localhost` on this port, and every endpoint except `/health` needs the
  per-user key from `~/.config/nova/api.token` (see `docs/HOW_IT_WORKS.md`).
- No CORS headers: web pages cannot read its answers.
- Fixed GET endpoints only; no endpoint accepts a command, path, process ID, query
  parameter or request body. It cannot run commands, change files, use `sudo` or kill
  processes.
- The only subprocesses are fixed, timeout-bound calls to `nvidia-smi` and `journalctl`.
- Process command lines are never returned; logs are capped to 100 current-boot
  warning-or-worse lines and common secret assignments are redacted.
- The systemd unit blocks privilege escalation and gives the service a private
  temporary directory.
