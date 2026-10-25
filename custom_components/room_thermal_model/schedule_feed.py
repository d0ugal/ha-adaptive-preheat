"""Blocks of a Home Assistant ``schedule`` helper (``schedule.get_schedule``), cached.

The service is called at most every ``SCHEDULE_REFRESH_MINUTES``, or sooner when the
schedule entity changes state (a block starts or ends, or the helper was edited). A failure
is logged once until the next success and gives no blocks, so the zone behaves as it does
without a schedule.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import SCHEDULE_REFRESH_MINUTES
from .rtm_core.schedule import Block, parse_blocks

_LOGGER = logging.getLogger(__name__)

SCHEDULE_DOMAIN = "schedule"
SERVICE_GET_SCHEDULE = "get_schedule"
ATTR_NEXT_EVENT = "next_event"


class ScheduleFeed:
    """Cached blocks of one schedule entity."""

    def __init__(self, hass: HomeAssistant, entity_id: str, key: str) -> None:
        """Initialize the feed."""
        self.hass = hass
        self.entity_id = entity_id
        self.key = key
        self.blocks: list[Block] = []
        self.fetched_at: datetime | None = None
        self.fetches = 0
        self._state_key: tuple[Any, ...] | None = None
        self._failed = False

    def _current_state_key(self) -> tuple[Any, ...] | None:
        state = self.hass.states.get(self.entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return (state.state, str(state.attributes.get(ATTR_NEXT_EVENT)), state.last_changed)

    async def async_blocks(self, now: datetime) -> list[Block]:
        """Blocks of the schedule (empty when it is missing, unavailable or failing)."""
        state_key = self._current_state_key()
        if state_key is None:
            self.blocks, self.fetched_at, self._state_key = [], None, None
            return self.blocks
        stale = self.fetched_at is None or now - self.fetched_at >= timedelta(
            minutes=SCHEDULE_REFRESH_MINUTES
        )
        if stale or state_key != self._state_key:
            await self._async_fetch(now, state_key)
        return self.blocks

    async def _async_fetch(self, now: datetime, state_key: tuple[Any, ...]) -> None:
        self.fetches += 1
        self.fetched_at, self._state_key = now, state_key
        try:
            response = await self.hass.services.async_call(
                SCHEDULE_DOMAIN,
                SERVICE_GET_SCHEDULE,
                target={"entity_id": self.entity_id},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, ValueError):  # service missing / failing
            if not self._failed:
                _LOGGER.warning("Reading schedule %s failed", self.entity_id, exc_info=True)
            self._failed = True
            self.blocks = []
            return
        entity = (response or {}).get(self.entity_id)
        self.blocks = parse_blocks(entity, self.key)
        if not self.blocks and not self._failed:
            _LOGGER.debug("Schedule %s has no usable %s blocks", self.entity_id, self.key)
        self._failed = False
