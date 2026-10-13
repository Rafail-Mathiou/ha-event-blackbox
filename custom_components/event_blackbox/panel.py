"""Sidebar panel for administrators.

The static path serves the panel code only, never data. The panel is added
while the config entry is loaded and removed on unload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

FRONTEND_DIR: Final = Path(__file__).parent / "frontend"
STATIC_URL: Final = "/event_blackbox_static"
PANEL_FILE: Final = "event-blackbox-panel.js"
PANEL_COMPONENT: Final = "event-blackbox-panel"
PANEL_URL_PATH: Final = "event-blackbox"
PANEL_TITLE: Final = "Event Blackbox"
PANEL_ICON: Final = "mdi:timeline-question-outline"


async def async_register_static_path(hass: HomeAssistant) -> None:
    """Serve the panel code. Called once per Home Assistant start."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
    )


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    """Add the panel to the sidebar, visible to administrators only."""
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{STATIC_URL}/{PANEL_FILE}?v={version}",
        require_admin=True,
    )


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the panel from the sidebar."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
