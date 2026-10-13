// @ts-check
/**
 * Pure helpers for the Event Blackbox panel: turn the data Home
 * Assistant already gives the browser into a catalog of entities, group it by
 * area or by type, filter it and compute selection states. No DOM, no network.
 */

/**
 * @typedef {{
 *   entities?: Record<string, { area_id?: string | null, device_id?: string | null,
 *     hidden?: boolean, entity_category?: string | null, icon?: string | null,
 *     name?: string | null } | undefined>,
 *   devices?: Record<string, { area_id?: string | null } | undefined>,
 *   areas?: Record<string, { name: string, icon?: string | null } | undefined>,
 *   states: Record<string, { attributes?: { friendly_name?: string,
 *     device_class?: string, icon?: string } } | undefined>,
 * }} HassData
 *
 * @typedef {{
 *   entityId: string,
 *   name: string,
 *   domain: string,
 *   typeId: string,
 *   areaId: string | null,
 *   areaName: string | null,
 *   icon: string,
 *   sensitive: boolean,
 *   technical: boolean,
 * }} Item
 *
 * @typedef {{
 *   id: string,
 *   title: string,
 *   icon: string,
 *   items: Item[],
 *   domain: string | null,
 * }} Group
 */

/**
 * Type groups in display order. A group with exactly one domain can record the
 * whole domain, including entities added later.
 * @type {ReadonlyArray<{ id: string, icon: string, domains: string[],
 *   deviceClasses?: string[], wholeDomain?: string }>}
 */
export const TYPE_GROUPS = [
  { id: "lights", icon: "mdi:lightbulb-group", domains: ["light"], wholeDomain: "light" },
  { id: "switches", icon: "mdi:toggle-switch-variant", domains: ["switch"], wholeDomain: "switch" },
  {
    id: "motion",
    icon: "mdi:motion-sensor",
    domains: ["binary_sensor"],
    deviceClasses: ["motion", "occupancy", "presence", "moving"],
  },
  {
    id: "openings",
    icon: "mdi:door-open",
    domains: ["binary_sensor"],
    deviceClasses: ["door", "window", "opening", "garage_door"],
  },
  { id: "binary", icon: "mdi:radiobox-marked", domains: ["binary_sensor"] },
  { id: "climate", icon: "mdi:thermostat", domains: ["climate", "water_heater", "humidifier"] },
  { id: "covers", icon: "mdi:window-shutter", domains: ["cover", "valve"] },
  { id: "fans", icon: "mdi:fan", domains: ["fan"], wholeDomain: "fan" },
  { id: "vacuums", icon: "mdi:robot-vacuum", domains: ["vacuum"], wholeDomain: "vacuum" },
  {
    id: "automations",
    icon: "mdi:robot",
    domains: ["automation"],
    wholeDomain: "automation",
  },
  { id: "scripts", icon: "mdi:script-text-outline", domains: ["script", "scene"] },
  {
    id: "helpers",
    icon: "mdi:tune-variant",
    domains: [
      "input_boolean",
      "input_button",
      "input_number",
      "input_select",
      "button",
      "select",
      "number",
      "event",
      "siren",
    ],
  },
  { id: "sensors", icon: "mdi:gauge", domains: ["sensor"], wholeDomain: "sensor" },
  { id: "other", icon: "mdi:shape-outline", domains: [] },
];

export const SENSITIVE_GROUP = { id: "sensitive", icon: "mdi:shield-alert-outline" };
export const NO_AREA = "__no_area__";

/**
 * Pick the type group of an entity.
 * @param {string} domain
 * @param {string | undefined} deviceClass
 * @param {ReadonlySet<string>} sensitiveDomains
 * @returns {string}
 */
export function classify(domain, deviceClass, sensitiveDomains) {
  if (sensitiveDomains.has(domain)) return SENSITIVE_GROUP.id;
  for (const group of TYPE_GROUPS) {
    if (!group.domains.includes(domain)) continue;
    if (group.deviceClasses && !group.deviceClasses.includes(deviceClass ?? "")) continue;
    return group.id;
  }
  return "other";
}

/** @param {string} groupId */
function groupIcon(groupId) {
  if (groupId === SENSITIVE_GROUP.id) return SENSITIVE_GROUP.icon;
  return TYPE_GROUPS.find((group) => group.id === groupId)?.icon ?? "mdi:shape-outline";
}

/**
 * Build the catalog of entities the user can choose from.
 * @param {HassData} hass
 * @param {ReadonlySet<string>} sensitiveDomains
 * @returns {Item[]}
 */
export function buildCatalog(hass, sensitiveDomains) {
  /** @type {Item[]} */
  const items = [];
  for (const [entityId, state] of Object.entries(hass.states)) {
    const domain = entityId.split(".", 1)[0];
    const entry = hass.entities?.[entityId];
    const areaId =
      entry?.area_id ?? (entry?.device_id ? hass.devices?.[entry.device_id]?.area_id : null) ?? null;
    const typeId = classify(domain, state?.attributes?.device_class, sensitiveDomains);
    items.push({
      entityId,
      name: state?.attributes?.friendly_name ?? entry?.name ?? entityId,
      domain,
      typeId,
      areaId,
      areaName: areaId ? (hass.areas?.[areaId]?.name ?? null) : null,
      icon: state?.attributes?.icon ?? entry?.icon ?? groupIcon(typeId),
      sensitive: sensitiveDomains.has(domain),
      technical: Boolean(entry?.hidden) || Boolean(entry?.entity_category),
    });
  }
  return items.sort((a, b) => a.name.localeCompare(b.name) || a.entityId.localeCompare(b.entityId));
}

/**
 * Keep items that match a search text and the technical filter.
 * @param {Item[]} items
 * @param {string} query
 * @param {boolean} showTechnical
 * @param {ReadonlySet<string>} [alwaysShow] selected entities stay visible
 * @returns {Item[]}
 */
export function filterItems(items, query, showTechnical, alwaysShow = new Set()) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    if (item.technical && !showTechnical && !alwaysShow.has(item.entityId)) return false;
    const haystack = `${item.name} ${item.entityId} ${item.areaName ?? ""}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

/**
 * Group items by area or by type, in a stable order.
 * @param {Item[]} items
 * @param {"area" | "type"} by
 * @param {(key: string) => string} title translation for group IDs
 * @returns {Group[]}
 */
export function groupItems(items, by, title) {
  /** @type {Map<string, Item[]>} */
  const buckets = new Map();
  for (const item of items) {
    const key = by === "area" ? (item.areaId ?? NO_AREA) : item.typeId;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(item);
    else buckets.set(key, [item]);
  }
  /** @type {Group[]} */
  const groups = [];
  if (by === "type") {
    const order = [...TYPE_GROUPS.map((group) => group.id), SENSITIVE_GROUP.id];
    for (const id of order) {
      const groupItemsList = buckets.get(id);
      if (!groupItemsList) continue;
      const definition = TYPE_GROUPS.find((group) => group.id === id);
      groups.push({
        id,
        title: title(id),
        icon: groupIcon(id),
        items: groupItemsList,
        domain: definition?.wholeDomain ?? null,
      });
    }
    return groups;
  }
  for (const [id, groupItemsList] of buckets) {
    groups.push({
      id,
      title: id === NO_AREA ? title(NO_AREA) : (groupItemsList[0].areaName ?? id),
      icon: id === NO_AREA ? "mdi:help-circle-outline" : "mdi:texture-box",
      items: groupItemsList,
      domain: null,
    });
  }
  return groups.sort((a, b) => {
    if (a.id === NO_AREA) return 1;
    if (b.id === NO_AREA) return -1;
    return a.title.localeCompare(b.title);
  });
}

/**
 * Whether an item is recorded, directly or through its whole domain.
 * @param {Item} item
 * @param {ReadonlySet<string>} entities
 * @param {ReadonlySet<string>} domains
 */
export function isSelected(item, entities, domains) {
  return entities.has(item.entityId) || domains.has(item.domain);
}

/**
 * Selection state of a group for its checkbox.
 * @param {Group} group
 * @param {ReadonlySet<string>} entities
 * @param {ReadonlySet<string>} domains
 * @returns {"all" | "some" | "none"}
 */
export function groupState(group, entities, domains) {
  const selected = group.items.filter((item) => isSelected(item, entities, domains)).length;
  if (selected === 0) return "none";
  return selected === group.items.length ? "all" : "some";
}

/**
 * Select or clear all items of a group (only direct entity selections change).
 * @param {Group} group
 * @param {Set<string>} entities modified in place
 * @param {boolean} select
 */
export function setGroup(group, entities, select) {
  for (const item of group.items) {
    if (select) entities.add(item.entityId);
    else entities.delete(item.entityId);
  }
}

/**
 * Sensitive domains touched by a selection, sorted.
 * @param {ReadonlySet<string>} entities
 * @param {ReadonlySet<string>} domains
 * @param {ReadonlySet<string>} sensitiveDomains
 * @returns {string[]}
 */
export function sensitiveSelection(entities, domains, sensitiveDomains) {
  const touched = new Set([...domains]);
  for (const entityId of entities) touched.add(entityId.split(".", 1)[0]);
  return [...touched].filter((domain) => sensitiveDomains.has(domain)).sort();
}
