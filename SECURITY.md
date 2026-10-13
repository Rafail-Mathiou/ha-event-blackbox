# Security

## Reporting a vulnerability

Please report security problems privately through GitHub: open the repository's
**Security** tab and choose **Report a vulnerability**. Do not open a public issue.

Do not attach real data such as logs, diagnostics, backups, tokens, locations or
configurations. Describe the problem with made-up examples.

## Scope

Event Blackbox runs inside Home Assistant with its full rights. Relevant are in
particular:

- actions against the documented limits, such as action calls or device control,
- data leaving the integration through logs, diagnostics, the panel, the action or
  the network,
- bypassing the recording switch, the allowlist, the confirmation for sensitive
  types or the administrator check,
- exhausting Home Assistant's resources, such as blocking the event loop or filling
  memory or disk.

## What the integration does

- It stores its settings in the config entry and, only if the optional history is on,
  the reduced events in `.storage/event_blackbox.history` for at most 7 days.
- After you turn recording on, it listens to the selected state changes, to automation
  and script runs, and to actions that target selected entities, and keeps a reduced
  copy in memory.
- It adds a sidebar panel, five WebSocket commands and one action, all for
  administrators only. Only `event_blackbox/set_config` and `event_blackbox/clear`
  change anything, and only the integration's own settings and data.
- It serves the panel code from `/event_blackbox_static/`. That path contains code
  only.
- It never calls actions, controls devices, opens network connections or runs other
  programs. Its diagnostics contain counters only.
