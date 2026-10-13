# Development

## Requirements

- [uv](https://docs.astral.sh/uv/). It provides Python 3.14, which Home Assistant
  2026.10 needs, and installs the pinned development tools from `uv.lock`.
- Node.js 24 with npm, for the checks of the panel code. The panel itself has no
  build step and no dependencies.
- Linux or macOS. Home Assistant does not run on Windows; use WSL there.
- A Home Assistant test instance for trying things out.

Files use LF line endings (see `.gitattributes`).

## Setup

```bash
uv sync
npm ci
```

## Checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest --cov
npm run check
```

`npm run check` runs ESLint, a TypeScript check of the JSDoc types and the unit tests
of the panel logic. CI runs the same checks, the tests also against the oldest
supported Home Assistant version, plus hassfest and the HACS validation.

## Tests

- `tests/test_ha_compatibility.py` records which Home Assistant events and context
  fields the integration relies on. If a Home Assistant update breaks one of them,
  adjust the integration, not the test.
- `tests/test_security.py` makes sure that made-up passwords, coordinates, tokens and
  health values never reach records, explanations, logs or diagnostics.
- `tests/test_panel.py` checks the WebSocket commands and the panel code: no
  `innerHTML`, no `eval`, no inline event handlers, no external URLs, and every icon
  exists in Home Assistant.
- The pure modules (`options.py`, `normalizer.py`, `storage.py`, `correlation.py`,
  `query.py`) are tested without a running Home Assistant. Coverage must stay at
  95 % or more.
- Use made-up data only, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Trying it out

Copy or link `custom_components/event_blackbox` into the `custom_components` folder
of a test instance and restart Home Assistant. After a change to the panel only,
reload the browser; Python changes need a restart.

## Panel

The panel lives in `custom_components/event_blackbox/frontend/`:

- `event-blackbox-panel.js`: the web component,
- `event-blackbox-model.js`: grouping logic without DOM access, tested in
  `tests/frontend/`,
- `event-blackbox-i18n.js`: texts in English, German and Greek. The type check makes
  sure every language has the same keys.

Keep it free of dependencies and render data only with `textContent`.

## Translations

The texts of the config flow, options flow, action and errors are in
`custom_components/event_blackbox/strings.json`. `translations/en.json` must be
identical to it, and every other language needs the same keys.

## Brand images

The icon and logos are in `custom_components/event_blackbox/brand/`, in the sizes Home
Assistant expects, with `@2x` and dark variants. `docs/images/` holds the logos used
in the README.
