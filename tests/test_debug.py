"""Tests for Home Assistant BLE debug protocol helpers."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "okokscale"

custom_components = sys.modules.setdefault(
    "custom_components", types.ModuleType("custom_components")
)
custom_components.__path__ = [str(ROOT / "custom_components")]
okokscale_package = sys.modules.setdefault(
    "custom_components.okokscale",
    types.ModuleType("custom_components.okokscale"),
)
okokscale_package.__path__ = [str(PACKAGE_ROOT)]

parser_spec = importlib.util.spec_from_file_location(
    "custom_components.okokscale.parser",
    PACKAGE_ROOT / "parser.py",
)
parser = importlib.util.module_from_spec(parser_spec)
sys.modules[parser_spec.name] = parser
assert parser_spec.loader is not None
parser_spec.loader.exec_module(parser)

debug_spec = importlib.util.spec_from_file_location(
    "custom_components.okokscale.debug",
    PACKAGE_ROOT / "debug.py",
)
debug = importlib.util.module_from_spec(debug_spec)
sys.modules[debug_spec.name] = debug
assert debug_spec.loader is not None
debug_spec.loader.exec_module(debug)


class DummyServiceInfo:
    """Simple stand-in for HA BluetoothServiceInfoBleak."""

    address = "E137B2F1-0235-2E2A-29E5-B010F5499DB1"
    name = "tzc"
    rssi = -55
    source = "adapter-address"
    connectable = False
    tx_power = None
    service_uuids = ["0000fffe-0000-1000-8000-00805f9b34fb"]
    manufacturer_data = {
        0x57C0: bytes.fromhex("1e961392000225d914000098fe")
    }
    service_data = {}
    advertisement = None


class HADebugProtocolTest(unittest.TestCase):
    """Test HA debug protocol helpers."""

    def test_service_info_event_classifies_stable_maxxmee_packet(self) -> None:
        event = debug.bluetooth_service_info_to_event(
            DummyServiceInfo(),
            time.monotonic(),
            ["maxxmee"],
        )

        self.assertTrue(event["target_match"])
        self.assertEqual(event["name"], "tzc")
        self.assertEqual(event["service_uuids"], DummyServiceInfo.service_uuids)
        self.assertEqual(event["classifications"][0]["type"], "maxxmee_c0_weight")
        self.assertTrue(event["classifications"][0]["stable"])
        self.assertAlmostEqual(event["classifications"][0]["weight_kg"], 78.30)

    def test_service_uuid_can_mark_target_match(self) -> None:
        event = debug.bluetooth_service_info_to_event(
            DummyServiceInfo(),
            time.monotonic(),
            ["0000fffe-0000-1000-8000-00805f9b34fb"],
        )

        self.assertTrue(event["target_match"])

    def test_summary_prioritizes_known_scale_device(self) -> None:
        scale_event = debug.bluetooth_service_info_to_event(
            DummyServiceInfo(),
            time.monotonic(),
            ["maxxmee"],
        )
        other_event = dict(scale_event)
        other_event.update(
            {
                "address": "other",
                "name": "other",
                "target_match": False,
                "classifications": [],
            }
        )
        records = [
            {
                "type": "session_start",
                "timestamp": "2026-06-28T09:10:26.196+00:00",
                "duration_seconds": 120,
                "targets": ["maxxmee"],
            },
            other_event,
            scale_event,
            {
                "type": "session_end",
                "timestamp": "2026-06-28T09:12:26.196+00:00",
            },
        ]

        summary = debug.summarize_records(records)

        self.assertEqual(summary["devices"][0]["address"], DummyServiceInfo.address)
        self.assertTrue(summary["devices"][0]["known_scale"])

    def test_connection_candidate_prefers_known_scale_packet(self) -> None:
        scale_event = debug.bluetooth_service_info_to_event(
            DummyServiceInfo(),
            time.monotonic(),
            ["maxxmee"],
        )
        target_event = dict(scale_event)
        target_event.update(
            {
                "address": "target-address",
                "name": "target-name",
                "target_match": True,
                "classifications": [],
            }
        )
        scale_event["target_match"] = False

        candidate = debug.find_best_connection_candidate([scale_event, target_event])

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["address"], DummyServiceInfo.address)
        self.assertEqual(candidate["name"], DummyServiceInfo.name)

    def test_connection_candidate_falls_back_to_target_match(self) -> None:
        event = debug.bluetooth_service_info_to_event(
            DummyServiceInfo(),
            time.monotonic(),
            ["tzc"],
        )
        event["classifications"] = []

        candidate = debug.find_best_connection_candidate([event])

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["address"], DummyServiceInfo.address)

    def test_write_debug_files_creates_download_artifacts(self) -> None:
        event = debug.bluetooth_service_info_to_event(
            DummyServiceInfo(),
            time.monotonic(),
            ["maxxmee"],
        )
        records = [
            {
                "type": "session_start",
                "timestamp": "2026-06-28T09:10:26.196+00:00",
                "duration_seconds": 120,
                "targets": ["maxxmee"],
            },
            event,
            {
                "type": "session_end",
                "timestamp": "2026-06-28T09:12:26.196+00:00",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            debug.write_debug_files(output_dir, records)

            self.assertTrue((output_dir / "protocol.jsonl").is_file())
            self.assertTrue((output_dir / "summary.json").is_file())
            report = (output_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("maxxmee_c0_weight", report)

    def test_report_renders_connection_attempts(self) -> None:
        summary = {
            "started_at": "2026-06-28T09:10:26.196+00:00",
            "finished_at": "2026-06-28T09:12:26.196+00:00",
            "duration_seconds": 120,
            "targets": ["maxxmee"],
            "device_count": 0,
            "advertisement_count": 0,
            "connection_attempts": [
                {
                    "timestamp": "2026-06-28T09:12:26.000+00:00",
                    "address": DummyServiceInfo.address,
                    "name": "tzc",
                    "success": False,
                    "error": "No connectable BLEDevice found",
                }
            ],
            "devices": [],
        }

        report = debug.render_markdown_report(summary)

        self.assertIn("## Connection Attempts", report)
        self.assertIn("No connectable BLEDevice found", report)


if __name__ == "__main__":
    unittest.main()
