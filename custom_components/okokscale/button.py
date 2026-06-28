"""Support for OKOK Scale diagnostic buttons."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEBUG_ONLY, CONF_DEBUG_TARGET, DOMAIN
from .debug import FOCUSED_DEBUG_CAPTURE_SECONDS
from .runtime import OKOKScaleRuntimeData

PARALLEL_UPDATES = 0
MAXXMEE_SERVICE_UUID = "0000fffe-0000-1000-8000-00805f9b34fb"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OKOK Scale diagnostic buttons."""
    runtime_data: OKOKScaleRuntimeData = entry.runtime_data
    async_add_entities(
        [
            OKOKScaleDebugProtocolButton(
                entry,
                runtime_data,
                "BLE debug protocol",
                "ble_debug_protocol",
                "mdi:bluetooth-connect",
            ),
            OKOKScaleDebugProtocolButton(
                entry,
                runtime_data,
                "BLE focused debug protocol",
                "ble_focused_debug_protocol",
                "mdi:bluetooth-audio",
                duration=FOCUSED_DEBUG_CAPTURE_SECONDS,
                focused=True,
            ),
        ]
    )


class OKOKScaleDebugProtocolButton(ButtonEntity):
    """Button that captures a local BLE debug protocol."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:bluetooth-connect"
    _attr_name = "BLE debug protocol"

    def __init__(
        self,
        entry: config_entries.ConfigEntry,
        runtime_data: OKOKScaleRuntimeData,
        name: str,
        unique_id_suffix: str,
        icon: str,
        duration: int | None = None,
        focused: bool = False,
    ) -> None:
        """Initialize the debug protocol button."""
        self._entry = entry
        self._debug_recorder = runtime_data.debug_recorder
        self._duration = duration
        self._focused = focused
        self._attr_icon = icon
        self._attr_name = name
        assert entry.unique_id is not None
        self._attr_unique_id = f"{entry.unique_id}_{unique_id_suffix}"

    async def async_added_to_hass(self) -> None:
        """Register recorder state updates."""
        self.async_on_remove(
            self._debug_recorder.add_listener(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        """Return true when a capture is not already running."""
        return not self._debug_recorder.running

    @property
    def device_info(self):
        """Return device information."""
        assert self._entry.unique_id is not None
        return {
            "identifiers": {(DOMAIN, self._entry.unique_id)},
            "manufacturer": "MAXXMEE",
            "name": self._entry.title or "MAXXMEE BLE Scale",
        }

    @property
    def extra_state_attributes(self):
        """Return debug protocol status attributes."""
        return {
            "running": self._debug_recorder.running,
            "duration_seconds": self._duration or self._debug_recorder.duration,
            "focused": self._focused,
            "latest_mode": self._debug_recorder.latest_mode,
            "latest_started_at": self._debug_recorder.latest_started_at,
            "latest_finished_at": self._debug_recorder.latest_finished_at,
            "latest_device_count": self._debug_recorder.latest_device_count,
            "latest_advertisement_count": (
                self._debug_recorder.latest_advertisement_count
            ),
            "latest_report_url": self._debug_recorder.latest_report_url,
            "latest_summary_url": self._debug_recorder.latest_summary_url,
            "latest_protocol_url": self._debug_recorder.latest_protocol_url,
            "latest_error": self._debug_recorder.latest_error,
            "debug_only": self._entry.data.get(CONF_DEBUG_ONLY, False),
            "debug_target": self._entry.data.get(CONF_DEBUG_TARGET),
        }

    async def async_press(self) -> None:
        """Start a BLE debug protocol capture."""
        targets = [
            target
            for target in (
                self._entry.unique_id,
                self._entry.title,
                self._entry.data.get(CONF_DEBUG_TARGET),
                "tzc",
                "maxxmee",
                MAXXMEE_SERVICE_UUID,
            )
            if target
        ]
        try:
            await self._debug_recorder.async_start(
                self.hass,
                targets,
                duration=self._duration,
                focused=self._focused,
            )
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from err
