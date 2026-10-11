"""Base entity: device per zone, translated names, explicit entity_id."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import RTMCoordinator, ZoneData


class RTMEntity(CoordinatorEntity[RTMCoordinator]):
    """Common base for all zone entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RTMCoordinator, key: str, entity_id_format: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        # Explicit entity_id (``<platform>.<zone>_<key>``), independent of the UI language.
        self.entity_id = async_generate_entity_id(
            entity_id_format, f"{coordinator.zone_name}_{key}", hass=coordinator.hass
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=coordinator.zone_name,
            manufacturer=NAME,
            model="RC thermal model",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def zone(self) -> ZoneData | None:
        """Latest zone data."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Available when the coordinator has data."""
        return super().available and self.coordinator.data is not None
