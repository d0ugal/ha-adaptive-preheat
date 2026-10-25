"""Registry of the zones that share a boiler zone (Home Assistant glue).

Config entries with the same ``zone_group`` option form a pool. Each member publishes a
small immutable summary of its fit (heating rate, loss rate, maturity); a member whose
own fit is not mature asks for the peers' summaries before refitting and builds a prior
from them (:func:`rtm_core.shared_prior.build_group_prior`).

The registry lives in ``hass.data[DOMAIN]`` and holds only frozen summaries, never a
reference to a coordinator or model, so a peer unloading mid-run cannot affect a fit in
progress: the prior is copied before the executor job starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .rtm_core.shared_prior import PeerSummary

DATA_KEY = "zone_groups"


@dataclass
class ZoneGroups:
    """Members per group: ``group -> {entry_id: summary or None (no trusted fit yet)}``."""

    members: dict[str, dict[str, PeerSummary | None]] = field(
        default_factory=dict[str, dict[str, PeerSummary | None]]
    )

    def join(self, group: str, entry_id: str) -> None:
        self.members.setdefault(group, {}).setdefault(entry_id, None)

    def publish(self, group: str, entry_id: str, summary: PeerSummary | None) -> None:
        self.members.setdefault(group, {})[entry_id] = summary

    def leave(self, entry_id: str) -> None:
        for group in list(self.members):
            self.members[group].pop(entry_id, None)
            if not self.members[group]:
                del self.members[group]

    def peers(self, group: str, entry_id: str) -> list[PeerSummary]:
        """Summaries of the other members of ``group`` (those with a fit)."""
        return [
            s for eid, s in self.members.get(group, {}).items() if eid != entry_id and s is not None
        ]


def normalize_group(value: object) -> str | None:
    """Group name as stored: stripped and case-folded, None when empty."""
    if not isinstance(value, str):
        return None
    return value.strip().casefold() or None


def _registry(hass: HomeAssistant) -> ZoneGroups:
    domain_data = hass.data.setdefault(DOMAIN, {})
    groups = domain_data.get(DATA_KEY)
    if groups is None:
        groups = domain_data[DATA_KEY] = ZoneGroups()
    return groups


def async_join(hass: HomeAssistant, group: str, entry_id: str) -> None:
    _registry(hass).join(group, entry_id)


def async_publish(
    hass: HomeAssistant, group: str, entry_id: str, summary: PeerSummary | None
) -> None:
    _registry(hass).publish(group, entry_id, summary)


def async_peers(hass: HomeAssistant, group: str, entry_id: str) -> list[PeerSummary]:
    return _registry(hass).peers(group, entry_id)


def async_leave(hass: HomeAssistant, entry_id: str) -> None:
    """Remove a zone from every group; drop the registry when nothing is left."""
    domain_data = hass.data.get(DOMAIN)
    if not domain_data or (groups := domain_data.get(DATA_KEY)) is None:
        return
    groups.leave(entry_id)
    if not groups.members:
        del domain_data[DATA_KEY]
    if not domain_data:
        del hass.data[DOMAIN]
