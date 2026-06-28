#!/usr/bin/env python3
"""Capture a local BLE debug protocol for OKOK/MAXXMEE scale analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MAXXMEE_PRESENCE_MANUFACTURER_ID = 0x004C
MAXXMEE_PRESENCE_PAYLOAD = bytes.fromhex("12025002")
MAXXMEE_C0_MARKER = b"\x00\x02"
MAXXMEE_C0_TRAILER = bytes.fromhex("D914000098FE")


def utc_now_iso() -> str:
    """Return a compact UTC timestamp for protocol records."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def hex_bytes(value: Optional[bytes]) -> Optional[str]:
    """Return a lowercase hex string for bytes."""
    return value.hex() if value is not None else None


def manufacturer_ad_raw_hex(manufacturer_id: int, payload: bytes) -> str:
    """Rebuild the manufacturer-specific AD structure as hex."""
    body = bytes([0xFF]) + manufacturer_id.to_bytes(2, "little") + payload
    return (bytes([len(body)]) + body).hex()


def manufacturer_entries(manufacturer_data: Dict[int, bytes]) -> List[Dict[str, Any]]:
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


def service_data_entries(service_data: Dict[str, bytes]) -> List[Dict[str, str]]:
    """Serialize service data into stable JSON objects."""
    return [
        {"uuid": uuid, "data": payload.hex()}
        for uuid, payload in sorted(service_data.items())
    ]


def decode_maxxmee_c0_raw(raw_value: bytes) -> Optional[Dict[str, Any]]:
    """Decode the known MAXXMEE C0 raw manufacturer packet."""
    if len(raw_value) != 15:
        return None
    if raw_value[0] != 0xC0:
        return None
    if raw_value[6:8] != MAXXMEE_C0_MARKER:
        return None
    if raw_value[9:] != MAXXMEE_C0_TRAILER:
        return None

    raw_weight = int.from_bytes(raw_value[2:4], "big")
    if raw_weight == 0:
        return None

    status = raw_value[8]
    return {
        "type": "maxxmee_c0_weight",
        "stable": bool(status & 0x01),
        "status": status,
        "status_hex": f"0x{status:02x}",
        "weight_kg": raw_weight / 100.0,
        "raw_weight": raw_weight,
        "raw": raw_value.hex(),
    }


def decode_legacy_vc0_payload(payload: bytes) -> Optional[Dict[str, Any]]:
    """Decode the legacy VC0 payload shape for protocol annotation."""
    if len(payload) != 13:
        return None

    raw_weight = int.from_bytes(payload[0:2], "big")
    if raw_weight == 0:
        return None

    status = payload[6]
    unit_bits = (status >> 3) & 0x03
    if unit_bits == 0:
        weight = raw_weight / 100.0
        unit = "kg"
    elif unit_bits == 2:
        weight = raw_weight / 10.0
        unit = "lb"
    elif unit_bits == 3:
        weight = payload[0] * 14 + payload[1] / 10.0
        unit = "lb"
    else:
        return None

    return {
        "type": "legacy_vc0_weight",
        "stable": bool(status & 0x01),
        "status": status,
        "status_hex": f"0x{status:02x}",
        "weight": weight,
        "unit": unit,
        "raw_weight": raw_weight,
    }


def classify_manufacturer_data(
    manufacturer_data: Dict[int, bytes],
) -> List[Dict[str, Any]]:
    """Identify known OKOK/MAXXMEE advertisement shapes."""
    classifications: List[Dict[str, Any]] = []

    if (
        manufacturer_data.get(MAXXMEE_PRESENCE_MANUFACTURER_ID)
        == MAXXMEE_PRESENCE_PAYLOAD
    ):
        classifications.append(
            {
                "type": "maxxmee_presence",
                "manufacturer_id": MAXXMEE_PRESENCE_MANUFACTURER_ID,
                "manufacturer_id_hex": "0x004c",
                "payload": MAXXMEE_PRESENCE_PAYLOAD.hex(),
                "contains_weight": False,
            }
        )

    for manufacturer_id, payload in manufacturer_data.items():
        if manufacturer_id & 0xFF != 0xC0:
            continue

        raw_value = manufacturer_id.to_bytes(2, "little") + payload
        maxxmee = decode_maxxmee_c0_raw(raw_value)
        if maxxmee is not None:
            maxxmee["manufacturer_id"] = manufacturer_id
            maxxmee["manufacturer_id_hex"] = f"0x{manufacturer_id:04x}"
            classifications.append(maxxmee)
            continue

        vc0 = decode_legacy_vc0_payload(payload)
        if vc0 is not None:
            vc0["manufacturer_id"] = manufacturer_id
            vc0["manufacturer_id_hex"] = f"0x{manufacturer_id:04x}"
            classifications.append(vc0)

    return classifications


def text_matches_targets(value: Optional[str], targets: Iterable[str]) -> bool:
    """Return true if value contains any target token."""
    if not value:
        return False
    haystack = value.lower()
    return any(target.lower() in haystack for target in targets if target)


def event_matches_targets(event: Dict[str, Any], targets: Iterable[str]) -> bool:
    """Return true if an advertisement event matches the requested target."""
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
    for entry in event.get("classifications", []):
        searchable.extend(str(value) for value in entry.values())
    return any(text_matches_targets(value, target_list) for value in searchable)


@dataclass
class DeviceStats:
    """Aggregated observations for one BLE address."""

    address: str
    names: Counter = field(default_factory=Counter)
    local_names: Counter = field(default_factory=Counter)
    rssi_values: List[int] = field(default_factory=list)
    tx_power_values: Counter = field(default_factory=Counter)
    connectable_values: Counter = field(default_factory=Counter)
    manufacturer_values: Counter = field(default_factory=Counter)
    service_values: Counter = field(default_factory=Counter)
    classifications: Counter = field(default_factory=Counter)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    advertisement_count: int = 0
    target_match: bool = False

    def record(self, event: Dict[str, Any]) -> None:
        """Record an advertisement event."""
        self.advertisement_count += 1
        self.first_seen = self.first_seen or event["timestamp"]
        self.last_seen = event["timestamp"]
        self.target_match = self.target_match or bool(event.get("target_match"))

        if event.get("name"):
            self.names[event["name"]] += 1
        if event.get("local_name"):
            self.local_names[event["local_name"]] += 1
        if isinstance(event.get("rssi"), int):
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

    def as_dict(self) -> Dict[str, Any]:
        """Return JSON-serializable aggregate stats."""
        rssi_avg = (
            round(statistics.fmean(self.rssi_values), 1)
            if self.rssi_values
            else None
        )
        return {
            "address": self.address,
            "target_match": self.target_match,
            "advertisement_count": self.advertisement_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "names": dict(self.names.most_common()),
            "local_names": dict(self.local_names.most_common()),
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
            "known_scale": bool(self.classifications),
        }


class ProtocolRecorder:
    """Write raw protocol records and maintain summary state."""

    def __init__(
        self,
        protocol_path: Path,
        started_monotonic: float,
        targets: List[str],
    ) -> None:
        self.protocol_path = protocol_path
        self.started_monotonic = started_monotonic
        self.targets = targets
        self.devices: Dict[str, DeviceStats] = {}
        self.device_objects: Dict[str, Any] = {}
        self.target_addresses: Counter = Counter()
        self.known_scale_addresses: Counter = Counter()
        self.connection_attempts: List[Dict[str, Any]] = []
        self._protocol_file = protocol_path.open("w", encoding="utf-8")

    def close(self) -> None:
        """Close the protocol file."""
        self._protocol_file.close()

    def write_record(self, record: Dict[str, Any]) -> None:
        """Append a JSONL protocol record."""
        self._protocol_file.write(json.dumps(record, sort_keys=True) + "\n")
        self._protocol_file.flush()

    def record_advertisement(self, device: Any, advertisement_data: Any) -> None:
        """Serialize and store one advertisement callback."""
        manufacturer_data = dict(getattr(advertisement_data, "manufacturer_data", {}))
        service_data = dict(getattr(advertisement_data, "service_data", {}))
        address = getattr(device, "address", None) or "unknown"
        name = getattr(device, "name", None)
        local_name = getattr(advertisement_data, "local_name", None)

        event = {
            "type": "advertisement",
            "timestamp": utc_now_iso(),
            "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "address": address,
            "name": name,
            "local_name": local_name,
            "rssi": getattr(advertisement_data, "rssi", None),
            "tx_power": getattr(advertisement_data, "tx_power", None),
            "connectable": getattr(advertisement_data, "connectable", None),
            "manufacturer_data": manufacturer_entries(manufacturer_data),
            "service_data": service_data_entries(service_data),
            "service_uuids": list(getattr(advertisement_data, "service_uuids", [])),
            "platform_data": [
                repr(value)
                for value in getattr(advertisement_data, "platform_data", ())
            ],
            "classifications": classify_manufacturer_data(manufacturer_data),
        }
        event["target_match"] = event_matches_targets(event, self.targets)

        self.write_record(event)
        self.devices.setdefault(address, DeviceStats(address)).record(event)
        self.device_objects[address] = device
        if event["target_match"]:
            self.target_addresses[address] += 1
        if event["classifications"]:
            self.known_scale_addresses[address] += 1

    def record_connection_attempt(self, result: Dict[str, Any]) -> None:
        """Store one connection attempt result."""
        self.connection_attempts.append(result)
        self.write_record(result)

    def best_target_device(self) -> Optional[Any]:
        """Return the most frequently seen target BLEDevice object."""
        addresses = self.target_addresses or self.known_scale_addresses
        if not addresses:
            return None
        address = addresses.most_common(1)[0][0]
        return self.device_objects.get(address)

    def summary(
        self,
        started_at: str,
        finished_at: str,
        args: argparse.Namespace,
    ) -> Dict[str, Any]:
        """Return the final protocol summary."""
        devices = [stats.as_dict() for stats in self.devices.values()]
        devices.sort(
            key=lambda item: (
                not item["target_match"],
                not item["known_scale"],
                -item["advertisement_count"],
                item["address"],
            )
        )
        return {
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": args.duration,
            "targets": args.target,
            "connect_check_enabled": bool(args.target and args.connect_check),
            "connect_during_scan": args.connect_during_scan,
            "device_count": len(devices),
            "advertisement_count": sum(
                item["advertisement_count"] for item in devices
            ),
            "connection_attempts": self.connection_attempts,
            "devices": devices,
        }


async def attempt_connection(device: Any, timeout: float) -> Dict[str, Any]:
    """Try to establish a BLE connection to a discovered device."""
    from bleak import BleakClient

    address = getattr(device, "address", None)
    name = getattr(device, "name", None)
    started = time.monotonic()
    result: Dict[str, Any] = {
        "type": "connection_attempt",
        "timestamp": utc_now_iso(),
        "address": address,
        "name": name,
        "timeout_seconds": timeout,
        "success": False,
    }

    client = BleakClient(device, timeout=timeout)
    try:
        await client.connect()
        result["success"] = bool(client.is_connected)
        result["connected"] = bool(client.is_connected)
        try:
            get_services = getattr(client, "get_services", None)
            services = await get_services() if get_services else client.services
            result["service_uuids"] = [str(service.uuid) for service in services]
        except Exception as err:  # pragma: no cover - depends on BLE backend
            result["service_error"] = repr(err)
    except Exception as err:  # pragma: no cover - depends on BLE backend
        result["error"] = repr(err)
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if getattr(client, "is_connected", False):
            try:
                await client.disconnect()
                result["disconnect_success"] = True
            except Exception as err:  # pragma: no cover - depends on BLE backend
                result["disconnect_error"] = repr(err)
    return result


async def connection_probe_loop(
    recorder: ProtocolRecorder,
    finished_monotonic: float,
    interval: float,
    timeout: float,
) -> None:
    """Try target connection checks during the scan window."""
    next_attempt = time.monotonic() + interval
    while time.monotonic() < finished_monotonic:
        await asyncio.sleep(max(0.5, next_attempt - time.monotonic()))
        next_attempt += interval
        device = recorder.best_target_device()
        if device is None:
            continue
        result = await attempt_connection(device, timeout)
        result["during_scan"] = True
        recorder.record_connection_attempt(result)


async def run_scan(args: argparse.Namespace) -> int:
    """Run the BLE scan and write protocol files."""
    try:
        from bleak import BleakScanner
    except ImportError:
        print(
            "Missing dependency: bleak. Install it with "
            "`python3 -m pip install bleak`.",
            file=sys.stderr,
        )
        return 2

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"ble-debug-{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    protocol_path = output_dir / "protocol.jsonl"
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"

    started_at = utc_now_iso()
    started_monotonic = time.monotonic()
    finished_monotonic = started_monotonic + args.duration
    recorder = ProtocolRecorder(protocol_path, started_monotonic, args.target)

    start_record = {
        "type": "session_start",
        "timestamp": started_at,
        "duration_seconds": args.duration,
        "targets": args.target,
        "connect_check": bool(args.target and args.connect_check),
        "connect_during_scan": args.connect_during_scan,
    }
    recorder.write_record(start_record)

    scanner = BleakScanner(detection_callback=recorder.record_advertisement)
    probe_task = None

    print(f"Writing BLE debug protocol to {output_dir}")
    print(f"Scanning for {args.duration} seconds...")
    if args.target and args.connect_check:
        print("Connection check enabled for target:", ", ".join(args.target))
    elif args.connect_check:
        print("No target specified, so no connection check will be attempted.")

    try:
        await scanner.start()
        if args.target and args.connect_check and args.connect_during_scan:
            probe_task = asyncio.create_task(
                connection_probe_loop(
                    recorder,
                    finished_monotonic,
                    args.connect_every,
                    args.connect_timeout,
                )
            )

        while time.monotonic() < finished_monotonic:
            await asyncio.sleep(min(5, finished_monotonic - time.monotonic()))
            elapsed = int(time.monotonic() - started_monotonic)
            print(f"  {elapsed:>3}s elapsed, {len(recorder.devices)} devices seen")
    finally:
        if probe_task is not None:
            probe_task.cancel()
            try:
                await probe_task
            except asyncio.CancelledError:
                pass
        await scanner.stop()

    if args.target and args.connect_check:
        device = recorder.best_target_device()
        if device is None:
            recorder.record_connection_attempt(
                {
                    "type": "connection_attempt",
                    "timestamp": utc_now_iso(),
                    "success": False,
                    "error": "No target device was observed during the scan.",
                    "during_scan": False,
                }
            )
        else:
            print("Trying final connection check...")
            result = await attempt_connection(device, args.connect_timeout)
            result["during_scan"] = False
            recorder.record_connection_attempt(result)

    finished_at = utc_now_iso()
    recorder.write_record({"type": "session_end", "timestamp": finished_at})
    summary = recorder.summary(started_at, finished_at, args)
    recorder.close()

    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(render_markdown_report(summary), encoding="utf-8")

    print(f"Protocol: {protocol_path}")
    print(f"Summary:  {summary_path}")
    print(f"Report:   {report_path}")
    return 0


def rebuild_protocol(protocol_path: Path) -> int:
    """Rebuild summary and report files from an existing protocol.jsonl."""
    if not protocol_path.is_file():
        print(f"Protocol file not found: {protocol_path}", file=sys.stderr)
        return 2

    devices: Dict[str, DeviceStats] = {}
    connection_attempts: List[Dict[str, Any]] = []
    start_record: Dict[str, Any] = {}
    end_record: Dict[str, Any] = {}
    advertisement_count = 0

    with protocol_path.open("r", encoding="utf-8") as protocol_file:
        for line_number, line in enumerate(protocol_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as err:
                print(
                    f"Skipping invalid JSON on line {line_number}: {err}",
                    file=sys.stderr,
                )
                continue

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

    started_at = start_record.get("timestamp") or ""
    finished_at = end_record.get("timestamp") or ""
    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": start_record.get("duration_seconds"),
        "targets": start_record.get("targets", []),
        "connect_check_enabled": bool(start_record.get("connect_check")),
        "connect_during_scan": bool(start_record.get("connect_during_scan")),
        "device_count": len(device_summaries),
        "advertisement_count": advertisement_count,
        "connection_attempts": connection_attempts,
        "devices": device_summaries,
        "rebuilt_from": str(protocol_path),
    }

    output_dir = protocol_path.parent
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(render_markdown_report(summary), encoding="utf-8")

    print(f"Rebuilt Summary: {summary_path}")
    print(f"Rebuilt Report:  {report_path}")
    return 0


def render_markdown_report(summary: Dict[str, Any]) -> str:
    """Render a human-readable protocol report."""
    lines = [
        "# BLE Debug Protocol",
        "",
        f"- Started: `{summary['started_at']}`",
        f"- Finished: `{summary['finished_at']}`",
        f"- Duration: `{summary['duration_seconds']}` seconds",
        f"- Targets: `{', '.join(summary['targets']) or 'none'}`",
        f"- Devices seen: `{summary['device_count']}`",
        f"- Advertisements recorded: `{summary['advertisement_count']}`",
        "",
        "## Connection Attempts",
        "",
    ]

    if summary["connection_attempts"]:
        lines.append("| Time | Address | Name | During scan | Success | Error |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for attempt in summary["connection_attempts"]:
            lines.append(
                (
                    "| {timestamp} | {address} | {name} | "
                    "{during} | {success} | {error} |"
                ).format(
                    timestamp=attempt.get("timestamp", ""),
                    address=attempt.get("address", ""),
                    name=attempt.get("name", ""),
                    during=attempt.get("during_scan", False),
                    success=attempt.get("success", False),
                    error=str(attempt.get("error", "")).replace("|", "\\|"),
                )
            )
    else:
        lines.append("No connection attempts were requested or possible.")

    lines.extend(["", "## Devices", ""])
    if not summary["devices"]:
        lines.append("No BLE advertisements were observed.")
        return "\n".join(lines) + "\n"

    for device in summary["devices"]:
        rssi = device["rssi"]
        rssi_summary = (
            f"`{rssi['min']}` / `{rssi['max']}` / "
            f"`{rssi['average']}` / `{rssi['last']}`"
        )
        names_json = json.dumps(device["names"], sort_keys=True)
        local_names_json = json.dumps(device["local_names"], sort_keys=True)
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
                f"- Advertisements: `{device['advertisement_count']}`",
                f"- First seen: `{device['first_seen']}`",
                f"- Last seen: `{device['last_seen']}`",
                f"- Names: `{names_json}`",
                f"- Local names: `{local_names_json}`",
                f"- RSSI min/max/avg/last: {rssi_summary}",
                f"- Connectable values: `{connectable_json}`",
                f"- Classifications: `{classifications_json}`",
                f"- Manufacturer values: `{manufacturer_json}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Scan BLE advertisements for a fixed time and write an OKOK/MAXXMEE "
            "debug protocol."
        )
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=120,
        help="Scan duration in seconds. Default: 120.",
    )
    parser.add_argument(
        "--rebuild-protocol",
        help=(
            "Rebuild summary.json and report.md from an existing protocol.jsonl "
            "without scanning."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="ble_debug_protocols",
        help="Directory where a timestamped protocol folder is created.",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help=(
            "Target substring to match against address, name, manufacturer id, "
            "or payload. Can be passed multiple times."
        ),
    )
    parser.add_argument(
        "--no-connect-check",
        dest="connect_check",
        action="store_false",
        default=True,
        help="Disable the final connection attempt for matched targets.",
    )
    parser.add_argument(
        "--connect-during-scan",
        action="store_true",
        help="Also try repeated target connections while scanning.",
    )
    parser.add_argument(
        "--connect-every",
        type=float,
        default=30.0,
        help="Seconds between connection attempts during scan. Default: 30.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=10.0,
        help="Connection timeout in seconds. Default: 10.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Command line entry point."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.rebuild_protocol:
        return rebuild_protocol(Path(args.rebuild_protocol))
    if args.duration <= 0:
        parser.error("--duration must be greater than 0")
    if args.connect_every <= 0:
        parser.error("--connect-every must be greater than 0")
    if args.connect_timeout <= 0:
        parser.error("--connect-timeout must be greater than 0")
    return asyncio.run(run_scan(args))


if __name__ == "__main__":
    raise SystemExit(main())
