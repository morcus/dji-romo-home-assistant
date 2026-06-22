"""Switch entities for DJI Romo writable device settings.

These wrap the REST ``PUT .../settings`` endpoint (the ``param`` body schema was
captured from the DJI Home app — see ``client.async_set_settings``). Each switch
mirrors a single on/off key from the ``settings`` GET payload and writes it back
through the coordinator, which patches the cached value optimistically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .coordinator import DjiRomoCoordinator
from .entity import DjiRomoCoordinatorEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DjiRomoSwitchDescription(SwitchEntityDescription):
    """Describes a writable settings switch.

    ``param_fn`` builds the ``param`` body for a desired on/off state (it gets the
    coordinator so nested settings can preserve their sibling fields, e.g. keeping
    the Do-Not-Disturb schedule when toggling its ``is_open`` flag); ``value_fn``
    reads the current state from the coordinator (None when unknown).
    """

    value_fn: Callable[[DjiRomoCoordinator], bool | None]
    param_fn: Callable[[DjiRomoCoordinator, bool], dict[str, Any]]


def _setting(coordinator: DjiRomoCoordinator, *path: str) -> Any:
    """Return a value from the REST settings payload by nested key path."""
    current: Any = coordinator.data.cloud_data.get("settings", {})
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _truthy(value: Any) -> bool | None:
    """Coerce a 0/1 setting flag to bool, preserving None when absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return None


def _inverted(value: Any) -> bool | None:
    """Coerce an inverted 0/1 flag (1 = feature OFF) to the feature's on/off state."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value == 0
    return None


SWITCHES: tuple[DjiRomoSwitchDescription, ...] = (
    DjiRomoSwitchDescription(
        key="child_lock",
        translation_key="child_lock",
        name="Child Lock",
        icon="mdi:lock",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: _truthy(
            _setting(coordinator, "is_child_lock_open")
        ),
        param_fn=lambda coordinator, on: {"is_child_lock_open": 1 if on else 0},
    ),
    DjiRomoSwitchDescription(
        key="cliff_detection",
        translation_key="cliff_detection",
        name="Cliff Detection",
        icon="mdi:stairs-down",
        entity_category=EntityCategory.CONFIG,
        # Inverted flag: the app's "void detection" ON maps to is_no_stair_mode = 0
        # ("no-stair mode" off), OFF maps to 1. Captured via MITM 2026-06-22.
        value_fn=lambda coordinator: _inverted(
            _setting(coordinator, "is_no_stair_mode")
        ),
        param_fn=lambda coordinator, on: {"is_no_stair_mode": 0 if on else 1},
    ),
    DjiRomoSwitchDescription(
        key="battery_care",
        translation_key="battery_care",
        name="Battery Care",
        icon="mdi:battery-heart-variant",
        entity_category=EntityCategory.CONFIG,
        # Caps the resting charge at 80% to slow battery aging. Non-inverted
        # (ON = 1), captured via MITM 2026-06-22. Reads the REST setting (the
        # osd battery_care_active flag is its live mirror).
        value_fn=lambda coordinator: _truthy(_setting(coordinator, "battery_care")),
        param_fn=lambda coordinator, on: {"battery_care": 1 if on else 0},
    ),
    DjiRomoSwitchDescription(
        key="do_not_disturb",
        translation_key="do_not_disturb",
        name="Do Not Disturb",
        icon="mdi:bell-sleep",
        entity_category=EntityCategory.CONFIG,
        # Nested setting: the app sends the whole no_disturb object (the schedule
        # is managed in the app), so we preserve the sibling fields and only flip
        # is_open. Captured via MITM 2026-06-22.
        value_fn=lambda coordinator: _truthy(
            _setting(coordinator, "no_disturb", "is_open")
        ),
        param_fn=lambda coordinator, on: {
            "no_disturb": {
                **(_setting(coordinator, "no_disturb") or {}),
                "is_open": 1 if on else 0,
            }
        },
    ),
    DjiRomoSwitchDescription(
        key="hot_water_mop",
        translation_key="hot_water_mop",
        name="Hot Water Mopping",
        icon="mdi:water-thermometer",
        entity_category=EntityCategory.CONFIG,
        # Washes the mop pads with hot water at the dock. Non-inverted (ON = 1),
        # captured via MITM 2026-06-22.
        value_fn=lambda coordinator: _truthy(
            _setting(coordinator, "wash_mop_with_hot_water")
        ),
        param_fn=lambda coordinator, on: {
            "wash_mop_with_hot_water": 1 if on else 0
        },
    ),
    DjiRomoSwitchDescription(
        key="auto_add_cleaner",
        translation_key="auto_add_cleaner",
        name="Auto Add Cleaner",
        icon="mdi:bottle-tonic-plus",
        entity_category=EntityCategory.CONFIG,
        # Nested setting: the app sends the whole add_cleaner_auto object, so we
        # preserve the sibling (sewage_tank_deodorizer) and only flip is_add_in_mop.
        # Captured via MITM 2026-06-22.
        value_fn=lambda coordinator: _truthy(
            _setting(coordinator, "add_cleaner_auto", "is_add_in_mop")
        ),
        param_fn=lambda coordinator, on: {
            "add_cleaner_auto": {
                **(_setting(coordinator, "add_cleaner_auto") or {}),
                "is_add_in_mop": 1 if on else 0,
            }
        },
    ),
    DjiRomoSwitchDescription(
        key="enhanced_particle_cleaning",
        translation_key="enhanced_particle_cleaning",
        name="Enhanced Particle Cleaning",
        icon="mdi:grain",
        entity_category=EntityCategory.CONFIG,
        # Flat key, non-inverted (ON = 1). Captured via MITM 2026-06-22.
        value_fn=lambda coordinator: _truthy(
            _setting(coordinator, "enhance_particle_clean")
        ),
        param_fn=lambda coordinator, on: {
            "enhance_particle_clean": 1 if on else 0
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Romo settings switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        DjiRomoSettingSwitch(coordinator, description) for description in SWITCHES
    )


class DjiRomoSettingSwitch(DjiRomoCoordinatorEntity, SwitchEntity):
    """A device setting exposed as a writable switch."""

    entity_description: DjiRomoSwitchDescription

    def __init__(
        self,
        coordinator: DjiRomoCoordinator,
        description: DjiRomoSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_sn}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the current setting state (None when not yet known)."""
        return self.entity_description.value_fn(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the setting."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the setting."""
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        try:
            await self.coordinator.async_set_device_setting(
                self.entity_description.param_fn(self.coordinator, on)
            )
        except UpdateFailed as err:
            raise HomeAssistantError(
                f"Failed to set DJI Romo '{self.name}': {err}"
            ) from err
