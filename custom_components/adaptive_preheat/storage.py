"""Persistent storage of the learnt zone model.

Stored document (``.storage/adaptive_preheat.<entry_id>``)::

    {
      "schema": 1,
      "model": {...},              # ThermalModel.to_dict() or null
      "last_fit": "2026-...",      # ISO timestamp of the last successful fit or null
      "bootstrapped": true,        # recorder history already merged once
      "inputs": ["climate...", ...], # sorted sample entities the model was learnt from
                                   # (null/missing in older documents: accepted as-is)
      "scoring": {...}             # RunScorer.to_dict() or null (missing in older documents)
    }
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SAVE_DELAY_SECONDS, STORAGE_VERSION

STORAGE_SCHEMA = 1

type StoredData = dict[str, Any]


def empty_document() -> StoredData:
    """Document used when nothing is stored yet."""
    return {
        "schema": STORAGE_SCHEMA,
        "model": None,
        "last_fit": None,
        "bootstrapped": False,
        "inputs": None,
        "scoring": None,
    }


class RTMStore(Store[StoredData]):
    """Store with a migration hook for future schema changes."""

    async def _async_migrate_func(
        self,
        old_major_version: int,  # noqa: ARG002 (Store signature, v1 is the only version)
        old_minor_version: int,  # noqa: ARG002
        old_data: StoredData,
    ) -> StoredData:
        """Migrate stored data to the current version.

        Only version 1 exists so far; add ``if old_major_version < N`` steps here.
        """
        return old_data


class RTMStorage:
    """Thin wrapper around :class:`RTMStore`."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the storage for one zone."""
        self._store = RTMStore(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")

    async def async_load(self) -> StoredData:
        """Load the stored document (an empty one when nothing is stored)."""
        data = await self._store.async_load()
        document = empty_document()
        if isinstance(data, dict):
            document.update(data)
        return document

    def async_schedule_save(self, data_func: Callable[[], StoredData]) -> None:
        """Delayed save (coalesces bursts; Store also flushes it on HA's final write)."""
        self._store.async_delay_save(data_func, SAVE_DELAY_SECONDS)

    async def async_save(self, data: StoredData) -> None:
        """Save immediately."""
        await self._store.async_save(data)

    async def async_remove(self) -> None:
        """Delete the stored file."""
        await self._store.async_remove()
