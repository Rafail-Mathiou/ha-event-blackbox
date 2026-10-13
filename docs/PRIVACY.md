# Privacy

> This is not legal advice. It describes technical behaviour and defaults. If you run
> the integration in a home shared with other people, you are responsible for informing
> them and getting their consent.

## Principles

- **Purpose:** data is only processed to explain why an entity changed state.
- **Minimisation:** only sources you select, only the fields that are needed, in memory
  by default.
- **Local:** everything happens inside your Home Assistant. No transfer, no telemetry,
  no cloud.
- **Defaults:** nothing is recorded after setup.

## What is processed

| Data | Where | Kept until |
|---|---|---|
| Settings: recording switch, selected entities and types, limits | config entry options | changed, or the integration is removed |
| Reduced events: entity, kind of change, transition without numbers, time | memory | dropped by the limits (default 30 minutes, 2,000 events, 2 MiB), on unload, or when recording is turned off |
| Home Assistant's context IDs of these events | memory | as above |
| Automation and script runs: entity and context IDs | memory | as above |
| Actions that target a selected entity: action name and selected targets | memory | as above |
| Optional history: the same reduced events with their time | `.storage/event_blackbox.history` | 1 to 7 days; deleted when history or recording is turned off, when the integration is removed, or with **Delete recorded data** |
| Counters: received, dropped, ignored, errors | memory, diagnostics | unload |

Automation and script runs are recorded for all automations and scripts, because they
link triggers to effects. Their entity IDs therefore stay in memory, and in the history
file if it is on, for the configured time.

## What is never processed

- Numbers in states, such as temperatures, energy, health or position values. They are
  recorded as `value`.
- The states of people, device trackers, cameras, locks, alarm panels and media
  players. Only the fact that they changed is recorded.
- Attributes of entities and the data of actions, which can contain codes, passwords or
  locations.
- The user who started an action.
- Automation traces.

## Storage and backups

Config entry options and Home Assistant's `.storage` are not encrypted and are part of
Home Assistant backups. Backups made while history was on keep that data until the
backups are deleted. No encryption is claimed.

## Who can see the data

Only administrators: the sidebar panel, its WebSocket commands and the action
`event_blackbox.explain` reject other users. Explanations show entity IDs,
transitions, times and evidence labels, never context or user IDs. The panel groups
entities with the names, areas and devices that Home Assistant already sends to the
administrator's browser and keeps nothing in the browser.

An administrator who calls the action from an automation decides where its response
goes.

## Logs and diagnostics

- Logs contain no event data: no entity IDs, context IDs, names, times, coordinates,
  URLs or tokens, also not in debug logging or exceptions.
- Diagnostics contain the integration and schema version, the recording state, the
  number of selected entities and types, the history setting and the buffer counters.
  Never names, records, context IDs or times.
