"""Tests for the local BLE debug protocol helper."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "ble_debug_protocol.py"
)

spec = importlib.util.spec_from_file_location("ble_debug_protocol", SCRIPT_PATH)
ble_debug_protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ble_debug_protocol
assert spec.loader is not None
spec.loader.exec_module(ble_debug_protocol)


class BleDebugProtocolTest(unittest.TestCase):
    """Test protocol serialization helpers."""

    def test_rebuilds_manufacturer_ad_raw(self) -> None:
        self.assertEqual(
            ble_debug_protocol.manufacturer_ad_raw_hex(
                76, bytes.fromhex("12025002")
            ),
            "07ff4c0012025002",
        )

    def test_classifies_maxxmee_presence_packet(self) -> None:
        classifications = ble_debug_protocol.classify_manufacturer_data(
            {76: bytes.fromhex("12025002")}
        )

        self.assertEqual(classifications[0]["type"], "maxxmee_presence")
        self.assertFalse(classifications[0]["contains_weight"])

    def test_classifies_stable_maxxmee_weight_packet(self) -> None:
        raw_value = bytes.fromhex("C0EC1ED21392000225D914000098FE")
        manufacturer_id = int.from_bytes(raw_value[:2], "little")
        classifications = ble_debug_protocol.classify_manufacturer_data(
            {manufacturer_id: raw_value[2:]}
        )

        self.assertEqual(classifications[0]["type"], "maxxmee_c0_weight")
        self.assertTrue(classifications[0]["stable"])
        self.assertEqual(classifications[0]["status"], 0x25)
        self.assertAlmostEqual(classifications[0]["weight_kg"], 78.90)

    def test_event_target_matching_uses_payloads_and_names(self) -> None:
        event = {
            "address": "C0:8F:40:F4:36:48",
            "name": "C0:8F:40:F4:36:48",
            "local_name": None,
            "manufacturer_data": ble_debug_protocol.manufacturer_entries(
                {76: bytes.fromhex("12025002")}
            ),
            "classifications": [
                {
                    "type": "maxxmee_presence",
                    "contains_weight": False,
                }
            ],
        }

        self.assertTrue(
            ble_debug_protocol.event_matches_targets(event, ["C0:8F:40"])
        )
        self.assertTrue(
            ble_debug_protocol.event_matches_targets(event, ["12025002"])
        )
        self.assertFalse(
            ble_debug_protocol.event_matches_targets(event, ["nope"])
        )
        self.assertTrue(
            ble_debug_protocol.event_matches_targets(event, ["maxxmee"])
        )

    def test_device_stats_summarizes_tx_power_values(self) -> None:
        stats = ble_debug_protocol.DeviceStats("device-address")
        stats.record(
            {
                "timestamp": "2026-06-28T08:57:11.511+00:00",
                "target_match": False,
                "name": "test",
                "local_name": None,
                "rssi": -70,
                "tx_power": 12,
                "connectable": True,
                "manufacturer_data": [],
                "service_data": [],
                "classifications": [],
            }
        )

        summary = stats.as_dict()

        self.assertEqual(summary["tx_power_values"], {"12": 1})

    def test_rebuild_protocol_writes_summary_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protocol_path = Path(temp_dir) / "protocol.jsonl"
            records = [
                {
                    "type": "session_start",
                    "timestamp": "2026-06-28T08:57:03.447+00:00",
                    "duration_seconds": 120,
                    "targets": ["C0:8F:40:F4:36:48"],
                    "connect_check": True,
                    "connect_during_scan": False,
                },
                {
                    "type": "advertisement",
                    "timestamp": "2026-06-28T08:59:01.792+00:00",
                    "address": "E137B2F1-0235-2E2A-29E5-B010F5499DB1",
                    "name": "tzc",
                    "local_name": "tzc",
                    "rssi": -53,
                    "tx_power": None,
                    "connectable": None,
                    "manufacturer_data": ble_debug_protocol.manufacturer_entries(
                        {
                            0x06C0: bytes.fromhex(
                                "1e8c1392000225d914000098fe"
                            )
                        }
                    ),
                    "service_data": [],
                    "classifications": [
                        {
                            "type": "maxxmee_c0_weight",
                            "stable": True,
                            "weight_kg": 78.2,
                        }
                    ],
                    "target_match": False,
                },
                {
                    "type": "session_end",
                    "timestamp": "2026-06-28T08:59:03.449+00:00",
                },
            ]
            protocol_path.write_text(
                "\n".join(
                    ble_debug_protocol.json.dumps(record) for record in records
                )
                + "\n",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                result = ble_debug_protocol.rebuild_protocol(protocol_path)

            self.assertEqual(result, 0)

            summary_path = Path(temp_dir) / "summary.json"
            report_path = Path(temp_dir) / "report.md"
            self.assertTrue(summary_path.is_file())
            self.assertTrue(report_path.is_file())
            self.assertIn("maxxmee_c0_weight", report_path.read_text())


if __name__ == "__main__":
    unittest.main()
