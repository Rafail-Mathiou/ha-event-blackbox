// @ts-check
/**
 * Event Blackbox panel.
 *
 * A dependency-free web component for administrators, styled as a monitoring
 * console: "Monitor" shows the recording state, key figures, a live activity
 * tape and the recorded entities; a slide-in panel explains a change as a
 * timeline. "Sources" chooses what is recorded, grouped by area or by type.
 * Every value is rendered as text through the DOM API; nothing is built from
 * markup and nothing is loaded from other origins.
 */

import { TEXT } from "./event-blackbox-i18n.js";
import {
  buildCatalog,
  filterItems,
  groupItems,
  groupState,
  isSelected,
  sensitiveSelection,
  setGroup,
} from "./event-blackbox-model.js";

/**
 * @typedef {import("./event-blackbox-model.js").HassData & {
 *   callWS: (message: Record<string, unknown>) => Promise<any>,
 *   language?: string,
 *   locale?: { language?: string },
 * }} Hass
 * @typedef {import("./event-blackbox-model.js").Group} Group
 * @typedef {import("./event-blackbox-model.js").Item} Item
 * @typedef {typeof import("./event-blackbox-i18n.js").EN} Texts
 *
 * @typedef {{ time: string, kind: string, entity_id?: string, transition?: string,
 *   service?: string, targets?: string[], link?: string, evidence?: string }} Step
 * @typedef {{ entity_id: string, changes: number, last_time: string,
 *   last_transition: string, last_evidence: string, initial: boolean }} EntitySummary
 * @typedef {{ events: number, max_events: number, bytes: number, max_bytes: number,
 *   dropped: number }} BufferInfo
 * @typedef {{ recording: boolean, started_at: string | null, history_days: number,
 *   buffer: BufferInfo | null,
 *   entities: EntitySummary[], activity: Step[],
 *   evidence: Record<string, number>, initial: number }} Status
 * @typedef {{ event_id: number, time: string, transition: string, selected: boolean }} Change
 * @typedef {{ entity_id: string, status: string, evidence: string, summary: string,
 *   change: Step | null, chain: Step[], candidates: Step[], notes: string[],
 *   changes: Change[], recorded_changes: number }} BlackboxReport
 *
 * @typedef {{ collection_enabled: boolean, entities: string[], domains: string[],
 *   max_events: number, max_kib: number, max_age_minutes: number,
 *   history_days: number }} Options
 * @typedef {{ min: number, max: number, default: number }} Limit
 * @typedef {{ configured: boolean, options: Options | null,
 *   limits: Record<"max_events" | "max_kib" | "max_age_minutes" | "history_days", Limit>,
 *   max_entities: number, sensitive_domains: string[],
 *   selectable_domains: string[] }} Config
 * @typedef {{ collection_enabled: boolean, entities: Set<string>, domains: Set<string>,
 *   max_events: number, max_kib: number, max_age_minutes: number,
 *   history_days: number }} Draft
 */

const REFRESH_MS = 5000;
const EVIDENCE_ORDER = ["CONFIRMED_BY_CONTEXT", "INFERRED_TEMPORAL", "UNRESOLVED"];
/** @type {Record<string, string>} */
const KIND_ICON = {
  state_change: "mdi:swap-horizontal",
  automation_triggered: "mdi:robot",
  script_started: "mdi:script-text-outline",
  service_call: "mdi:gesture-tap",
};

const STYLE = `
:host {
  --eb-accent: #ff8a3d;
  --eb-accent-2: #ffb347;
  --eb-ok: #22c55e;
  --eb-warn: #f59e0b;
  --eb-idle: #94a3b8;
  --eb-danger: #ef4444;
  --eb-violet: #a855f7;
  --eb-teal: #14b8a6;
  --eb-blue: #3b82f6;
  --eb-text: var(--primary-text-color);
  --eb-dim: var(--secondary-text-color);
  --eb-line: color-mix(in srgb, var(--primary-text-color) 10%, transparent);
  --eb-surface: color-mix(in srgb, var(--card-background-color) 88%, transparent);
  --eb-raised: color-mix(in srgb, var(--primary-text-color) 4%, var(--card-background-color));
  --eb-radius: 16px;
  --eb-mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace;
  display: block;
  min-height: 100%;
  color: var(--eb-text);
  font-family: var(--ha-font-family-body, Roboto, system-ui, sans-serif);
  background:
    radial-gradient(1200px 500px at 85% -10%, color-mix(in srgb, var(--eb-accent) 14%, transparent), transparent 60%),
    radial-gradient(900px 400px at -10% 110%, color-mix(in srgb, var(--eb-blue) 8%, transparent), transparent 60%),
    var(--primary-background-color);
}
* { box-sizing: border-box; }
header {
  position: sticky; top: 0; z-index: 3;
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  padding: 12px 24px;
  background: color-mix(in srgb, var(--primary-background-color) 80%, transparent);
  backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--eb-line);
}
.brand { display: flex; align-items: center; gap: 12px; }
.logo {
  width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center;
  background: linear-gradient(135deg, var(--eb-accent), var(--eb-accent-2));
  color: #fff; box-shadow: 0 6px 18px color-mix(in srgb, var(--eb-accent) 45%, transparent);
}
.logo ha-icon { color: #fff; --mdc-icon-size: 22px; }
.brand-text h1 { font-size: 18px; font-weight: 600; margin: 0; letter-spacing: .01em; }
.brand-text div { font-size: 12px; color: var(--eb-dim); }
.pill {
  display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: 999px;
  font-size: 12px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  border: 1px solid var(--eb-line); background: var(--eb-raised);
}
.pill .led { width: 8px; height: 8px; border-radius: 50%; background: var(--eb-idle); }
.pill.live .led { background: var(--eb-ok); box-shadow: 0 0 0 0 color-mix(in srgb, var(--eb-ok) 60%, transparent); animation: pulse 2s infinite; }
.pill .since { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--eb-dim); }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--eb-ok) 55%, transparent); } 70% { box-shadow: 0 0 0 8px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
@media (prefers-reduced-motion: reduce) { .pill.live .led { animation: none; } .drawer { transition: none; } }
.spacer { flex: 1; }
.tabs { display: inline-flex; padding: 4px; border-radius: 12px; background: var(--eb-raised); border: 1px solid var(--eb-line); }
.tabs button {
  font: inherit; font-size: 14px; border: none; background: none; color: var(--eb-dim);
  padding: 7px 16px; border-radius: 9px; cursor: pointer;
}
.tabs .active { background: var(--card-background-color); color: var(--eb-text); box-shadow: 0 1px 4px rgba(0,0,0,.12); }
.icon-button {
  background: none; border: 1px solid var(--eb-line); color: var(--eb-text); cursor: pointer;
  width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center;
}
main { padding: 24px; max-width: 1320px; margin: 0 auto; }
.panel {
  background: var(--eb-surface); border: 1px solid var(--eb-line); border-radius: var(--eb-radius);
  box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.04);
}
.panel-head { display: flex; align-items: center; gap: 12px; padding: 16px 18px 8px; }
.panel-head h2 { font-size: 13px; font-weight: 600; margin: 0; text-transform: uppercase; letter-spacing: .08em; color: var(--eb-dim); }
.panel-body { padding: 4px 10px 12px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 16px; }
.kpi { padding: 18px; position: relative; overflow: hidden; }
.kpi .label { font-size: 12px; color: var(--eb-dim); text-transform: uppercase; letter-spacing: .08em; font-weight: 600; }
.kpi .value { font-size: 34px; font-weight: 600; margin: 8px 0 4px; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
.kpi .sub { font-size: 12px; color: var(--eb-dim); }
.kpi .glyph { position: absolute; right: 14px; top: 14px; color: var(--eb-dim); opacity: .5; }
.kpi.alert .value { color: var(--eb-warn); }
.gauge { height: 6px; border-radius: 3px; background: var(--eb-line); overflow: hidden; margin-top: 12px; }
.gauge span { display: block; height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--eb-accent), var(--eb-accent-2)); }
.stack { display: flex; height: 6px; border-radius: 3px; overflow: hidden; margin-top: 12px; background: var(--eb-line); gap: 2px; }
.stack span { display: block; height: 100%; }
.legend { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 10px; font-size: 12px; color: var(--eb-dim); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.ok { background: var(--eb-ok); } .warn { background: var(--eb-warn); } .idle { background: var(--eb-idle); }
.hollow { background: none; border: 2px solid var(--eb-idle); }
.grid { display: grid; grid-template-columns: minmax(300px, 5fr) minmax(320px, 7fr); gap: 16px; align-items: start; }
@media (max-width: 960px) { .grid { grid-template-columns: 1fr; } main { padding: 16px; } header { padding: 12px 16px; } }
.search {
  width: 100%; font: inherit; font-size: 14px; color: var(--eb-text); background: var(--eb-raised);
  border: 1px solid var(--eb-line); border-radius: 10px; padding: 9px 12px; outline: none;
}
.search:focus { border-color: var(--eb-accent); }
.entity {
  display: grid; grid-template-columns: auto auto 1fr auto; align-items: center; gap: 12px;
  width: 100%; padding: 10px 8px; border: none; background: none; color: inherit; font: inherit;
  text-align: left; border-radius: 10px; cursor: pointer;
}
.entity:hover, .tape-row:hover { background: var(--eb-raised); }
.entity.selected { background: color-mix(in srgb, var(--eb-accent) 12%, transparent); }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.entity .meta, .tape-row .meta { font-size: 12px; color: var(--eb-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.name { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cell { min-width: 0; }
.chip {
  display: inline-block; font-family: var(--eb-mono); font-size: 11px; padding: 3px 8px; border-radius: 6px;
  background: var(--eb-raised); border: 1px solid var(--eb-line); color: var(--eb-text); white-space: nowrap;
}
.tape { max-height: 620px; overflow-y: auto; }
.tape-row {
  display: grid; grid-template-columns: 96px 32px 1fr auto; gap: 12px; align-items: center;
  padding: 8px; border-radius: 10px; cursor: default;
}
.tape-row.clickable { cursor: pointer; }
.time { font-family: var(--eb-mono); font-size: 12px; color: var(--eb-dim); font-variant-numeric: tabular-nums; }
.bubble { width: 32px; height: 32px; border-radius: 10px; display: grid; place-items: center; }
.bubble ha-icon { --mdc-icon-size: 18px; }
.k-state_change { background: color-mix(in srgb, var(--eb-accent) 16%, transparent); color: var(--eb-accent); }
.k-automation_triggered { background: color-mix(in srgb, var(--eb-violet) 16%, transparent); color: var(--eb-violet); }
.k-script_started { background: color-mix(in srgb, var(--eb-teal) 16%, transparent); color: var(--eb-teal); }
.k-service_call { background: color-mix(in srgb, var(--eb-blue) 16%, transparent); color: var(--eb-blue); }
.bubble ha-icon { color: inherit; }
.empty { padding: 24px 12px; text-align: center; color: var(--eb-dim); font-size: 14px; }
.hero { padding: 36px; display: flex; gap: 24px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
.hero .glyph-lg {
  width: 72px; height: 72px; border-radius: 20px; display: grid; place-items: center;
  background: color-mix(in srgb, var(--eb-accent) 14%, transparent); color: var(--eb-accent);
}
.hero .glyph-lg ha-icon { --mdc-icon-size: 36px; color: var(--eb-accent); }
.hero h2 { margin: 0 0 6px; font-size: 22px; }
.hero p { margin: 0; color: var(--eb-dim); max-width: 560px; line-height: 1.5; }
.primary, .secondary {
  font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; border-radius: 10px; padding: 10px 18px;
  border: 1px solid transparent;
}
.primary { background: linear-gradient(135deg, var(--eb-accent), var(--eb-accent-2)); color: #fff; box-shadow: 0 6px 16px color-mix(in srgb, var(--eb-accent) 35%, transparent); }
.secondary { background: var(--eb-raised); color: var(--eb-text); border-color: var(--eb-line); }
.primary:disabled { opacity: .5; cursor: default; box-shadow: none; }
.backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 5; }
.drawer {
  position: fixed; top: 0; right: 0; bottom: 0; width: min(560px, 100vw); z-index: 6;
  background: var(--card-background-color); border-left: 1px solid var(--eb-line);
  box-shadow: -12px 0 40px rgba(0,0,0,.18); overflow-y: auto; padding: 20px 22px 28px;
}
.drawer-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.drawer-head .name { font-size: 18px; }
.badge {
  display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 4px 10px;
  font-size: 12px; font-weight: 600; white-space: nowrap;
}
.badge.confirmed { background: color-mix(in srgb, var(--eb-ok) 16%, transparent); color: var(--eb-ok); }
.badge.inferred { background: color-mix(in srgb, var(--eb-warn) 18%, transparent); color: var(--eb-warn); }
.badge.unresolved { background: color-mix(in srgb, var(--eb-idle) 20%, transparent); color: var(--eb-idle); }
.badge.sensitive { background: color-mix(in srgb, var(--eb-warn) 14%, transparent); color: var(--eb-warn); }
.summary {
  margin: 14px 0 6px; padding: 14px 16px; border-radius: 12px; background: var(--eb-raised);
  border-left: 3px solid var(--eb-accent); line-height: 1.55;
}
h3 { font-size: 12px; font-weight: 600; margin: 22px 0 10px; color: var(--eb-dim); text-transform: uppercase; letter-spacing: .08em; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip-button {
  font-family: var(--eb-mono); font-size: 12px; cursor: pointer; border-radius: 8px; padding: 5px 10px;
  border: 1px solid var(--eb-line); background: var(--eb-raised); color: var(--eb-text);
}
.chip-button.selected { border-color: var(--eb-accent); background: color-mix(in srgb, var(--eb-accent) 14%, transparent); }
ol.timeline { list-style: none; margin: 0; padding: 0; }
ol.timeline li { display: grid; grid-template-columns: 28px 1fr; gap: 12px; position: relative; padding-bottom: 12px; }
ol.timeline li::before { content: ""; position: absolute; left: 13px; top: 26px; bottom: -2px; width: 2px; background: var(--eb-line); }
ol.timeline li:last-child::before { display: none; }
.node { width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; border: 2px solid var(--eb-idle); background: var(--card-background-color); }
.node span { width: 10px; height: 10px; border-radius: 50%; background: var(--eb-idle); }
li.confirmed .node { border-color: var(--eb-ok); } li.confirmed .node span { background: var(--eb-ok); }
li.inferred .node { border-color: var(--eb-warn); } li.inferred .node span { background: var(--eb-warn); }
li.target .node { border-color: var(--eb-accent); } li.target .node span { background: var(--eb-accent); }
.step { padding: 10px 12px; border-radius: 12px; background: var(--eb-raised); border: 1px solid var(--eb-line); }
li.target .step { border-color: color-mix(in srgb, var(--eb-accent) 50%, transparent); }
.step-top { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
.step-link { font-size: 12px; color: var(--eb-dim); margin-top: 4px; }
ul.notes { margin: 0; padding-left: 18px; line-height: 1.55; color: var(--eb-dim); }
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 16px; }
.toolbar .search { flex: 1 1 280px; width: auto; }
.segmented { display: inline-flex; padding: 3px; border-radius: 10px; background: var(--eb-raised); border: 1px solid var(--eb-line); }
.segmented button { font: inherit; font-size: 13px; border: none; background: none; color: var(--eb-dim); padding: 6px 12px; border-radius: 7px; cursor: pointer; }
.segmented .active { background: var(--card-background-color); color: var(--eb-text); box-shadow: 0 1px 3px rgba(0,0,0,.12); }
.toggle { display: inline-flex; align-items: center; gap: 10px; cursor: pointer; font-size: 14px; user-select: none; }
.toggle input { appearance: none; width: 38px; height: 22px; border-radius: 11px; background: var(--eb-line); position: relative; cursor: pointer; transition: background .2s; margin: 0; flex: none; }
.toggle input::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: transform .2s; box-shadow: 0 1px 3px rgba(0,0,0,.3); }
.toggle input:checked { background: var(--eb-accent); }
.toggle input:checked::after { transform: translateX(16px); }
.toggle.big { font-size: 16px; font-weight: 600; }
input[type="checkbox"].box { width: 18px; height: 18px; accent-color: var(--eb-accent); margin: 0; flex: none; cursor: pointer; }
.record-card { padding: 20px 22px; margin-bottom: 16px; display: grid; grid-template-columns: 1fr auto; gap: 8px 24px; align-items: center; }
.record-card p { margin: 0; color: var(--eb-dim); font-size: 13px; line-height: 1.5; grid-column: 1 / -1; }
.counter { font-family: var(--eb-mono); font-size: 12px; color: var(--eb-dim); }
.counter.over { color: var(--eb-danger); }
.groups { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; align-items: start; }
.group { overflow: hidden; }
.group-head { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center; padding: 14px 16px; cursor: pointer; }
.tile-icon { width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center; background: color-mix(in srgb, var(--eb-accent) 13%, transparent); color: var(--eb-accent); }
.tile-icon ha-icon { color: var(--eb-accent); }
.group-sub { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.group-sub .gauge { flex: 1; margin: 0; height: 4px; }
.group-tools { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 0 16px 12px; font-size: 13px; color: var(--eb-dim); }
.group-body { border-top: 1px solid var(--eb-line); max-height: 360px; overflow-y: auto; padding: 6px; }
.row { display: grid; grid-template-columns: auto auto 1fr auto; gap: 10px; align-items: center; padding: 7px 8px; border-radius: 8px; cursor: pointer; }
.row:hover { background: var(--eb-raised); }
.row.disabled { cursor: default; opacity: .75; }
.row ha-icon { color: var(--eb-dim); --mdc-icon-size: 20px; }
details.panel { margin-top: 16px; padding: 14px 18px; }
details summary { cursor: pointer; font-weight: 600; font-size: 14px; }
.limits { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-top: 14px; }
.limits label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--eb-dim); }
.limits input { font: inherit; color: var(--eb-text); background: var(--eb-raised); border: 1px solid var(--eb-line); border-radius: 10px; padding: 8px 12px; }
.savebar {
  position: sticky; bottom: 16px; margin-top: 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 12px 16px; border-radius: 14px; background: var(--card-background-color);
  border: 1px solid color-mix(in srgb, var(--eb-accent) 50%, transparent); box-shadow: 0 10px 30px rgba(0,0,0,.18);
}
.savebar .grow { flex: 1; font-size: 14px; }
.error { color: var(--eb-danger); }
.notice { color: var(--eb-ok); }
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: grid; place-items: center; z-index: 10; padding: 16px; }
.dialog { background: var(--card-background-color); border-radius: 18px; padding: 26px; max-width: 520px; box-shadow: 0 20px 60px rgba(0,0,0,.35); border: 1px solid var(--eb-line); }
.dialog h2 { margin-top: 0; }
.dialog p { line-height: 1.55; color: var(--eb-dim); }
.dialog .actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 22px; }
ha-icon { flex: none; }
`;

/**
 * Create an element. Text always goes through textContent, so data can never
 * become markup.
 * @param {string} tag
 * @param {{ text?: string, className?: string, attrs?: Record<string, string> }} [options]
 * @param {Node[]} [children]
 * @returns {HTMLElement}
 */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attrs ?? {})) {
    node.setAttribute(name, value);
  }
  for (const child of children) node.appendChild(child);
  return node;
}

/** @param {string} name */
function icon(name) {
  return el("ha-icon", { attrs: { icon: name } });
}

/** @param {string | undefined} evidence */
function evidenceClass(evidence) {
  if (evidence === "CONFIRMED_BY_CONTEXT") return "confirmed";
  if (evidence === "INFERRED_TEMPORAL") return "inferred";
  return "unresolved";
}

/** @param {string | undefined} evidence */
function evidenceSwatch(evidence) {
  if (evidence === "CONFIRMED_BY_CONTEXT") return "ok";
  if (evidence === "INFERRED_TEMPORAL") return "warn";
  return "idle";
}

/**
 * Fill {placeholders} of a text.
 * @param {string} text
 * @param {Record<string, string | number>} values
 */
function format(text, values) {
  return text.replace(/\{(\w+)\}/g, (_match, key) => String(values[key] ?? ""));
}

/**
 * @param {number} part
 * @param {number} whole
 */
function percent(part, whole) {
  return whole > 0 ? Math.min(100, Math.round((part / whole) * 100)) : 0;
}

/**
 * A horizontal gauge filled to a percentage.
 * @param {number} value 0 to 100
 */
function gauge(value) {
  const fill = el("span");
  fill.style.width = `${value}%`;
  return el("div", { className: "gauge" }, [fill]);
}

/**
 * @param {Options} options
 * @returns {Draft}
 */
function toDraft(options) {
  return {
    collection_enabled: options.collection_enabled,
    entities: new Set(options.entities),
    domains: new Set(options.domains),
    max_events: options.max_events,
    max_kib: options.max_kib,
    max_age_minutes: options.max_age_minutes,
    history_days: options.history_days ?? 0,
  };
}

/**
 * @param {Draft} draft
 * @returns {Options}
 */
function fromDraft(draft) {
  return {
    collection_enabled: draft.collection_enabled,
    entities: [...draft.entities].sort(),
    domains: [...draft.domains].sort(),
    max_events: draft.max_events,
    max_kib: draft.max_kib,
    max_age_minutes: draft.max_age_minutes,
    history_days: draft.history_days,
  };
}

class EventBlackboxPanel extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    /** @type {Hass | null} */
    this._hass = null;
    /** @type {"monitor" | "sources"} */
    this._tab = "monitor";
    /** @type {Status | null} */
    this._status = null;
    /** @type {BlackboxReport | null} */
    this._report = null;
    /** @type {string | null} */
    this._entity = null;
    this._entityQuery = "";
    /** @type {Config | null} */
    this._config = null;
    /** @type {Draft | null} */
    this._draft = null;
    /** @type {"area" | "type"} */
    this._groupBy = "type";
    this._query = "";
    this._showTechnical = false;
    /** @type {Set<string>} */
    this._expanded = new Set();
    /** @type {string | null} */
    this._error = null;
    /** @type {string | null} */
    this._saveMessage = null;
    this._saveOk = false;
    this._saving = false;
    /** @type {string[] | null} */
    this._dialog = null;
    /** @type {ReturnType<typeof setInterval> | null} */
    this._timer = null;
    /** @type {Record<string, HTMLElement | null>} */
    this._slots = {};
  }

  /** @param {Hass} hass */
  set hass(hass) {
    const first = this._hass === null;
    this._hass = hass;
    if (first) {
      this._load();
      this._startTimer();
    }
  }

  /** @param {boolean} _value */
  set narrow(_value) {}

  /** @param {unknown} _value */
  set panel(_value) {}

  connectedCallback() {
    if (this._hass) this._startTimer();
  }

  disconnectedCallback() {
    if (this._timer !== null) clearInterval(this._timer);
    this._timer = null;
  }

  _startTimer() {
    if (this._timer !== null) return;
    this._timer = setInterval(() => {
      if (document.visibilityState === "visible" && this._tab === "monitor") {
        this._refreshStatus();
      }
    }, REFRESH_MS);
  }

  /** @returns {Texts} */
  get _t() {
    const language = (this._hass?.locale?.language ?? this._hass?.language ?? "en").split("-")[0];
    return language === "de" || language === "el" ? TEXT[language] : TEXT.en;
  }

  get _language() {
    return this._hass?.locale?.language ?? this._hass?.language ?? "en";
  }

  /** @param {string} key */
  _label(key) {
    const texts = /** @type {Record<string, string>} */ (this._t);
    return texts[key] ?? key;
  }

  /** @param {string} entityId */
  _name(entityId) {
    return this._hass?.states[entityId]?.attributes?.friendly_name ?? entityId;
  }

  /** @param {string} entityId */
  _icon(entityId) {
    return this._hass?.states[entityId]?.attributes?.icon ?? KIND_ICON.state_change;
  }

  /** @param {string} iso */
  _time(iso) {
    return new Intl.DateTimeFormat(this._language, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      fractionalSecondDigits: 3,
    }).format(new Date(iso));
  }

  /** @param {string} iso */
  _clock(iso) {
    return new Intl.DateTimeFormat(this._language, { hour: "2-digit", minute: "2-digit" }).format(
      new Date(iso),
    );
  }

  /** @param {string} iso */
  _ago(iso) {
    const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
    const format = new Intl.RelativeTimeFormat(this._language, { numeric: "auto" });
    if (Math.abs(seconds) < 60) return format.format(seconds, "second");
    if (Math.abs(seconds) < 3600) return format.format(Math.round(seconds / 60), "minute");
    return format.format(Math.round(seconds / 3600), "hour");
  }

  get _sensitiveDomains() {
    return new Set(this._config?.sensitive_domains ?? []);
  }

  get _dirty() {
    if (!this._draft || !this._config?.options) return false;
    return JSON.stringify(fromDraft(this._draft)) !== JSON.stringify(this._config.options);
  }

  // --- data -----------------------------------------------------------------

  async _load() {
    if (!this._hass) return;
    this._error = null;
    this._render();
    try {
      const [status, config] = await Promise.all([
        this._hass.callWS({ type: "event_blackbox/status" }),
        this._hass.callWS({ type: "event_blackbox/get_config" }),
      ]);
      this._status = status;
      const keepDraft = this._dirty;
      this._config = config;
      if (!keepDraft) this._draft = config.options ? toDraft(config.options) : null;
    } catch {
      this._error = this._t.error;
    }
    this._render();
  }

  async _refreshStatus() {
    if (!this._hass) return;
    try {
      this._status = await this._hass.callWS({ type: "event_blackbox/status" });
    } catch {
      return;
    }
    this._renderHeaderStatus();
    this._renderKpis();
    this._renderEntityList();
    this._renderTape();
  }

  /**
   * @param {string} entityId
   * @param {number} [eventId]
   */
  async _explain(entityId, eventId) {
    if (!this._hass) return;
    this._entity = entityId;
    /** @type {Record<string, unknown>} */
    const message = { type: "event_blackbox/explain", entity_id: entityId };
    if (eventId !== undefined) message.event_id = eventId;
    try {
      this._report = await this._hass.callWS(message);
    } catch {
      this._report = null;
    }
    this._renderEntityList();
    this._renderDrawer();
  }

  _closeDrawer() {
    this._entity = null;
    this._report = null;
    this._renderEntityList();
    this._renderDrawer();
  }

  /** @param {"monitor" | "sources"} tab */
  _setTab(tab) {
    this._tab = tab;
    if (tab === "monitor") this._refreshStatus();
    this._render();
  }

  // --- layout ---------------------------------------------------------------

  _render() {
    const t = this._t;
    this._slots = {};
    const logo = el("div", { className: "logo" }, [icon("mdi:timeline-question-outline")]);
    const brand = el("div", { className: "brand" }, [
      logo,
      el("div", { className: "brand-text" }, [
        el("h1", { text: t.title }),
        el("div", { text: t.subtitle }),
      ]),
    ]);
    const status = el("div");
    this._slots.status = status;
    const tabs = el("nav", { className: "tabs" });
    /** @type {Array<["monitor" | "sources", string]>} */
    const tabList = [
      ["monitor", t.tabMonitor],
      ["sources", t.tabSources],
    ];
    for (const [id, text] of tabList) {
      const tab = el("button", { className: this._tab === id ? "active" : "", text });
      tab.addEventListener("click", () => this._setTab(id));
      tabs.appendChild(tab);
    }
    const refresh = el("button", { className: "icon-button", attrs: { title: t.refresh } }, [
      icon("mdi:refresh"),
    ]);
    refresh.addEventListener("click", () => this._load());
    const header = el("header", {}, [brand, status, el("div", { className: "spacer" }), tabs, refresh]);

    const main = el("main");
    if (this._config && !this._config.configured) {
      main.appendChild(this._hero("mdi:puzzle-outline", t.title, t.notConfigured));
    } else if (this._tab === "sources") {
      this._renderSources(main);
    } else {
      this._renderMonitor(main);
    }
    const drawer = el("div");
    this._slots.drawer = drawer;
    const nodes = [el("style", { text: STYLE }), header, main, drawer];
    if (this._dialog) nodes.push(this._renderDialog(this._dialog));
    this._root.replaceChildren(...nodes);
    this._renderHeaderStatus();
    this._renderDrawer();
  }

  _renderHeaderStatus() {
    const slot = this._slots.status;
    if (!slot) return;
    const t = this._t;
    const live = Boolean(this._status?.recording);
    const pill = el("div", { className: `pill${live ? " live" : ""}` }, [
      el("span", { className: "led" }),
      el("span", { text: live ? t.statusRecording : t.statusPaused }),
    ]);
    if (live && this._status?.started_at) {
      pill.appendChild(
        el("span", { className: "since", text: format(t.since, { time: this._clock(this._status.started_at) }) }),
      );
    }
    if (live && this._status?.history_days) {
      pill.appendChild(
        el("span", { className: "since", text: `· ${format(t.historyBadge, { days: this._status.history_days })}` }),
      );
    }
    slot.replaceChildren(pill);
  }

  /**
   * @param {string} glyph
   * @param {string} title
   * @param {string} text
   * @param {HTMLElement} [action]
   */
  _hero(glyph, title, text, action) {
    const body = el("div", { className: "cell" }, [el("h2", { text: title }), el("p", { text })]);
    const hero = el("section", { className: "panel hero" }, [
      el("div", { className: "glyph-lg" }, [icon(glyph)]),
      body,
    ]);
    if (action) hero.appendChild(action);
    return hero;
  }

  // --- monitor --------------------------------------------------------------

  /** @param {HTMLElement} main */
  _renderMonitor(main) {
    const t = this._t;
    if (!this._status) {
      main.appendChild(el("div", { className: "empty", text: this._error ?? t.loading }));
      return;
    }
    if (!this._status.recording) {
      const open = el("button", { className: "primary", text: t.openSources });
      open.addEventListener("click", () => this._setTab("sources"));
      main.appendChild(this._hero("mdi:pause-circle-outline", t.pausedTitle, t.pausedText, open));
    }
    const kpis = el("div", { className: "kpis" });
    this._slots.kpis = kpis;
    main.appendChild(kpis);

    const search = /** @type {HTMLInputElement} */ (
      el("input", { className: "search", attrs: { type: "search", placeholder: t.search } })
    );
    search.value = this._entityQuery;
    search.addEventListener("input", () => {
      this._entityQuery = search.value;
      this._renderEntityList();
    });
    const entityList = el("div");
    this._slots.entities = entityList;
    const entitiesPanel = el("section", { className: "panel" }, [
      el("div", { className: "panel-head" }, [el("h2", { text: t.entities })]),
      el("div", { className: "panel-body" }, [search, entityList]),
    ]);

    const tape = el("div", { className: "tape" });
    this._slots.tape = tape;
    const livePill = el("span", { className: `pill${this._status.recording ? " live" : ""}` }, [
      el("span", { className: "led" }),
      el("span", { text: "Live" }),
    ]);
    const tapePanel = el("section", { className: "panel" }, [
      el("div", { className: "panel-head" }, [
        el("h2", { text: t.activity }),
        el("div", { className: "spacer" }),
        livePill,
      ]),
      el("div", { className: "panel-body" }, [tape]),
    ]);
    main.appendChild(el("div", { className: "grid" }, [entitiesPanel, tapePanel]));
    this._renderKpis();
    this._renderEntityList();
    this._renderTape();
  }

  _renderKpis() {
    const slot = this._slots.kpis;
    const status = this._status;
    if (!slot || !status) return;
    const t = this._t;
    const buffer = status.buffer ?? { events: 0, max_events: 0, bytes: 0, max_bytes: 0, dropped: 0 };
    const kib = Math.round(buffer.bytes / 1024);
    const maxKib = Math.round(buffer.max_bytes / 1024);
    const usage = Math.max(percent(buffer.events, buffer.max_events), percent(buffer.bytes, buffer.max_bytes));

    const changes = status.entities.reduce((sum, entity) => sum + entity.changes, 0);
    const total = EVIDENCE_ORDER.reduce((sum, key) => sum + (status.evidence[key] ?? 0), 0);
    const confirmed = status.evidence.CONFIRMED_BY_CONTEXT ?? 0;
    const stack = el("div", { className: "stack" });
    const legend = el("div", { className: "legend" });
    for (const key of EVIDENCE_ORDER) {
      const count = status.evidence[key] ?? 0;
      const segment = el("span", { className: evidenceSwatch(key) });
      segment.style.flex = String(count);
      if (count > 0) stack.appendChild(segment);
      legend.appendChild(
        el("span", {}, [
          el("span", { className: `swatch ${evidenceSwatch(key)}` }),
          el("span", { text: `${this._label(key)} ${count}` }),
        ]),
      );
    }

    /**
     * @param {string} glyph
     * @param {string} label
     * @param {string} value
     * @param {string} sub
     * @param {Node[]} [extra]
     * @param {boolean} [alert]
     */
    const tile = (glyph, label, value, sub, extra = [], alert = false) =>
      el("section", { className: `panel kpi${alert ? " alert" : ""}` }, [
        el("span", { className: "glyph" }, [icon(glyph)]),
        el("div", { className: "label", text: label }),
        el("div", { className: "value", text: value }),
        el("div", { className: "sub", text: sub }),
        ...extra,
      ]);

    slot.replaceChildren(
      tile(
        "mdi:database-outline",
        t.kpiEvents,
        String(buffer.events),
        format(t.kpiEventsSub, { max: buffer.max_events, kib, maxKib }),
        [gauge(usage)],
      ),
      tile(
        "mdi:eye-outline",
        t.kpiEntities,
        String(status.entities.length),
        changes === 1 ? t.kpiEntitiesSubOne : format(t.kpiEntitiesSub, { changes }),
      ),
      tile(
        "mdi:link-variant",
        t.kpiEvidence,
        total > 0 ? `${percent(confirmed, total)} %` : "–",
        status.initial > 0
          ? `${t.kpiEvidenceSub} · ${(status.initial === 1 ? t.firstStatesOne : format(t.firstStates, { count: status.initial }))}`
          : t.kpiEvidenceSub,
        [stack, legend],
      ),
      tile(
        "mdi:delete-sweep-outline",
        t.kpiDropped,
        String(buffer.dropped),
        t.kpiDroppedSub,
        [],
        buffer.dropped > 0,
      ),
    );
  }

  _renderEntityList() {
    const slot = this._slots.entities;
    const status = this._status;
    if (!slot || !status) return;
    const t = this._t;
    if (status.entities.length === 0) {
      slot.replaceChildren(el("div", { className: "empty", text: t.noEntities }));
      return;
    }
    const words = this._entityQuery.toLowerCase().split(/\s+/).filter(Boolean);
    const rows = status.entities
      .filter((entity) => {
        const haystack = `${this._name(entity.entity_id)} ${entity.entity_id}`.toLowerCase();
        return words.every((word) => haystack.includes(word));
      })
      .map((entity) => {
        const row = el(
          "button",
          { className: `entity${entity.entity_id === this._entity ? " selected" : ""}` },
          [
            el("span", {
              className: `dot ${entity.initial ? "hollow" : evidenceSwatch(entity.last_evidence)}`,
            }),
            icon(this._icon(entity.entity_id)),
            el("span", { className: "cell" }, [
              el("div", { className: "name", text: this._name(entity.entity_id) }),
              el("div", {
                className: "meta",
                text: `${this._ago(entity.last_time)} · ${entity.changes} ${entity.changes === 1 ? t.changeCountOne : t.changeCount}`,
              }),
            ]),
            el("span", { className: "chip", text: entity.last_transition }),
          ],
        );
        const label = entity.initial ? t.firstState : this._label(entity.last_evidence);
        row.setAttribute("title", `${entity.entity_id} · ${label}`);
        row.addEventListener("click", () => this._explain(entity.entity_id));
        return row;
      });
    slot.replaceChildren(...rows);
  }

  _renderTape() {
    const slot = this._slots.tape;
    const status = this._status;
    if (!slot || !status) return;
    const t = this._t;
    if (status.activity.length === 0) {
      slot.replaceChildren(el("div", { className: "empty", text: t.noActivity }));
      return;
    }
    slot.replaceChildren(
      ...status.activity.map((step) => {
        const name = step.entity_id ? this._name(step.entity_id) : (step.service ?? "");
        const meta =
          step.kind === "service_call" && step.targets?.length
            ? step.targets.map((target) => this._name(target)).join(", ")
            : this._label(step.kind);
        const entityId = step.kind === "state_change" ? step.entity_id : undefined;
        const row = el("div", { className: `tape-row${entityId ? " clickable" : ""}` }, [
          el("span", { className: "time", text: this._time(step.time) }),
          el("span", { className: `bubble k-${step.kind}` }, [
            icon(KIND_ICON[step.kind] ?? KIND_ICON.state_change),
          ]),
          el("span", { className: "cell" }, [
            el("div", { className: "name", text: name }),
            el("div", { className: "meta", text: meta }),
          ]),
          el("span", step.transition ? { className: "chip", text: step.transition } : {}),
        ]);
        if (entityId) {
          row.setAttribute("title", entityId);
          row.addEventListener("click", () => this._explain(entityId));
        }
        return row;
      }),
    );
  }

  // --- explanation drawer ---------------------------------------------------

  _renderDrawer() {
    const slot = this._slots.drawer;
    if (!slot) return;
    const report = this._report;
    if (!report || !this._entity) {
      slot.replaceChildren();
      return;
    }
    const t = this._t;
    const close = el("button", { className: "icon-button", attrs: { title: t.close } }, [
      icon("mdi:close"),
    ]);
    close.addEventListener("click", () => this._closeDrawer());
    const backdrop = el("div", { className: "backdrop" });
    backdrop.addEventListener("click", () => this._closeDrawer());

    const drawer = el("aside", { className: "drawer", attrs: { role: "dialog" } }, [
      el("div", { className: "drawer-head" }, [
        el("div", { className: "tile-icon" }, [icon(this._icon(report.entity_id))]),
        el("div", { className: "cell" }, [
          el("div", { className: "name", text: this._name(report.entity_id) }),
          el("div", { className: "meta muted", text: report.entity_id }),
        ]),
        el("div", { className: "spacer" }),
        close,
      ]),
      el("span", {
        className: `badge ${evidenceClass(report.evidence)}`,
        text: this._label(report.evidence),
      }),
      el("div", { className: "summary", text: report.summary }),
    ]);

    if (report.changes.length > 0) {
      drawer.appendChild(el("h3", { text: t.changes }));
      const chips = el("div", { className: "chips" });
      for (const change of report.changes) {
        const chip = el("button", {
          className: `chip-button${change.selected ? " selected" : ""}`,
          text: `${this._time(change.time)} · ${change.transition}`,
        });
        chip.addEventListener("click", () => this._explain(report.entity_id, change.event_id));
        chips.appendChild(chip);
      }
      drawer.appendChild(chips);
    }
    if (report.change) {
      drawer.appendChild(el("h3", { text: report.chain.length ? t.chain : t.thisChange }));
      const timeline = el("ol", { className: "timeline" });
      for (const step of report.chain) timeline.appendChild(this._renderStep(step));
      timeline.appendChild(this._renderStep(report.change, true));
      drawer.appendChild(timeline);
    }
    if (report.candidates.length > 0) {
      drawer.appendChild(el("h3", { text: t.candidates }));
      const list = el("ol", { className: "timeline" });
      for (const step of report.candidates) list.appendChild(this._renderStep(step));
      drawer.appendChild(list);
    } else if (report.change && report.chain.length === 0) {
      drawer.appendChild(el("p", { className: "meta", text: t.noCause }));
    }
    if (report.notes.length > 0) {
      drawer.appendChild(el("h3", { text: t.notes }));
      drawer.appendChild(
        el("ul", { className: "notes" }, report.notes.map((note) => el("li", { text: this._label(note) }))),
      );
    }
    slot.replaceChildren(backdrop, drawer);
  }

  /**
   * @param {Step} step
   * @param {boolean} [isTarget]
   */
  _renderStep(step, isTarget = false) {
    const name = step.entity_id ? this._name(step.entity_id) : (step.service ?? "");
    const what = step.transition ?? this._label(step.kind);
    const card = el("div", { className: "step" }, [
      el("div", { className: "step-top" }, [
        el("span", { className: "name", text: name }),
        el("span", { className: "time", text: this._time(step.time) }),
      ]),
      el("div", { className: "group-sub" }, [el("span", { className: "chip", text: what })]),
    ]);
    const meta = [];
    if (step.link) meta.push(this._label(step.link));
    if (step.service && step.targets?.length) {
      meta.push(step.targets.map((target) => this._name(target)).join(", "));
    }
    if (meta.length) card.appendChild(el("div", { className: "step-link", text: meta.join(" · ") }));
    const item = el("li", { className: isTarget ? "target" : evidenceClass(step.evidence) }, [
      el("div", { className: "node" }, [el("span")]),
      card,
    ]);
    if (step.entity_id) item.setAttribute("title", step.entity_id);
    return item;
  }

  // --- sources --------------------------------------------------------------

  /** @param {HTMLElement} main */
  _renderSources(main) {
    const t = this._t;
    const draft = this._draft;
    const config = this._config;
    if (!draft || !config) {
      main.appendChild(el("div", { className: "empty", text: this._error ?? t.loading }));
      return;
    }

    const toggle = /** @type {HTMLInputElement} */ (el("input", { attrs: { type: "checkbox" } }));
    toggle.checked = draft.collection_enabled;
    toggle.addEventListener("change", () => {
      draft.collection_enabled = toggle.checked;
      this._saveMessage = null;
      this._update();
    });
    const summary = el("div", { className: "counter" });
    this._slots.summary = summary;
    const clear = el("button", { className: "secondary", text: t.clearData });
    clear.addEventListener("click", () => this._clear());
    main.appendChild(
      el("section", { className: "panel record-card" }, [
        el("label", { className: "toggle big" }, [toggle, el("span", { text: t.recordToggle })]),
        summary,
        el("p", { text: t.recordingHint }),
        el("div", {}, [clear]),
      ]),
    );

    const search = /** @type {HTMLInputElement} */ (
      el("input", { className: "search", attrs: { type: "search", placeholder: t.search } })
    );
    search.value = this._query;
    search.addEventListener("input", () => {
      this._query = search.value;
      this._renderGroups();
    });
    const segmented = el("div", { className: "segmented" });
    /** @type {Array<["type" | "area", string]>} */
    const groupings = [
      ["type", t.byType],
      ["area", t.byArea],
    ];
    for (const [id, text] of groupings) {
      const button = el("button", { className: this._groupBy === id ? "active" : "", text });
      button.addEventListener("click", () => {
        this._groupBy = id;
        this._expanded.clear();
        this._render();
      });
      segmented.appendChild(button);
    }
    const technical = /** @type {HTMLInputElement} */ (el("input", { attrs: { type: "checkbox" } }));
    technical.checked = this._showTechnical;
    technical.addEventListener("change", () => {
      this._showTechnical = technical.checked;
      this._renderGroups();
    });
    main.appendChild(
      el("div", { className: "toolbar" }, [
        search,
        segmented,
        el("label", { className: "toggle" }, [technical, el("span", { text: t.showTechnical })]),
      ]),
    );

    const groups = el("div", { className: "groups" });
    this._slots.groups = groups;
    main.appendChild(groups);
    main.appendChild(this._renderLimits(draft, config));
    const saveBar = el("div");
    this._slots.saveBar = saveBar;
    main.appendChild(saveBar);
    this._update();
  }

  /** Re-render the parts that depend on the draft without losing focus. */
  _update() {
    this._renderSummary();
    this._renderGroups();
    this._renderSaveBar();
  }

  _renderSummary() {
    const slot = this._slots.summary;
    if (!slot || !this._draft || !this._config) return;
    const over = this._draft.entities.size > this._config.max_entities;
    slot.className = over ? "counter over" : "counter";
    slot.textContent = format(this._t.selectedSummary, {
      entities: this._draft.entities.size,
      max: this._config.max_entities,
      domains: this._draft.domains.size,
    });
  }

  _renderGroups() {
    const container = this._slots.groups;
    const draft = this._draft;
    if (!container || !draft || !this._hass) return;
    const catalog = buildCatalog(this._hass, this._sensitiveDomains);
    const visible = filterItems(catalog, this._query, this._showTechnical, draft.entities);
    const groups = groupItems(visible, this._groupBy, (key) => this._label(key));
    if (groups.length === 0) {
      container.replaceChildren(el("div", { className: "empty", text: this._t.nothingFound }));
      return;
    }
    container.replaceChildren(...groups.map((group) => this._renderGroup(group, draft)));
  }

  /**
   * @param {Group} group
   * @param {Draft} draft
   */
  _renderGroup(group, draft) {
    const t = this._t;
    const expanded = this._expanded.has(group.id) || this._query.trim() !== "";
    const state = groupState(group, draft.entities, draft.domains);
    const selectedCount = group.items.filter((item) =>
      isSelected(item, draft.entities, draft.domains),
    ).length;

    const all = /** @type {HTMLInputElement} */ (
      el("input", {
        className: "box",
        attrs: { type: "checkbox", title: t.selectAll, "aria-label": t.selectAll },
      })
    );
    all.checked = state === "all";
    all.indeterminate = state === "some";
    all.addEventListener("click", (event) => event.stopPropagation());
    all.addEventListener("change", () => {
      setGroup(group, draft.entities, all.checked);
      this._saveMessage = null;
      this._update();
    });

    const head = el("div", { className: "group-head", attrs: { "aria-expanded": String(expanded) } }, [
      el("div", { className: "tile-icon" }, [icon(group.icon)]),
      el("div", { className: "cell" }, [
        el("div", { className: "name", text: group.title }),
        el("div", { className: "group-sub" }, [
          gauge(percent(selectedCount, group.items.length)),
          el("span", { className: "counter", text: `${selectedCount}/${group.items.length}` }),
        ]),
      ]),
      all,
    ]);
    head.addEventListener("click", () => {
      if (this._expanded.has(group.id)) this._expanded.delete(group.id);
      else this._expanded.add(group.id);
      this._renderGroups();
    });

    const section = el("section", { className: "panel group" }, [head]);
    const domain = group.domain;
    if (domain && this._config?.selectable_domains.includes(domain)) {
      const whole = /** @type {HTMLInputElement} */ (el("input", { attrs: { type: "checkbox" } }));
      whole.checked = draft.domains.has(domain);
      whole.addEventListener("change", () => {
        if (whole.checked) draft.domains.add(domain);
        else draft.domains.delete(domain);
        this._saveMessage = null;
        this._update();
      });
      section.appendChild(
        el("div", { className: "group-tools" }, [
          el("label", { className: "toggle" }, [whole, el("span", { text: t.wholeDomain })]),
        ]),
      );
    }
    if (expanded) {
      section.appendChild(
        el("div", { className: "group-body" }, group.items.map((item) => this._renderRow(item, draft))),
      );
    }
    return section;
  }

  /**
   * @param {Item} item
   * @param {Draft} draft
   */
  _renderRow(item, draft) {
    const t = this._t;
    const viaDomain = draft.domains.has(item.domain);
    const check = /** @type {HTMLInputElement} */ (
      el("input", { className: "box", attrs: { type: "checkbox" } })
    );
    check.checked = viaDomain || draft.entities.has(item.entityId);
    check.disabled = viaDomain;
    check.addEventListener("change", () => {
      if (check.checked) draft.entities.add(item.entityId);
      else draft.entities.delete(item.entityId);
      this._saveMessage = null;
      this._update();
    });
    const where = this._groupBy === "area" ? this._label(item.typeId) : (item.areaName ?? "");
    const secondary = viaDomain ? t.viaDomain : [item.entityId, where].filter(Boolean).join(" · ");
    const row = el("label", { className: `row${viaDomain ? " disabled" : ""}` }, [
      check,
      icon(item.icon),
      el("span", { className: "cell" }, [
        el("div", { className: "name", text: item.name }),
        el("div", { className: "meta", text: secondary }),
      ]),
    ]);
    row.appendChild(
      item.sensitive ? el("span", { className: "badge sensitive", text: t.sensitiveBadge }) : el("span"),
    );
    return row;
  }

  /**
   * @param {Draft} draft
   * @param {Config} config
   */
  _renderLimits(draft, config) {
    const t = this._t;
    const details = el("details", { className: "panel" }, [el("summary", { text: t.advanced })]);
    const grid = el("div", { className: "limits" });
    /** @type {Array<["max_events" | "max_kib" | "max_age_minutes" | "history_days", string]>} */
    const fields = [
      ["max_events", t.maxEvents],
      ["max_kib", t.maxKib],
      ["max_age_minutes", t.maxAge],
      ["history_days", t.historyDays],
    ];
    for (const [key, text] of fields) {
      const limit = config.limits[key];
      const input = /** @type {HTMLInputElement} */ (
        el("input", {
          attrs: { type: "number", min: String(limit.min), max: String(limit.max), step: "1" },
        })
      );
      input.value = String(draft[key]);
      input.addEventListener("change", () => {
        const value = Number.parseInt(input.value, 10);
        draft[key] = Number.isFinite(value) ? value : limit.default;
        this._saveMessage = null;
        this._renderSaveBar();
      });
      grid.appendChild(el("label", {}, [el("span", { text }), input]));
    }
    details.appendChild(grid);
    details.appendChild(el("p", { className: "meta", text: t.historyHint }));
    return details;
  }

  _renderSaveBar() {
    const bar = this._slots.saveBar;
    if (!bar) return;
    const t = this._t;
    if (!this._dirty && !this._saveMessage) {
      bar.replaceChildren();
      bar.className = "";
      return;
    }
    bar.className = "savebar";
    let tone = "";
    if (this._saveMessage) tone = this._saveOk ? " notice" : " error";
    const children = [el("span", { className: `grow${tone}`, text: this._saveMessage ?? t.unsaved })];
    if (this._dirty) {
      const discard = el("button", { className: "secondary", text: t.discard });
      discard.addEventListener("click", () => {
        if (this._config?.options) this._draft = toDraft(this._config.options);
        this._saveMessage = null;
        this._render();
      });
      const save = /** @type {HTMLButtonElement} */ (el("button", { className: "primary", text: t.save }));
      save.disabled = this._saving;
      save.addEventListener("click", () => this._save(false));
      children.push(discard, save);
    }
    bar.replaceChildren(...children);
  }

  /** @param {boolean} acknowledge */
  async _save(acknowledge) {
    if (!this._hass || !this._draft) return;
    const sensitive = sensitiveSelection(this._draft.entities, this._draft.domains, this._sensitiveDomains);
    if (sensitive.length > 0 && !acknowledge) {
      this._dialog = sensitive;
      this._render();
      return;
    }
    this._saving = true;
    this._dialog = null;
    this._saveMessage = null;
    this._render();
    try {
      const result = await this._hass.callWS({
        type: "event_blackbox/set_config",
        options: fromDraft(this._draft),
        acknowledge_sensitive: acknowledge,
      });
      if (result.saved) {
        this._saving = false;
        this._draft = null;
        await this._load();
        this._saveOk = true;
        this._saveMessage = this._t.saved;
        this._renderSaveBar();
        return;
      }
      const errors = /** @type {Record<string, string>} */ (result.errors ?? {});
      this._saveOk = false;
      this._saveMessage = Object.values(errors)
        .map((code) => this._label(code))
        .join(" ");
    } catch {
      this._saveOk = false;
      this._saveMessage = this._t.error;
    }
    this._saving = false;
    this._render();
  }

  async _clear() {
    if (!this._hass) return;
    try {
      await this._hass.callWS({ type: "event_blackbox/clear" });
      this._saveOk = true;
      this._saveMessage = this._t.cleared;
    } catch {
      this._saveOk = false;
      this._saveMessage = this._t.error;
    }
    this._entity = null;
    this._report = null;
    await this._load();
    this._renderSaveBar();
  }

  /** @param {string[]} domains */
  _renderDialog(domains) {
    const t = this._t;
    const confirm = /** @type {HTMLInputElement} */ (
      el("input", { className: "box", attrs: { type: "checkbox" } })
    );
    const proceed = /** @type {HTMLButtonElement} */ (el("button", { className: "primary", text: t.save }));
    proceed.disabled = true;
    confirm.addEventListener("change", () => {
      proceed.disabled = !confirm.checked;
    });
    proceed.addEventListener("click", () => this._save(true));
    const cancel = el("button", { className: "secondary", text: t.cancel });
    cancel.addEventListener("click", () => {
      this._dialog = null;
      this._render();
    });
    return el("div", { className: "overlay" }, [
      el("div", { className: "dialog", attrs: { role: "dialog", "aria-modal": "true" } }, [
        el("h2", { text: t.dialogTitle }),
        el("p", { text: format(t.dialogText, { domains: domains.join(", ") }) }),
        el("label", { className: "toggle" }, [confirm, el("span", { text: t.dialogConfirm })]),
        el("div", { className: "actions" }, [cancel, proceed]),
      ]),
    ]);
  }
}

if (!customElements.get("event-blackbox-panel")) {
  customElements.define("event-blackbox-panel", EventBlackboxPanel);
}
