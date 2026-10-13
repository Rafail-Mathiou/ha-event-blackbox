# Changelog

## 0.1.0

First release.

- Records the entities and types you choose, after you turn recording on, in a
  bounded in-memory buffer. Numbers and the states of sensitive entities are never
  stored.
- Explains why an entity changed: confirmed chains of causes through Home Assistant's
  context, possible causes that are only close in time, or an unknown cause, with
  plain-language notes. First states after a restart are recognised as such.
- Sidebar panel for administrators: a monitor with recording state, key figures, live
  activity and the evidence per entity, explanations as a timeline, and the choice of
  sources grouped by area or type.
- Optional history across restarts for 1 to 7 days, off by default, with a delete
  function.
- Action `event_blackbox.explain` for scripts and the developer tools.
- English, German and Greek.
