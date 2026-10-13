// Unit tests for the pure panel helpers (node --test). Synthetic data only.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NO_AREA,
  buildCatalog,
  classify,
  filterItems,
  groupItems,
  groupState,
  isSelected,
  sensitiveSelection,
  setGroup,
} from "../../custom_components/event_blackbox/frontend/event-blackbox-model.js";

const SENSITIVE = new Set(["person", "lock", "camera"]);

const HASS = {
  areas: { demo_hall: { name: "Hall" }, demo_kitchen: { name: "Kitchen" } },
  devices: { demo_device: { area_id: "demo_kitchen" } },
  entities: {
    "light.demo_hall": { area_id: "demo_hall" },
    "light.demo_kitchen": { device_id: "demo_device" },
    "binary_sensor.demo_motion": { area_id: "demo_hall" },
    "sensor.demo_signal": { entity_category: "diagnostic" },
    "switch.demo_hidden": { hidden: true },
  },
  states: {
    "light.demo_hall": { attributes: { friendly_name: "Hall light" } },
    "light.demo_kitchen": { attributes: { friendly_name: "Kitchen light" } },
    "binary_sensor.demo_motion": {
      attributes: { friendly_name: "Hall motion", device_class: "motion" },
    },
    "binary_sensor.demo_door": { attributes: { device_class: "door" } },
    "binary_sensor.demo_other": { attributes: {} },
    "sensor.demo_signal": { attributes: { friendly_name: "Signal" } },
    "switch.demo_hidden": { attributes: {} },
    "lock.demo_door": { attributes: { friendly_name: "Door lock" } },
    "weather.demo_home": { attributes: {} },
  },
};

const title = (key) => `T:${key}`;

test("classify uses domain, device class and the sensitive list", () => {
  assert.equal(classify("light", undefined, SENSITIVE), "lights");
  assert.equal(classify("binary_sensor", "motion", SENSITIVE), "motion");
  assert.equal(classify("binary_sensor", "window", SENSITIVE), "openings");
  assert.equal(classify("binary_sensor", "smoke", SENSITIVE), "binary");
  assert.equal(classify("lock", undefined, SENSITIVE), "sensitive");
  assert.equal(classify("weather", undefined, SENSITIVE), "other");
});

test("catalog resolves names, areas through devices and technical flags", () => {
  const items = buildCatalog(HASS, SENSITIVE);
  const byId = Object.fromEntries(items.map((item) => [item.entityId, item]));
  assert.equal(byId["light.demo_hall"].areaName, "Hall");
  assert.equal(byId["light.demo_kitchen"].areaName, "Kitchen");
  assert.equal(byId["binary_sensor.demo_door"].name, "binary_sensor.demo_door");
  assert.equal(byId["sensor.demo_signal"].technical, true);
  assert.equal(byId["switch.demo_hidden"].technical, true);
  assert.equal(byId["lock.demo_door"].sensitive, true);
  assert.deepEqual(
    items.map((item) => item.name),
    [...items.map((item) => item.name)].sort((a, b) => a.localeCompare(b)),
  );
});

test("filter hides technical entities unless asked or selected", () => {
  const items = buildCatalog(HASS, SENSITIVE);
  const ids = (list) => list.map((item) => item.entityId);
  assert.ok(!ids(filterItems(items, "", false)).includes("sensor.demo_signal"));
  assert.ok(ids(filterItems(items, "", true)).includes("sensor.demo_signal"));
  assert.ok(
    ids(filterItems(items, "", false, new Set(["sensor.demo_signal"]))).includes(
      "sensor.demo_signal",
    ),
  );
  assert.deepEqual(ids(filterItems(items, "hall LIGHT", false)), ["light.demo_hall"]);
  assert.deepEqual(ids(filterItems(items, "kitchen", false)), ["light.demo_kitchen"]);
});

test("grouping by area sorts areas and puts entities without area last", () => {
  const groups = groupItems(filterItems(buildCatalog(HASS, SENSITIVE), "", true), "area", title);
  assert.deepEqual(
    groups.map((group) => group.title),
    ["Hall", "Kitchen", `T:${NO_AREA}`],
  );
  assert.ok(groups.every((group) => group.domain === null));
});

test("grouping by type follows the fixed order and offers whole domains", () => {
  const groups = groupItems(buildCatalog(HASS, SENSITIVE), "type", title);
  assert.deepEqual(
    groups.map((group) => group.id),
    ["lights", "switches", "motion", "openings", "binary", "sensors", "other", "sensitive"],
  );
  assert.equal(groups[0].domain, "light");
  assert.equal(groups.find((group) => group.id === "motion").domain, null);
  assert.equal(groups[0].title, "T:lights");
});

test("selection states and bulk selection", () => {
  const groups = groupItems(buildCatalog(HASS, SENSITIVE), "type", title);
  const lights = groups[0];
  const entities = new Set(["light.demo_hall"]);
  const domains = new Set();
  assert.equal(groupState(lights, entities, domains), "some");
  setGroup(lights, entities, true);
  assert.equal(groupState(lights, entities, domains), "all");
  setGroup(lights, entities, false);
  assert.equal(groupState(lights, entities, domains), "none");
  assert.equal(groupState(lights, new Set(), new Set(["light"])), "all");
  assert.ok(isSelected(lights.items[0], new Set(), new Set(["light"])));
});

test("sensitive selection covers entities and whole domains", () => {
  assert.deepEqual(
    sensitiveSelection(new Set(["lock.demo_door", "light.demo_hall"]), new Set(["camera"]), SENSITIVE),
    ["camera", "lock"],
  );
  assert.deepEqual(sensitiveSelection(new Set(["light.demo_hall"]), new Set(), SENSITIVE), []);
});
