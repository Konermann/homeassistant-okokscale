"""Tests for pure OKOK Scale packet parsers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PARSER_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "okokscale"
    / "parser.py"
)

spec = importlib.util.spec_from_file_location("okokscale_parser", PARSER_PATH)
parser = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = parser
assert spec.loader is not None
spec.loader.exec_module(parser)


class MaxxmeeC0ParserTest(unittest.TestCase):
    """Test MAXXMEE C0 advertisement parsing."""

    def test_unstable_raw_packet_is_ignored(self) -> None:
        raw_value = bytes.fromhex("C0681E4B0000000224D914000098FE")

        self.assertIsNone(parser.parse_maxxmee_c0_raw_value(raw_value))

        reading = parser.decode_maxxmee_c0_raw_value(raw_value)
        self.assertIsNotNone(reading)
        self.assertFalse(reading.final)
        self.assertEqual(reading.status, 0x24)
        self.assertAlmostEqual(reading.weight, 77.55)

    def test_stable_raw_packets_decode_weight(self) -> None:
        raw_values = (
            bytes.fromhex("C0B91ED21392000225D914000098FE"),
            bytes.fromhex("C0EC1ED21392000225D914000098FE"),
        )

        for raw_value in raw_values:
            with self.subTest(raw_value=raw_value.hex()):
                self.assertAlmostEqual(
                    parser.parse_maxxmee_c0_raw_value(raw_value), 78.90
                )

                reading = parser.decode_maxxmee_c0_raw_value(raw_value)
                self.assertIsNotNone(reading)
                self.assertTrue(reading.final)
                self.assertEqual(reading.status, 0x25)
                self.assertEqual(reading.raw_weight, 7890)
                self.assertAlmostEqual(reading.weight, 78.90)

    def test_home_assistant_manufacturer_payload_rebuilds_raw_value(self) -> None:
        raw_value = bytes.fromhex("C0EC1ED21392000225D914000098FE")
        manufacturer_id = int.from_bytes(raw_value[:2], "little")
        payload = raw_value[2:]

        self.assertEqual(manufacturer_id, 0xECC0)
        self.assertEqual(
            parser.maxxmee_c0_raw_from_manufacturer_data(manufacturer_id, payload),
            raw_value,
        )
        self.assertAlmostEqual(parser.parse_maxxmee_c0_raw_value(raw_value), 78.90)

    def test_short_presence_advertisement_is_not_weight_data(self) -> None:
        manufacturer_data = {76: bytes.fromhex("12025002")}

        self.assertTrue(
            parser.is_maxxmee_presence_manufacturer_data(manufacturer_data)
        )
        self.assertIsNone(
            parser.maxxmee_c0_raw_from_manufacturer_data(
                76, manufacturer_data[76]
            )
        )
        self.assertIsNone(
            parser.decode_maxxmee_c0_raw_value(bytes.fromhex("4c0012025002"))
        )

    def test_legacy_vc0_payload_decoder_still_parses_kg(self) -> None:
        payload = bytes.fromhex("1ED21392000225D914000098FE")

        reading = parser.decode_vc0_payload(payload)

        self.assertIsNotNone(reading)
        self.assertTrue(reading.final)
        self.assertEqual(reading.unit, parser.MASS_UNIT_KILOGRAMS)
        self.assertAlmostEqual(reading.weight, 78.90)


if __name__ == "__main__":
    unittest.main()
