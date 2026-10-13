"""Timeline panel, its WebSocket commands and the panel code."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from homeassistant.components import frontend
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)

from custom_components.event_blackbox.const import DOMAIN
from custom_components.event_blackbox.panel import (
    FRONTEND_DIR,
    PANEL_COMPONENT,
    PANEL_FILE,
    PANEL_ICON,
    PANEL_URL_PATH,
    STATIC_URL,
)

MODEL_FILE = "event-blackbox-model.js"
I18N_FILE = "event-blackbox-i18n.js"
PANEL_SOURCE = "\n".join(
    (FRONTEND_DIR / name).read_text(encoding="utf-8")
    for name in (PANEL_FILE, MODEL_FILE, I18N_FILE)
)
# The panel code without comments: JSDoc types such as {import("./x.js").T}
# are not calls. Only block comments and whole-line comments are removed, so
# a "//" inside a string is still checked.
PANEL_CODE = re.sub(
    r"^\s*//.*$",
    "",
    re.sub(r"/\*.*?\*/", "", PANEL_SOURCE, flags=re.DOTALL),
    flags=re.MULTILINE,
)
HALL = "input_boolean.demo_hall"
MOTION = "input_boolean.demo_motion"


def _options(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 - option values
    return {
        "collection_enabled": True,
        "entities": [HALL, MOTION],
        "domains": [],
        "max_events": 2000,
        "max_kib": 2048,
        "max_age_minutes": 30,
        **overrides,
    }


async def _setup(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"demo_hall": {}, "demo_motion": {}}}
    )
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _turn_on(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "input_boolean",
        "turn_on",
        {"entity_id": entity_id},
        blocking=True,
        context=Context(),
    )
    await hass.async_block_till_done()


# --- panel registration -----------------------------------------------------


async def test_panel_is_admin_only_and_removed_on_unload(hass: HomeAssistant) -> None:
    """The sidebar entry exists while loaded and requires an administrator."""
    entry = await _setup(hass, _options())
    panel = hass.data[frontend.DATA_PANELS][PANEL_URL_PATH]
    assert panel.require_admin is True
    assert panel.config is not None
    assert panel.config["_panel_custom"]["name"] == PANEL_COMPONENT
    assert panel.config["_panel_custom"]["module_url"].startswith(
        f"{STATIC_URL}/{PANEL_FILE}?v="
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert PANEL_URL_PATH not in hass.data[frontend.DATA_PANELS]


async def test_static_path_serves_the_panel_code(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """The static path delivers the code file, and nothing else exists there."""
    await _setup(hass, _options())
    client = await hass_client()
    response = await client.get(f"{STATIC_URL}/{PANEL_FILE}")
    assert response.status == 200
    assert "customElements.define" in await response.text()
    missing = await client.get(f"{STATIC_URL}/missing.js")
    assert missing.status == 404


# --- WebSocket commands -----------------------------------------------------


async def test_status_lists_entities_with_changes(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Status reports recording and entities with their number of changes."""
    await _setup(hass, _options())
    await _turn_on(hass, HALL)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/status"})
    response = await client.receive_json()
    assert response["success"] is True
    result = response["result"]
    assert result["recording"] is True
    assert result["started_at"]
    assert [(e["entity_id"], e["changes"]) for e in result["entities"]] == [(HALL, 1)]
    assert result["entities"][0]["last_evidence"] == "CONFIRMED_BY_CONTEXT"
    assert result["buffer"]["max_events"] == 2000
    assert result["buffer"]["max_bytes"] == 2048 * 1024
    assert result["buffer"]["events"] == len(result["activity"])
    assert result["buffer"]["dropped"] == 0
    assert result["evidence"]["CONFIRMED_BY_CONTEXT"] == 1


async def test_status_when_not_recording(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Without recording, status says so and lists nothing."""
    await _setup(hass, _options(collection_enabled=False))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/status"})
    response = await client.receive_json()
    result = response["result"]
    assert result["recording"] is False
    assert result["buffer"] is None
    assert result["entities"] == []
    assert result["activity"] == []


async def test_explain_latest_and_selected_change(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Explain returns the report and can select an earlier change."""
    await _setup(hass, _options())
    await _turn_on(hass, HALL)
    await hass.services.async_call(
        "input_boolean", "turn_off", {"entity_id": HALL}, blocking=True
    )
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "event_blackbox/explain", "entity_id": HALL}
    )
    latest = (await client.receive_json())["result"]
    assert latest["status"] == "explained"
    assert latest["change"]["transition"] == "on_to_off"
    assert len(latest["changes"]) == 2

    earlier = latest["changes"][1]["event_id"]
    await client.send_json_auto_id(
        {"type": "event_blackbox/explain", "entity_id": HALL, "event_id": earlier}
    )
    selected = (await client.receive_json())["result"]
    assert selected["change"]["transition"] == "off_to_on"
    # Context IDs (ULIDs) never leave the integration.
    assert re.search(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", json.dumps(selected)) is None


@pytest.mark.parametrize(
    ("options", "entity_id", "code"),
    [
        ({"collection_enabled": False}, HALL, "not_recording"),
        ({}, "light.demo_unlisted", "not_on_allowlist"),
    ],
)
async def test_explain_errors(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    options: dict[str, Any],
    entity_id: str,
    code: str,
) -> None:
    """Refusals come back as error codes the panel can show."""
    await _setup(hass, _options(**options))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "event_blackbox/explain", "entity_id": entity_id}
    )
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == code


@pytest.mark.parametrize(
    "message",
    [
        {"type": "event_blackbox/status"},
        {"type": "event_blackbox/explain", "entity_id": HALL},
    ],
)
async def test_commands_reject_non_admin(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    message: dict[str, Any],
) -> None:
    """A non-admin connection gets no data."""
    await _setup(hass, _options())
    await _turn_on(hass, HALL)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(message)
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "event_blackbox/explain", "entity_id": "not an entity"},
        {"type": "event_blackbox/explain", "entity_id": HALL, "event_id": 0},
        {"type": "event_blackbox/explain", "entity_id": HALL, "extra": 1},
    ],
)
async def test_invalid_messages_are_rejected(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, message: dict[str, Any]
) -> None:
    """The command schemas reject malformed input."""
    await _setup(hass, _options())
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(message)
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == "invalid_format"


# --- panel code policy (content security) -----------------------------------

FORBIDDEN_IN_PANEL = {
    "innerHTML": r"\binnerHTML\b",
    "outerHTML": r"\bouterHTML\b",
    "insertAdjacentHTML": r"\binsertAdjacentHTML\b",
    "document.write": r"\bdocument\.write\b",
    "eval": r"\beval\s*\(",
    "new Function": r"\bnew\s+Function\b",
    "setTimeout with a string": r"setTimeout\(\s*[\"'`]",
    "inline event handler": r"setAttribute\(\s*[\"'`]on",
    "absolute URL": r"\b(?:https?|wss?):\/\/",
    "protocol-relative URL": r"[\"'`]\/\/",
    "dynamic import": r"\bimport\s*\(",
    "fetch": r"\bfetch\s*\(",
    "XMLHttpRequest": r"\bXMLHttpRequest\b",
    "localStorage": r"\blocalStorage\b",
}


@pytest.mark.parametrize(("name", "pattern"), sorted(FORBIDDEN_IN_PANEL.items()))
def test_panel_code_avoids_unsafe_constructs(name: str, pattern: str) -> None:
    """Data is rendered as text only and nothing is loaded from elsewhere."""
    assert re.search(pattern, PANEL_CODE) is None, name


def test_panel_code_is_the_only_static_content() -> None:
    """The static path contains the three panel modules and nothing else."""
    assert sorted(p.name for p in Path(FRONTEND_DIR).iterdir()) == sorted(
        [PANEL_FILE, MODEL_FILE, I18N_FILE]
    )


def _mdi_icons() -> set[str]:
    """Return the icon names that have drawing data in Home Assistant's frontend."""
    import hass_frontend  # noqa: PLC0415

    mdi = Path(hass_frontend.where()) / "static" / "mdi"
    names: set[str] = set()
    for chunk in mdi.glob("*.json"):
        if chunk.name == "iconList.json":
            continue  # aliases only, no drawing data
        names.update(json.loads(chunk.read_text(encoding="utf-8")))
    return names


def test_every_icon_used_exists_in_home_assistant() -> None:
    """Icons in the panel, the sidebar and the action really render."""
    icons_json = json.loads(
        (FRONTEND_DIR.parent / "icons.json").read_text(encoding="utf-8")
    )
    used = set(re.findall(r"mdi:([a-z0-9-]+)", PANEL_SOURCE))
    used.add(PANEL_ICON.removeprefix("mdi:"))
    used.add(icons_json["services"]["explain"]["service"].removeprefix("mdi:"))
    missing = used - _mdi_icons()
    assert not missing, sorted(missing)


def test_panel_translations_cover_every_language() -> None:
    """English, German and Greek blocks exist; the type check enforces equal keys."""
    for language in ("en: EN", "de: {", "el: {"):
        assert language in PANEL_SOURCE


# --- configuration through the panel -----------------------------


async def test_get_config_returns_options_and_limits(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The panel receives the current options, limits and domain lists."""
    await _setup(hass, _options())
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/get_config"})
    result = (await client.receive_json())["result"]
    assert result["configured"] is True
    assert result["options"]["entities"] == sorted([HALL, MOTION])
    assert result["limits"]["max_events"] == {"min": 100, "max": 10000, "default": 2000}
    assert result["max_entities"] == 200
    assert "person" in result["sensitive_domains"]
    assert "light" in result["selectable_domains"]


async def test_get_config_without_entry(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Without a config entry the panel is told so."""
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/get_config"})
    result = (await client.receive_json())["result"]
    assert result["configured"] is False


async def test_set_config_saves_and_reloads(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Saved options are validated, stored and take effect through a reload."""
    entry = await _setup(hass, _options(collection_enabled=False))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "event_blackbox/set_config",
            "options": _options(entities=[HALL], unknown="dropped"),
        }
    )
    result = (await client.receive_json())["result"]
    await hass.async_block_till_done()
    assert result == {"saved": True, "errors": {}, "sensitive_domains": []}
    assert entry.options["entities"] == [HALL]
    assert "unknown" not in entry.options
    assert entry.runtime_data.recorder is not None


async def test_set_config_rejects_invalid_options(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Invalid options are reported per field and nothing is stored."""
    entry = await _setup(hass, _options(collection_enabled=False))
    before = dict(entry.options)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "event_blackbox/set_config",
            "options": _options(entities=[], domains=[]),
        }
    )
    result = (await client.receive_json())["result"]
    assert result["saved"] is False
    assert result["errors"] == {"base": "allowlist_required"}
    assert dict(entry.options) == before


async def test_set_config_needs_acknowledgement_for_sensitive(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Sensitive domains are only saved with an explicit confirmation."""
    entry = await _setup(hass, _options(collection_enabled=False))
    client = await hass_ws_client(hass)
    message = {
        "type": "event_blackbox/set_config",
        "options": _options(entities=["lock.demo_door"], domains=["camera"]),
    }
    await client.send_json_auto_id(message)
    refused = (await client.receive_json())["result"]
    assert refused["saved"] is False
    assert refused["errors"] == {"base": "acknowledgement_required"}
    assert refused["sensitive_domains"] == ["camera", "lock"]
    assert entry.options["collection_enabled"] is False

    await client.send_json_auto_id({**message, "acknowledge_sensitive": True})
    saved = (await client.receive_json())["result"]
    await hass.async_block_till_done()
    assert saved["saved"] is True
    assert entry.options["domains"] == ["camera"]


async def test_set_config_without_entry_is_an_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Saving needs a config entry."""
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "event_blackbox/set_config", "options": _options()}
    )
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == "not_configured"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "event_blackbox/get_config"},
        {"type": "event_blackbox/set_config", "options": {}},
    ],
)
async def test_config_commands_reject_non_admin(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    message: dict[str, Any],
) -> None:
    """Only administrators can read or change what is recorded."""
    entry = await _setup(hass, _options(collection_enabled=False))
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(message)
    response = await client.receive_json()
    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"
    assert entry.options["collection_enabled"] is False
