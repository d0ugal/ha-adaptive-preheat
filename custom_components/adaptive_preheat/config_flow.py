"""Config, reconfigure and options flow (one config entry per zone)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
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
    TextSelector,
)

from .const import (
    CONF_ALLOW_UNTRUSTED,
    CONF_ARRIVAL_SENSOR,
    CONF_CLIMATE,
    CONF_EFFORT_SENSOR,
    CONF_HALF_LIFE_DAYS,
    CONF_HISTORY_DAYS,
    CONF_OUTDOOR_SENSOR,
    CONF_ROOM_SENSOR,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_TARGET_KEY,
    CONF_SELF_CALIBRATE,
    CONF_TARGET_MODE,
    CONF_TARGET_TEMPERATURE,
    CONF_UPDATE_INTERVAL_MINUTES,
    CONF_WEATHER,
    CONF_ZONE_GROUP,
    CONF_ZONE_NAME,
    DEFAULT_ALLOW_UNTRUSTED,
    DEFAULT_HALF_LIFE_DAYS,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_SCHEDULE_TARGET_KEY,
    DEFAULT_SELF_CALIBRATE,
    DEFAULT_TARGET_MODE,
    DEFAULT_TARGET_TEMPERATURE,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    HALF_LIFE_DAYS_MAX,
    HALF_LIFE_DAYS_MIN,
    HISTORY_DAYS_MAX,
    HISTORY_DAYS_MIN,
    TARGET_MODE_CLIMATE,
    TARGET_MODE_FIXED,
    TARGET_TEMPERATURE_MAX,
    TARGET_TEMPERATURE_MIN,
    TARGET_TEMPERATURE_STEP,
    UPDATE_INTERVAL_MAX,
    UPDATE_INTERVAL_MIN,
    ZONE_GROUP_MAX_LENGTH,
)
from .zone_group import normalize_group

OPTIONAL_INPUTS = (
    CONF_ROOM_SENSOR,
    CONF_EFFORT_SENSOR,
    CONF_OUTDOOR_SENSOR,
    CONF_WEATHER,
    CONF_ARRIVAL_SENSOR,
    CONF_SCHEDULE_ENTITY,
    CONF_SCHEDULE_TARGET_KEY,
)


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _target_fields() -> dict[Any, Any]:
    return {
        vol.Required(CONF_TARGET_MODE, default=DEFAULT_TARGET_MODE): SelectSelector(
            SelectSelectorConfig(
                options=[TARGET_MODE_FIXED, TARGET_MODE_CLIMATE],
                translation_key=CONF_TARGET_MODE,
                mode=SelectSelectorMode.LIST,
            )
        ),
        vol.Required(CONF_TARGET_TEMPERATURE, default=DEFAULT_TARGET_TEMPERATURE): _number(
            TARGET_TEMPERATURE_MIN, TARGET_TEMPERATURE_MAX, TARGET_TEMPERATURE_STEP, "°C"
        ),
    }


def _input_fields(include_climate: bool) -> dict[Any, Any]:
    fields: dict[Any, Any] = {vol.Required(CONF_ZONE_NAME): TextSelector()}
    if include_climate:
        fields[vol.Required(CONF_CLIMATE)] = EntitySelector(EntitySelectorConfig(domain="climate"))
    fields.update(
        {
            vol.Optional(CONF_ROOM_SENSOR): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_EFFORT_SENSOR): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "number", "input_number"])
            ),
            vol.Optional(CONF_OUTDOOR_SENSOR): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_WEATHER): EntitySelector(EntitySelectorConfig(domain="weather")),
            vol.Optional(CONF_ARRIVAL_SENSOR): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="timestamp")
            ),
            vol.Optional(CONF_SCHEDULE_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="schedule")
            ),
            vol.Optional(CONF_SCHEDULE_TARGET_KEY): TextSelector(),
        }
    )
    return fields


def _clean(user_input: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Keep non-empty values of ``keys``."""
    cleaned: dict[str, Any] = {}
    for key in keys:
        value = user_input.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value:
            cleaned[key] = value
    # the key only matters with a schedule; the default is implied
    if CONF_SCHEDULE_ENTITY not in cleaned or (
        cleaned.get(CONF_SCHEDULE_TARGET_KEY) == DEFAULT_SCHEDULE_TARGET_KEY
    ):
        cleaned.pop(CONF_SCHEDULE_TARGET_KEY, None)
    return cleaned


class RoomThermalModelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a zone."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Zone name, climate, optional inputs and target."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_ZONE_NAME, "")).strip()
            if not name:
                errors[CONF_ZONE_NAME] = "name_required"
            else:
                await self.async_set_unique_id(user_input[CONF_CLIMATE])
                self._abort_if_unique_id_configured()
                data = {
                    CONF_ZONE_NAME: name,
                    CONF_CLIMATE: user_input[CONF_CLIMATE],
                    **_clean(user_input, OPTIONAL_INPUTS),
                }
                options = {
                    CONF_TARGET_MODE: user_input.get(CONF_TARGET_MODE, DEFAULT_TARGET_MODE),
                    CONF_TARGET_TEMPERATURE: float(
                        user_input.get(CONF_TARGET_TEMPERATURE, DEFAULT_TARGET_TEMPERATURE)
                    ),
                    CONF_HALF_LIFE_DAYS: DEFAULT_HALF_LIFE_DAYS,
                    CONF_HISTORY_DAYS: DEFAULT_HISTORY_DAYS,
                    CONF_UPDATE_INTERVAL_MINUTES: DEFAULT_UPDATE_INTERVAL_MINUTES,
                    CONF_SELF_CALIBRATE: DEFAULT_SELF_CALIBRATE,
                }
                return self.async_create_entry(title=name, data=data, options=options)
        schema = vol.Schema({**_input_fields(include_climate=True), **_target_fields()})
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the zone name and the optional inputs (the climate stays)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_ZONE_NAME, "")).strip()
            if not name:
                errors[CONF_ZONE_NAME] = "name_required"
            else:
                data = {
                    CONF_ZONE_NAME: name,
                    CONF_CLIMATE: entry.data[CONF_CLIMATE],
                    **_clean(user_input, OPTIONAL_INPUTS),
                }
                return self.async_update_reload_and_abort(entry, title=name, data=data)
        schema = vol.Schema(_input_fields(include_climate=False))
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or entry.data),
            errors=errors,
            description_placeholders={"climate": entry.data[CONF_CLIMATE]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> RoomThermalModelOptionsFlow:  # noqa: ARG004 (HA signature)
        """Return the options flow."""
        return RoomThermalModelOptionsFlow()


class RoomThermalModelOptionsFlow(OptionsFlowWithReload):
    """Target and learning options (the entry reloads on change)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            group = normalize_group(user_input.get(CONF_ZONE_GROUP))
            if group is not None and len(group) > ZONE_GROUP_MAX_LENGTH:
                errors[CONF_ZONE_GROUP] = "group_too_long"
        if user_input is not None and not errors:
            return self.async_create_entry(
                data={
                    **({CONF_ZONE_GROUP: group} if group else {}),
                    CONF_TARGET_MODE: user_input[CONF_TARGET_MODE],
                    CONF_TARGET_TEMPERATURE: float(user_input[CONF_TARGET_TEMPERATURE]),
                    CONF_HALF_LIFE_DAYS: float(user_input[CONF_HALF_LIFE_DAYS]),
                    CONF_HISTORY_DAYS: int(user_input[CONF_HISTORY_DAYS]),
                    CONF_UPDATE_INTERVAL_MINUTES: int(user_input[CONF_UPDATE_INTERVAL_MINUTES]),
                    CONF_ALLOW_UNTRUSTED: bool(user_input.get(CONF_ALLOW_UNTRUSTED, False)),
                    CONF_SELF_CALIBRATE: bool(
                        user_input.get(CONF_SELF_CALIBRATE, DEFAULT_SELF_CALIBRATE)
                    ),
                }
            )
        current = {
            CONF_TARGET_MODE: DEFAULT_TARGET_MODE,
            CONF_TARGET_TEMPERATURE: DEFAULT_TARGET_TEMPERATURE,
            CONF_HALF_LIFE_DAYS: DEFAULT_HALF_LIFE_DAYS,
            CONF_HISTORY_DAYS: DEFAULT_HISTORY_DAYS,
            CONF_UPDATE_INTERVAL_MINUTES: DEFAULT_UPDATE_INTERVAL_MINUTES,
            CONF_ALLOW_UNTRUSTED: DEFAULT_ALLOW_UNTRUSTED,
            CONF_SELF_CALIBRATE: DEFAULT_SELF_CALIBRATE,
            **self.config_entry.options,
        }
        schema = vol.Schema(
            {
                **_target_fields(),
                vol.Required(CONF_HALF_LIFE_DAYS): _number(
                    HALF_LIFE_DAYS_MIN, HALF_LIFE_DAYS_MAX, 1, "d"
                ),
                vol.Required(CONF_HISTORY_DAYS): _number(
                    HISTORY_DAYS_MIN, HISTORY_DAYS_MAX, 1, "d"
                ),
                vol.Required(CONF_UPDATE_INTERVAL_MINUTES): _number(
                    UPDATE_INTERVAL_MIN, UPDATE_INTERVAL_MAX, 1, "min"
                ),
                vol.Optional(
                    CONF_ALLOW_UNTRUSTED, default=DEFAULT_ALLOW_UNTRUSTED
                ): BooleanSelector(),
                vol.Optional(CONF_ZONE_GROUP): TextSelector(),
                vol.Required(CONF_SELF_CALIBRATE): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )
