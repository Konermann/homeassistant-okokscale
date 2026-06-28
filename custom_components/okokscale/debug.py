"""Bluetooth debug protocol capture for OKOK Scale devices."""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .parser import (
    decode_maxxmee_c0_raw_value,
    decode_vc0_payload,
    is_c0_manufacturer_id,
    is_maxxmee_presence_manufacturer_data,
    maxxmee_c0_raw_from_manufacturer_data,
)

DEBUG_CAPTURE_SECONDS = 120
DEBUG_DIRECTORY = "okokscale_debug"
DEBUG_NOTIFICATION_ID = "okokscale_debug_protocol"

_LOGGER = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Return a compact UTC timestamp for protocol records."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def manufacturer_ad_raw_hex(manufacturer_id: int, payload: bytes) -> str:
    """Rebuild the manufacturer-specific AD structure as hex."""
    body = bytes([0xFF]) + manufacturer_id.to_bytes(2, "little") + payload
    return (bytes([len(body)]) + body).hex()


def manufacturer_entries(
    manufacturer_data: Mapping[int, bytes],
) -> list[dict[str, Any]]:
    """Serialize manufacturer data into stable JSON objects."""
    return [
        {
            "id": manufacturer_id,
            "id_hex": f"0x{manufacturer_id:04x}",
            "data": payload.hex(),
            "ad_raw": manufacturer_ad_raw_hex(manufacturer_id, payload),
        }
        for manufacturer_id, payload in sorted(manufacturer_data.items())
    ]


def service_data_entries(service_data: Mapping[str, bytes]) -> list[dict[str, str]]:
    """Serialize service data into stable JSON objects."""
    return [
        {"uuid": uuid, "data": payload.hex()}
        for uuid, payload in sorted(service_data.items())
    ]


def classify_manufacturer_data(
    manufacturer_data: Mapping[int, bytes],
) -> list[dict[str, Any]]:
    """Identify known OKOK/MAXXMEE advertisement shapes."""
    classifications: list[dict[str, Any]] = []

    if is_maxxmee_presence_manufacturer_data(manufacturer_data):
        classifications.append(
            {
                "type": "maxxmee_presence",
                "manufacturer_id": 0x004C,
                "manufacturer_id_hex": "0x004c",
                "payload": manufacturer_data[0x004C].hex(),
                "contains_weight": False,
            }
        )

    for manufacturer_id, payload in manufacturer_data.items():
        if not is_c0_manufacturer_id(manufacturer_id):
            continue

        raw_value = maxxmee_c0_raw_from_manufacturer_data(
            manufacturer_id, payload
        )
        reading = (
            decode_maxxmee_c0_raw_value(raw_value)
            if raw_value is not None
            else None
        )
        if reading is not None:
            classifications.append(
                {
                    "type": "maxxmee_c0_weight",
                    "manufacturer_id": manufacturer_id,
                    "manufacturer_id_hex": f"0x{manufacturer_id:04x}",
                    "raw": raw_value.hex(),
                    "stable": reading.final,
                    "status": reading.status,
                    "status_hex": f"0x{reading.status:02x}",
                    "weight_kg": reading.weight,
                    "raw_weight": reading.raw_weight,
                }
            )
            continue

        vc0_reading = decode_vc0_payload(payload)
        if vc0_reading is not None:
            classifications.append(
                {
                    "type": "legacy_vc0_weight",
                    "manufacturer_id": manufacturer_id,
                    "manufacturer_id_hex": f"0x{manufacturer_id:04x}",
                    "stable": vc0_reading.final,
                    "status": vc0_reading.status,
                    "status_hex": f"0x{vc0_reading.status:02x}",
                    "weight": vc0_reading.weight,
                    "unit": vc0_reading.unit,
                    "raw_weight": vc0_reading.raw_weight,
                }
            )

    return classifications


def text_matches_targets(value: str | None, targets: Iterable[str]) -> bool:
    """Return true if value contains any target token."""
    if not value:
        return False
    haystack = value.lower()
    return any(target.lower() in haystack for target in targets if target)


def event_matches_targets(event: Mapping[str, Any], targets: Iterable[str]) -> bool:
    """Return true if an advertisement event matches target tokens."""
    target_list = list(targets)
    if not target_list:
        return False

    searchable = [
        event.get("address"),
        event.get("name"),
        event.get("local_name"),
    ]
    for entry in event.get("manufacturer_data", []):
        searchable.extend(
            [
                entry.get("id_hex"),
                str(entry.get("id")),
                entry.get("data"),
                entry.get("ad_raw"),
            ]
        )
    searchable.extend(event.get("service_uuids", []))
    for entry in event.get("service_data", []):
        searchable.extend([entry.get("uuid"), entry.get("data")])
    for entry in event.get("classifications", []):
        searchable.extend(str(value) for value in entry.values())
    return any(text_matches_targets(value, target_list) for value in searchable)


def bluetooth_service_info_to_event(
    service_info: Any,
    started_monotonic: float,
    targets: Iterable[str],
) -> dict[str, Any]:
    """Serialize one Home Assistant Bluetooth service info object."""
    manufacturer_data = dict(getattr(service_info, "manufacturer_data", {}) or {})
    service_data = dict(getattr(service_info, "service_data", {}) or {})
    advertisement = getattr(service_info, "advertisement", None)
    platform_data = (
        getattr(advertisement, "platform_data", ())
        if advertisement is not None
        else ()
    )

    event = {
        "type": "advertisement",
        "timestamp": utc_now_iso(),
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
        "address": getattr(service_info, "address", None),
        "name": getattr(service_info, "name", None),
        "local_name": getattr(service_info, "name", None),
        "rssi": getattr(service_info, "rssi", None),
        "source": getattr(service_info, "source", None),
        "connectable": getattr(service_info, "connectable", None),
        "tx_power": getattr(service_info, "tx_power", None),
        "service_uuids": list(getattr(service_info, "service_uuids", []) or []),
        "manufacturer_data": manufacturer_entries(manufacturer_data),
        "service_data": service_data_entries(service_data),
        "platform_data": [repr(value) for value in platform_data],
        "classifications": classify_manufacturer_data(manufacturer_data),
    }
    event["target_match"] = event_matches_targets(event, targets)
    return event


@dataclass
class DeviceStats:
    """Aggregated observations for one BLE address."""

    address: str
    names: Counter = field(default_factory=Counter)
    rssi_values: list[int] = field(default_factory=list)
    tx_power_values: Counter = field(default_factory=Counter)
    connectable_values: Counter = field(default_factory=Counter)
    manufacturer_values: Counter = field(default_factory=Counter)
    service_values: Counter = field(default_factory=Counter)
    classifications: Counter = field(default_factory=Counter)
    first_seen: str | None = None
    last_seen: str | None = None
    advertisement_count: int = 0
    target_match: bool = False

    def record(self, event: Mapping[str, Any]) -> None:
        """Record an advertisement event."""
        self.advertisement_count += 1
        self.first_seen = self.first_seen or event["timestamp"]
        self.last_seen = event["timestamp"]
        self.target_match = self.target_match or bool(event.get("target_match"))

        if event.get("name"):
            self.names[event["name"]] += 1
        if isinstance(event.get("rssi"), int) and event["rssi"] != 127:
            self.rssi_values.append(event["rssi"])
        if isinstance(event.get("tx_power"), int):
            self.tx_power_values[str(event["tx_power"])] += 1
        self.connectable_values[str(event.get("connectable"))] += 1

        for entry in event.get("manufacturer_data", []):
            value = f'{entry["id_hex"]}:{entry["data"]}'
            self.manufacturer_values[value] += 1
        for entry in event.get("service_data", []):
            value = f'{entry["uuid"]}:{entry["data"]}'
            self.service_values[value] += 1
        for entry in event.get("classifications", []):
            self.classifications[entry["type"]] += 1

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serializable aggregate stats."""
        rssi_avg = (
            round(statistics.fmean(self.rssi_values), 1)
            if self.rssi_values
            else None
        )
        return {
            "address": self.address,
            "target_match": self.target_match,
            "known_scale": bool(self.classifications),
            "advertisement_count": self.advertisement_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "names": dict(self.names.most_common()),
            "rssi": {
                "min": min(self.rssi_values) if self.rssi_values else None,
                "max": max(self.rssi_values) if self.rssi_values else None,
                "average": rssi_avg,
                "last": self.rssi_values[-1] if self.rssi_values else None,
            },
            "tx_power_values": dict(self.tx_power_values.most_common()),
            "connectable_values": dict(self.connectable_values.most_common()),
            "manufacturer_values": dict(self.manufacturer_values.most_common()),
            "service_values": dict(self.service_values.most_common()),
            "classifications": dict(self.classifications.most_common()),
        }


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a summary from protocol records."""
    devices: dict[str, DeviceStats] = {}
    connection_attempts: list[dict[str, Any]] = []
    start_record: dict[str, Any] = {}
    end_record: dict[str, Any] = {}
    advertisement_count = 0

    for record in records:
        record_type = record.get("type")
        if record_type == "session_start":
            start_record = record
        elif record_type == "session_end":
            end_record = record
        elif record_type == "connection_attempt":
            connection_attempts.append(record)
        elif record_type == "advertisement":
            advertisement_count += 1
            address = record.get("address") or "unknown"
            devices.setdefault(address, DeviceStats(address)).record(record)

    device_summaries = [stats.as_dict() for stats in devices.values()]
    device_summaries.sort(
        key=lambda item: (
            not item["target_match"],
            not item["known_scale"],
            -item["advertisement_count"],
            item["address"],
        )
    )

    return {
        "started_at": start_record.get("timestamp"),
        "finished_at": end_record.get("timestamp"),
        "duration_seconds": start_record.get("duration_seconds"),
        "targets": start_record.get("targets", []),
        "device_count": len(device_summaries),
        "advertisement_count": advertisement_count,
        "connection_attempts": connection_attempts,
        "devices": device_summaries,
    }


def find_best_connection_candidate(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, str | None] | None:
    """Find the best target or known-scale address for a connection check."""
    target_addresses: Counter = Counter()
    known_scale_addresses: Counter = Counter()
    names: dict[str, str | None] = {}

    for record in records:
        if record.get("type") != "advertisement":
            continue
        address = record.get("address")
        if not address:
            continue
        names[address] = record.get("name")
        if record.get("target_match"):
            target_addresses[address] += 1
        if record.get("classifications"):
            known_scale_addresses[address] += 1

    addresses = known_scale_addresses or target_addresses
    if not addresses:
        return None

    address = addresses.most_common(1)[0][0]
    return {"address": address, "name": names.get(address)}


def render_markdown_report(summary: Mapping[str, Any]) -> str:
    """Render a human-readable debug report."""
    lines = [
        "# OKOK Scale BLE Debug Protocol",
        "",
        f"- Started: `{summary.get('started_at')}`",
        f"- Finished: `{summary.get('finished_at')}`",
        f"- Duration: `{summary.get('duration_seconds')}` seconds",
        f"- Targets: `{', '.join(summary.get('targets', [])) or 'none'}`",
        f"- Devices seen: `{summary.get('device_count')}`",
        f"- Advertisements recorded: `{summary.get('advertisement_count')}`",
        "",
        "## Connection Attempts",
        "",
    ]

    connection_attempts = summary.get("connection_attempts", [])
    if connection_attempts:
        lines.append("| Time | Address | Name | Success | Error |")
        lines.append("| --- | --- | --- | --- | --- |")
        for attempt in connection_attempts:
            error = str(attempt.get("error", "")).replace("|", "\\|")
            lines.append(
                "| {timestamp} | {address} | {name} | {success} | {error} |".format(
                    timestamp=attempt.get("timestamp", ""),
                    address=attempt.get("address", ""),
                    name=attempt.get("name", ""),
                    success=attempt.get("success", False),
                    error=error,
                )
            )
    else:
        lines.append("No connection attempt was possible.")

    lines.extend(
        [
            "",
            "## Devices",
            "",
        ]
    )

    devices = summary.get("devices", [])
    if not devices:
        lines.append("No BLE advertisements were observed.")
        return "\n".join(lines) + "\n"

    for device in devices:
        rssi = device["rssi"]
        rssi_summary = (
            f"`{rssi['min']}` / `{rssi['max']}` / "
            f"`{rssi['average']}` / `{rssi['last']}`"
        )
        connectable_json = json.dumps(
            device["connectable_values"], sort_keys=True
        )
        classifications_json = json.dumps(
            device["classifications"], sort_keys=True
        )
        manufacturer_json = json.dumps(
            device["manufacturer_values"], sort_keys=True
        )
        lines.extend(
            [
                f"### `{device['address']}`",
                "",
                f"- Target match: `{device['target_match']}`",
                f"- Known scale packet: `{device['known_scale']}`",
                f"- Advertisements: `{device['advertisement_count']}`",
                f"- First seen: `{device['first_seen']}`",
                f"- Last seen: `{device['last_seen']}`",
                f"- Names: `{json.dumps(device['names'], sort_keys=True)}`",
                f"- RSSI min/max/avg/last: {rssi_summary}",
                f"- Connectable values: `{connectable_json}`",
                f"- Classifications: `{classifications_json}`",
                f"- Manufacturer values: `{manufacturer_json}`",
                "",
            ]
        )

    return "\n".join(lines) + "\n"


def write_debug_files(output_dir: Path, records: list[dict[str, Any]]) -> None:
    """Write protocol files to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_records(records)
    report = render_markdown_report(summary)

    (output_dir / "protocol.jsonl").write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def build_notification_message(summary: Mapping[str, Any], base_url: str) -> str:
    """Build a persistent notification message for a completed protocol."""
    known_devices = [
        device for device in summary.get("devices", []) if device["known_scale"]
    ]
    if known_devices:
        scale = known_devices[0]
        scale_summary = (
            f"Detected `{scale['address']}` with "
            f"`{json.dumps(scale['classifications'], sort_keys=True)}`."
        )
    else:
        scale_summary = "No known OKOK/MAXXMEE packets were detected."

    return (
        "OKOK Scale BLE debug protocol finished.\n\n"
        f"{scale_summary}\n\n"
        f"- [Report]({base_url}/report.md)\n"
        f"- [Summary JSON]({base_url}/summary.json)\n"
        f"- [Raw protocol JSONL]({base_url}/protocol.jsonl)"
    )


class OKOKScaleDebugRecorder:
    """Capture Bluetooth advertisements into a downloadable protocol."""

    def __init__(self, duration: int = DEBUG_CAPTURE_SECONDS) -> None:
        """Initialize the debug recorder."""
        self.duration = duration
        self._task: asyncio.Task | None = None
        self._listeners: list[Callable[[], None]] = []
        self.running = False
        self.latest_started_at: str | None = None
        self.latest_finished_at: str | None = None
        self.latest_protocol_url: str | None = None
        self.latest_summary_url: str | None = None
        self.latest_report_url: str | None = None
        self.latest_error: str | None = None
        self.latest_device_count: int | None = None
        self.latest_advertisement_count: int | None = None

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a state-change listener."""
        self._listeners.append(listener)

        def remove_listener() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    def _notify_listeners(self) -> None:
        """Notify listeners that recorder state changed."""
        for listener in list(self._listeners):
            listener()

    def cancel(self) -> None:
        """Cancel a running debug capture."""
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def async_start(self, hass: Any, targets: list[str]) -> None:
        """Start a debug capture in the background."""
        if self.running:
            raise RuntimeError("OKOK Scale BLE debug protocol is already running")

        self.running = True
        self.latest_error = None
        self.latest_started_at = utc_now_iso()
        self.latest_finished_at = None
        self._notify_listeners()
        self._task = hass.async_create_task(self._async_capture(hass, targets))

    async def _async_capture(self, hass: Any, targets: list[str]) -> None:
        """Capture advertisements and write the resulting protocol."""
        from homeassistant.components import persistent_notification
        from homeassistant.components.bluetooth import (
            BluetoothScanningMode,
            async_register_callback,
        )

        cancel_callbacks: list[Callable[[], None]] = []
        records: list[dict[str, Any]] = [
            {
                "type": "session_start",
                "timestamp": self.latest_started_at,
                "duration_seconds": self.duration,
                "targets": targets,
                "matchers": [
                    {"connectable": False},
                    {"connectable": True},
                ],
            }
        ]
        started_monotonic = time.monotonic()

        def _record(service_info: Any, *_args: Any) -> None:
            records.append(
                bluetooth_service_info_to_event(
                    service_info,
                    started_monotonic,
                    targets,
                )
            )

        try:
            for matcher in records[0]["matchers"]:
                cancel_callbacks.append(
                    async_register_callback(
                        hass,
                        _record,
                        matcher,
                        BluetoothScanningMode.PASSIVE,
                    )
                )
            await asyncio.sleep(self.duration)
            while cancel_callbacks:
                cancel_callbacks.pop()()
            records.append(await self._async_connection_attempt(hass, records))
            self.latest_finished_at = utc_now_iso()
            records.append(
                {"type": "session_end", "timestamp": self.latest_finished_at}
            )

            run_id = datetime.now().strftime("ble-debug-%Y%m%d-%H%M%S")
            output_dir = Path(hass.config.path("www", DEBUG_DIRECTORY, run_id))
            await hass.async_add_executor_job(write_debug_files, output_dir, records)

            summary = summarize_records(records)
            self.latest_device_count = summary["device_count"]
            self.latest_advertisement_count = summary["advertisement_count"]
            base_url = f"/local/{DEBUG_DIRECTORY}/{run_id}"
            self.latest_report_url = f"{base_url}/report.md"
            self.latest_summary_url = f"{base_url}/summary.json"
            self.latest_protocol_url = f"{base_url}/protocol.jsonl"

            persistent_notification.async_create(
                hass,
                build_notification_message(summary, base_url),
                title="OKOK Scale BLE debug protocol",
                notification_id=DEBUG_NOTIFICATION_ID,
            )
        except asyncio.CancelledError:
            self.latest_error = "Debug capture was cancelled"
            raise
        except Exception as err:  # pragma: no cover - defensive HA integration path
            self.latest_error = repr(err)
            _LOGGER.exception("Failed to create OKOK Scale BLE debug protocol")
        finally:
            while cancel_callbacks:
                cancel_callbacks.pop()()
            self.running = False
            self.latest_finished_at = self.latest_finished_at or utc_now_iso()
            self._notify_listeners()

    async def _async_connection_attempt(
        self,
        hass: Any,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Try to connect to the best observed scale candidate once."""
        from bleak import BleakClient
        from homeassistant.components.bluetooth import async_ble_device_from_address

        started = time.monotonic()
        result: dict[str, Any] = {
            "type": "connection_attempt",
            "timestamp": utc_now_iso(),
            "success": False,
            "timeout_seconds": 10.0,
        }
        candidate = find_best_connection_candidate(records)
        if candidate is None:
            result["error"] = (
                "No target or known OKOK/MAXXMEE scale was observed during capture"
            )
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return result

        address = candidate["address"]
        result["address"] = address
        result["name"] = candidate["name"]

        ble_device = async_ble_device_from_address(
            hass,
            address,
            connectable=True,
        )
        if ble_device is None:
            result["error"] = "No connectable BLEDevice found for captured address"
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return result

        client = BleakClient(ble_device, timeout=result["timeout_seconds"])
        try:
            await client.connect()
            result["success"] = bool(client.is_connected)
            result["connected"] = bool(client.is_connected)
        except Exception as err:  # pragma: no cover - depends on BLE hardware
            result["error"] = repr(err)
        finally:
            if client.is_connected:
                await client.disconnect()
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)

        return result
