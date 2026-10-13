"""Config and options flow for the Event Blackbox integration."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import probatio

from .const import DOMAIN, NAME
from .options import (
    CONF_COLLECTION_ENABLED,
    CONF_DOMAINS,
    CONF_ENTITIES,
    LIMITS,
    SELECTABLE_DOMAINS,
    BlackboxOptions,
    parse_options,
    validate_user_input,
)

CONF_ACKNOWLEDGE_SENSITIVE: Final = "acknowledge_sensitive"

# Setup asks for nothing: everything is configured later in the options flow,
# with collection off by default. An empty schema also rejects every unexpected
# key a client might submit.
EMPTY_SCHEMA = probatio.Schema({})


def _options_schema(current: BlackboxOptions) -> probatio.Schema:
    """Build the options form, prefilled with the current (safe) values."""
    fields: dict[Any, Any] = {
        probatio.Required(
            CONF_COLLECTION_ENABLED, default=current.collection_enabled
        ): BooleanSelector(),
        probatio.Optional(
            CONF_ENTITIES, default=list(current.entities)
        ): EntitySelector(EntitySelectorConfig(multiple=True)),
        probatio.Optional(CONF_DOMAINS, default=list(current.domains)): SelectSelector(
            SelectSelectorConfig(
                options=list(SELECTABLE_DOMAINS),
                multiple=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }
    values = current.as_dict()
    for key, limit in LIMITS.items():
        fields[probatio.Required(key, default=values[key])] = NumberSelector(
            NumberSelectorConfig(
                min=limit.minimum,
                max=limit.maximum,
                step=1,
                mode=NumberSelectorMode.BOX,
            )
        )
    return probatio.Schema(fields)


SENSITIVE_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_ACKNOWLEDGE_SENSITIVE, default=False): BooleanSelector()}
)


class EventBlackboxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup. Only one entry is allowed (manifest)."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the setup. Nothing is activated by creating the entry."""
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user", data_schema=EMPTY_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EventBlackboxOptionsFlow:
        """Return the options flow."""
        return EventBlackboxOptionsFlow()


class EventBlackboxOptionsFlow(OptionsFlow):
    """Opt-in, allowlist and limits. Sensitive domains need an extra step."""

    def __init__(self) -> None:
        """Initialise the flow."""
        self._pending: BlackboxOptions | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and validate the options form."""
        current = parse_options(self.config_entry.options)
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = validate_user_input(user_input)
            if options is not None:
                if options.sensitive_domains:
                    self._pending = options
                    return await self.async_step_sensitive()
                return self.async_create_entry(data=options.as_dict())
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
            errors=errors,
        )

    async def async_step_sensitive(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Warn about sensitive domains and require an explicit acknowledgement."""
        pending = self._pending
        if pending is None:  # entered directly: start over at the form
            return await self.async_step_init()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_ACKNOWLEDGE_SENSITIVE) is True:
                return self.async_create_entry(data=pending.as_dict())
            errors["base"] = "acknowledgement_required"
        return self.async_show_form(
            step_id="sensitive",
            data_schema=SENSITIVE_SCHEMA,
            errors=errors,
            description_placeholders={"domains": ", ".join(pending.sensitive_domains)},
        )
