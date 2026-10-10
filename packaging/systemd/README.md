# NOVA user-service templates

These are source templates, not the active services. P0.2 must substitute
`@NOVA_ROOT@` with the installation root and `@PYTHON@` with the chosen Python
executable, install the three user units, reload the user daemon, and define
safe update/removal behavior.

The templates intentionally contain no credentials, model paths, or
installation-specific token values. The action executor references UWSM's
standard per-session environment file at runtime so fixed desktop launches have
the current Wayland/Hyprland context; no session values are rendered or stored
by NOVA. The templates preserve the current loopback-service working-directory
and restart behavior. Plugin installation alone must not install or start these
services.

P0.2A renders these templates only through `tools/nova_install.py`; it stages
the result, records ownership, and rejects untracked overwrites. The templates
are not installed by an Omarchy plugin hook.
