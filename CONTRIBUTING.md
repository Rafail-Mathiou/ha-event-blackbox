# Contributing

Bug reports, ideas and pull requests are welcome. Privacy and safety come before
features.

## Issues

- Describe what you expected and what happened, with your Home Assistant version.
- Use made-up examples only, such as `light.demo_hall`. Do not attach logs,
  diagnostics, traces, backups or screenshots that show your home.
- Report security problems privately, see [SECURITY.md](SECURITY.md).

## Pull requests

1. Fork the repository and create a branch, for example `feat/...`, `fix/...` or
   `docs/...`.
2. Follow [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Add tests for every change, and
   run all checks before you push.
3. Keep each pull request to one topic. Use a prefix such as `feat:`, `fix:` or
   `docs:` in the title.

## Guidelines

- The integration only observes. It does not call actions, control devices, open
  network connections or run other programs.
- New data sources, interfaces or storage need tests that prove: nothing happens
  without opt-in, limits hold, unload cleans up, and no event data reaches logs or
  diagnostics. Update [docs/PRIVACY.md](docs/PRIVACY.md) and
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) in the same pull request.
- Logic that does not need Home Assistant lives in modules without `hass`, so it can
  be tested quickly with synthetic data.
- Everything runs async without blocking the event loop. Listeners, timers and tasks
  are registered with `entry.async_on_unload`.
- No runtime requirements.
- Code, comments and docs are in English.

## Test data

Only synthetic data: entities with a `demo_` prefix, IP addresses from
`192.0.2.0/24`, `198.51.100.0/24` and `203.0.113.0/24`, host names under `.invalid`.
No real names, places, devices, MAC addresses, serial numbers, tokens or traces, also
not in branch names, commit messages or pull requests.
