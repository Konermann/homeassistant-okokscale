"""Pure parsers for OKOK Scale Bluetooth advertisements."""

from __future__ import annotations

from dataclasses import dataclass

C0_MANUFACTURER_ID_LSB = 0xC0

MASS_UNIT_KILOGRAMS = "kg"
MASS_UNIT_POUNDS = "lb"

MAXXMEE_C0_RAW_LENGTH = 15
MAXXMEE_C0_MARKER = b"\x00\x02"
MAXXMEE_C0_TRAILER = bytes.fromhex("D914000098FE")
MAXXMEE_C0_WEIGHT_START = 2
MAXXMEE_C0_WEIGHT_END = 4
MAXXMEE_C0_MARKER_START = 6
MAXXMEE_C0_MARKER_END = 8
MAXXMEE_C0_STATUS = 8
MAXXMEE_C0_TRAILER_START = 9

VC0_PAYLOAD_LENGTH = 13
VC0_WEIGHT_START = 0
VC0_WEIGHT_END = 2
VC0_STATUS = 6


@dataclass(frozen=True)
class ScaleReading:
    """A decoded scale reading."""

    weight: float
    unit: str
    final: bool
    raw_weight: int
    status: int


def is_c0_manufacturer_id(manufacturer_id: int) -> bool:
    """Return true if the manufacturer id has the VC0/C0 low byte."""
    return (manufacturer_id & 0xFF) == C0_MANUFACTURER_ID_LSB


def maxxmee_c0_raw_from_manufacturer_data(
    manufacturer_id: int, payload: bytes
) -> bytes | None:
    """Rebuild BLE Scanner style raw manufacturer data from HA manufacturer data."""
    if not 0 <= manufacturer_id <= 0xFFFF:
        return None
    if not is_c0_manufacturer_id(manufacturer_id):
        return None
    if len(payload) != VC0_PAYLOAD_LENGTH:
        return None
    return manufacturer_id.to_bytes(2, "little") + payload


def decode_maxxmee_c0_raw_value(raw_value: bytes) -> ScaleReading | None:
    """Decode a MAXXMEE C0 raw manufacturer data value."""
    if len(raw_value) != MAXXMEE_C0_RAW_LENGTH:
        return None
    if raw_value[0] != C0_MANUFACTURER_ID_LSB:
        return None
    if (
        raw_value[MAXXMEE_C0_MARKER_START:MAXXMEE_C0_MARKER_END]
        != MAXXMEE_C0_MARKER
    ):
        return None
    if raw_value[MAXXMEE_C0_TRAILER_START:] != MAXXMEE_C0_TRAILER:
        return None

    raw_weight = int.from_bytes(
        raw_value[MAXXMEE_C0_WEIGHT_START:MAXXMEE_C0_WEIGHT_END], "big"
    )
    if raw_weight == 0:
        return None

    status = raw_value[MAXXMEE_C0_STATUS]
    return ScaleReading(
        weight=raw_weight / 100.0,
        unit=MASS_UNIT_KILOGRAMS,
        final=bool(status & 0x01),
        raw_weight=raw_weight,
        status=status,
    )


def parse_maxxmee_c0_raw_value(raw_value: bytes) -> float | None:
    """Parse a stable MAXXMEE C0 raw manufacturer data value."""
    reading = decode_maxxmee_c0_raw_value(raw_value)
    if reading is None or not reading.final:
        return None
    return reading.weight


def decode_vc0_payload(payload: bytes) -> ScaleReading | None:
    """Decode a legacy VC0 payload as delivered by Home Assistant."""
    if len(payload) != VC0_PAYLOAD_LENGTH:
        return None

    raw_weight = int.from_bytes(payload[VC0_WEIGHT_START:VC0_WEIGHT_END], "big")
    if raw_weight == 0:
        return None

    status = payload[VC0_STATUS]
    match (status >> 3) & 0x03:
        case 0:
            weight = raw_weight / 100.0
            unit = MASS_UNIT_KILOGRAMS
        case 2:
            weight = raw_weight / 10.0
            unit = MASS_UNIT_POUNDS
        case 3:
            weight = payload[0] * 14 + payload[1] / 10.0
            unit = MASS_UNIT_POUNDS
        case _:
            return None

    return ScaleReading(
        weight=weight,
        unit=unit,
        final=bool(status & 0x01),
        raw_weight=raw_weight,
        status=status,
    )
