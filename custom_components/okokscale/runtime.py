"""Runtime data for the OKOK Scale integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.bluetooth.active_update_processor import (
    ActiveBluetoothProcessorCoordinator,
)

from .debug import OKOKScaleDebugRecorder
from .okokscale import OKOKScaleBluetoothDeviceData


@dataclass
class OKOKScaleRuntimeData:
    """Runtime objects shared by OKOK Scale platforms."""

    coordinator: ActiveBluetoothProcessorCoordinator
    device_data: OKOKScaleBluetoothDeviceData
    debug_recorder: OKOKScaleDebugRecorder
