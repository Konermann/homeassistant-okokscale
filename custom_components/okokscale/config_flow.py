"""Config flow for OKOK Scale integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_DEBUG_ONLY,
    CONF_DEBUG_TARGET,
    CONF_DISCOVERED_DEVICE,
    DOMAIN,
)
from .okokscale import OKOKScaleBluetoothDeviceData as DeviceData

_LOGGER = logging.getLogger(__name__)


class OKOKScaleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OKOK Scales."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_device: DeviceData | None = None
        self._discovered_devices: dict[str, str] = {}
        self._discovered_titles: dict[str, str] = {}
        self._supported_devices: set[str] = set()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        device = DeviceData()
        if not device.supported(discovery_info):
            return self.async_abort(reason="not_supported")

        _LOGGER.debug("%s is not yet configured", discovery_info.address)

        self._discovery_info = discovery_info
        self._discovered_device = device

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""

        assert self._discovered_device is not None
        device = self._discovered_device
        assert self._discovery_info is not None
        discovery_info = self._discovery_info
        title = device.title or device.get_device_name() or discovery_info.name

        if user_input is not None:
            return self.async_create_entry(title=title, data={})

        self._set_confirm_only()
        placeholders = {"name": title}
        self.context["title_placeholders"] = placeholders
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["title_placeholders"],
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick a device or create debug entry."""
        self._refresh_discovered_devices()

        if user_input is not None:
            selected_device = user_input.get(CONF_DISCOVERED_DEVICE)
            manual_target = (user_input.get(CONF_DEBUG_TARGET) or "").strip()
            target = manual_target or selected_device
            if not target:
                return self._show_user_form({"base": "target_required"})

            if not manual_target and selected_device in self._supported_devices:
                await self.async_set_unique_id(target, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._discovered_titles[target],
                    data={},
                )

            unique_id = f"debug:{target.lower()}"
            await self.async_set_unique_id(unique_id, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"MAXXMEE BLE debug ({target})",
                data={
                    CONF_DEBUG_ONLY: True,
                    CONF_DEBUG_TARGET: target,
                },
            )

        return self._show_user_form()

    def _refresh_discovered_devices(self) -> None:
        """Refresh the known BLE devices for the user setup form."""
        self._discovered_devices.clear()
        self._discovered_titles.clear()
        self._supported_devices.clear()
        current_addresses = self._async_current_ids(include_ignore=False)
        for connectable in (False, True):
            for discovery_info in async_discovered_service_info(
                self.hass, connectable
            ):
                address = discovery_info.address
                if (
                    address in current_addresses
                    or address in self._discovered_devices
                ):
                    continue
                device = DeviceData()
                supported = device.supported(discovery_info)
                if supported:
                    self._supported_devices.add(address)
                title = (
                    device.title
                    or device.get_device_name()
                    or discovery_info.name
                    or "Unknown BLE device"
                )
                self._discovered_titles[address] = title
                self._discovered_devices[address] = self._format_device_label(
                    discovery_info, title, supported
                )

    @staticmethod
    def _format_device_label(
        discovery_info: BluetoothServiceInfoBleak,
        title: str,
        supported: bool,
    ) -> str:
        """Return a readable discovered-device label."""
        mode = "supported" if supported else "debug only"
        rssi = getattr(discovery_info, "rssi", None)
        rssi_text = f", RSSI {rssi}" if rssi is not None else ""
        return f"{title} ({discovery_info.address}{rssi_text}, {mode})"

    def _show_user_form(
        self,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Show the manual setup/debug form."""
        schema_fields: dict[Any, Any] = {
            vol.Optional(CONF_DEBUG_TARGET): str,
        }
        if self._discovered_devices:
            schema_fields[
                vol.Optional(CONF_DISCOVERED_DEVICE)
            ] = vol.In(self._discovered_devices)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema_fields),
            errors=errors or {},
        )
