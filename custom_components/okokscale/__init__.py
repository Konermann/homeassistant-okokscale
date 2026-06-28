"""The OKOK Scale integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
)
from homeassistant.components.bluetooth.active_update_processor import (
    ActiveBluetoothProcessorCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry

from .const import CONF_DEBUG_ONLY, DOMAIN
from .debug import OKOKScaleDebugRecorder
from .measurements import OKOKScaleMeasurementStore
from .okokscale import OKOKScaleBluetoothDeviceData
from .runtime import OKOKScaleRuntimeData

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]
DEBUG_PLATFORMS: list[Platform] = [Platform.BUTTON]

_LOGGER = logging.getLogger(__name__)

DATA_MEASUREMENT_STORES = "measurement_stores"
DATA_SERVICES_REGISTERED = "services_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OKOK Scale device from a config entry."""
    address = entry.unique_id
    assert address is not None

    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault(DATA_MEASUREMENT_STORES, {})
    _async_register_measurement_services(hass)

    debug_recorder = OKOKScaleDebugRecorder()
    entry.async_on_unload(debug_recorder.cancel)

    if entry.data.get(CONF_DEBUG_ONLY):
        entry.runtime_data = OKOKScaleRuntimeData(
            coordinator=None,
            device_data=None,
            debug_recorder=debug_recorder,
            measurement_store=None,
        )
        await hass.config_entries.async_forward_entry_setups(entry, DEBUG_PLATFORMS)
        return True

    data = OKOKScaleBluetoothDeviceData()
    measurement_store = OKOKScaleMeasurementStore(hass, entry.entry_id)
    await measurement_store.async_load()
    domain_data[DATA_MEASUREMENT_STORES][entry.entry_id] = measurement_store

    def _record_measurement(reading) -> None:
        hass.async_create_task(
            measurement_store.async_record_measurement(
                weight_kg=reading.weight,
                source="ble",
                metadata={
                    "unit": reading.unit,
                    "raw_weight": reading.raw_weight,
                    "status": reading.status,
                    "weight_source": reading.weight_source,
                },
            )
        )

    data.measurement_callback = _record_measurement

    def _needs_poll(
        service_info: BluetoothServiceInfoBleak, last_poll: float | None
    ) -> bool:
        # Only poll if hass is running, we need to poll,
        # and we actually have a way to connect to the device
        return (
            hass.state == CoreState.running
            and data.poll_needed(service_info, last_poll)
            and bool(
                async_ble_device_from_address(
                    hass, service_info.device.address, connectable=True
                )
            )
        )

    async def _async_poll(service_info: BluetoothServiceInfoBleak):
        # BluetoothServiceInfoBleak is defined in HA, otherwise would just pass it
        # directly to the OKOK Scale code
        # Make sure the device we have is one that we can connect with
        # in case its coming from a passive scanner
        if service_info.connectable:
            connectable_device = service_info.device
        elif device := async_ble_device_from_address(
            hass, service_info.device.address, True
        ):
            connectable_device = device
        else:
            # We have no bluetooth controller that is in range of
            # the device to poll it
            raise RuntimeError(
                f"No connectable device found for {service_info.device.address}"
            )

        return await data.async_poll(connectable_device, service_info.advertisement)

    coordinator = ActiveBluetoothProcessorCoordinator(
        hass,
        _LOGGER,
        address=address,
        mode=BluetoothScanningMode.PASSIVE,
        update_method=data.update,
        needs_poll_method=_needs_poll,
        poll_method=_async_poll,
        # Listen to all advertisements. If a device can be polled, _needs_poll
        # still verifies that a connectable BLEDevice is available first.
        connectable=False,
    )

    entry.runtime_data = OKOKScaleRuntimeData(
        coordinator=coordinator,
        device_data=data,
        debug_recorder=debug_recorder,
        measurement_store=measurement_store,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        coordinator.async_start()
    )  # Only start after all platforms have had a chance to subscribe

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = DEBUG_PLATFORMS if entry.data.get(CONF_DEBUG_ONLY) else PLATFORMS
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        hass.data.get(DOMAIN, {}).get(DATA_MEASUREMENT_STORES, {}).pop(
            entry.entry_id,
            None,
        )
    return unloaded


def _async_register_measurement_services(hass: HomeAssistant) -> None:
    """Register measurement-management services once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_SERVICES_REGISTERED):
        return

    domain_data[DATA_SERVICES_REGISTERED] = True

    def _get_store(call) -> OKOKScaleMeasurementStore:
        stores = hass.data[DOMAIN][DATA_MEASUREMENT_STORES]
        entry_id = call.data.get("entry_id")
        if entry_id:
            if entry_id not in stores:
                raise HomeAssistantError(
                    f"No MAXXMEE measurement store for entry_id {entry_id}"
                )
            return stores[entry_id]
        if len(stores) == 1:
            return next(iter(stores.values()))
        raise HomeAssistantError("entry_id is required when multiple scales exist")

    async def _add_user(call) -> None:
        await _get_store(call).async_add_user(call.data["name"])

    async def _rename_user(call) -> None:
        store = _get_store(call)
        user = await store.async_rename_user(
            call.data["user_id"],
            call.data["name"],
        )
        if user is None:
            raise HomeAssistantError(f'Unknown user_id {call.data["user_id"]}')

    async def _add_measurement(call) -> None:
        store = _get_store(call)
        user_id = call.data.get("user_id")
        if user_id and not store.user_exists(user_id):
            raise HomeAssistantError(f"Unknown user_id {user_id}")
        await store.async_record_measurement(
            weight_kg=call.data["weight_kg"],
            measured_at=call.data.get("measured_at"),
            user_id=user_id,
            source="manual",
        )

    async def _update_measurement(call) -> None:
        store = _get_store(call)
        user_id = call.data.get("user_id")
        if user_id and not store.user_exists(user_id):
            raise HomeAssistantError(f"Unknown user_id {user_id}")
        measurement = await store.async_update_measurement(
            measurement_id=call.data["measurement_id"],
            weight_kg=call.data.get("weight_kg"),
            user_id=user_id,
            measured_at=call.data.get("measured_at"),
        )
        if measurement is None:
            raise HomeAssistantError(
                f'Unknown measurement_id {call.data["measurement_id"]}'
            )

    async def _delete_measurement(call) -> None:
        store = _get_store(call)
        measurement = await store.async_delete_measurement(
            call.data["measurement_id"]
        )
        if measurement is None:
            raise HomeAssistantError(
                f'Unknown measurement_id {call.data["measurement_id"]}'
            )

    entry_field = vol.Optional("entry_id")
    hass.services.async_register(
        DOMAIN,
        "add_user",
        _add_user,
        schema=vol.Schema({entry_field: str, vol.Required("name"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        "rename_user",
        _rename_user,
        schema=vol.Schema(
            {
                entry_field: str,
                vol.Required("user_id"): str,
                vol.Required("name"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "add_measurement",
        _add_measurement,
        schema=vol.Schema(
            {
                entry_field: str,
                vol.Required("weight_kg"): vol.Coerce(float),
                vol.Optional("user_id"): str,
                vol.Optional("measured_at"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "update_measurement",
        _update_measurement,
        schema=vol.Schema(
            {
                entry_field: str,
                vol.Required("measurement_id"): str,
                vol.Optional("weight_kg"): vol.Coerce(float),
                vol.Optional("user_id"): str,
                vol.Optional("measured_at"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "delete_measurement",
        _delete_measurement,
        schema=vol.Schema(
            {
                entry_field: str,
                vol.Required("measurement_id"): str,
            }
        ),
    )


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    if config_entry.version == 1 and config_entry.minor_version < 2:

        @callback
        def migrate_unique_id(
            entity_entry: entity_registry.RegistryEntry,
        ) -> dict[str, Any]:
            """Migrate the unique ID to a new format."""
            unique_id = entity_entry.unique_id
            if unique_id.endswith("-battery"):
                unique_id += "_percent"
            elif unique_id.endswith("-weight"):
                unique_id = unique_id[:-7] + "-mass"
            return {"new_unique_id": unique_id}

        await entity_registry.async_migrate_entries(
            hass, config_entry.entry_id, migrate_unique_id
        )

        hass.config_entries.async_update_entry(config_entry, version=1, minor_version=2)

    return True
