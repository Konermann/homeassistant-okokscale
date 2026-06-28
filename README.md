# MAXXMEE BLE Scale for Home Assistant

Home Assistant custom integration for a MAXXMEE Bluetooth body scale that
broadcasts weight values in passive BLE advertisements.

This fork keeps the original OKOK/Chipsea scale support from upstream and adds
support for the MAXXMEE advertisement-only C0 packet variant.

## Origin

Original repository: [rrooggiieerr/homeassistant-okokscale](https://github.com/rrooggiieerr/homeassistant-okokscale)

This repository is a fork of the original **OKOK Scale** integration. The
original integration was built for Bluetooth scales that work with the OKOK
International app and often identify as Chipsea/OKOK devices.

This fork is intentionally named **MAXXMEE BLE Scale for Home Assistant** in the
README and Home Assistant metadata because the fork-specific work is focused on
the MAXXMEE scale variant. The Home Assistant domain remains `okokscale` so
existing integration paths and entities stay compatible.

## Vibecoded Status

The fork-specific MAXXMEE support and BLE debug tooling were developed in an
AI-assisted, vibecoded workflow. Treat this fork as experimental: it is tested
against captured packets and local helper tests, but it has not had the same
real-world coverage as the original upstream integration.

## Supported Scales

Known supported or intentionally preserved variants:

- MAXXMEE Bluetooth scale with passive C0 advertisements
- OKOK/Chipsea variants supported by the original upstream integration
- Tristar WG-2440, as listed by the original project

The MAXXMEE scale observed for this fork appears with Bluetooth names such as
`tzc` or sometimes no useful name. It may also emit a short Apple manufacturer
presence packet from an address-like name such as `C0:8F:40:F4:36:48`.

## MAXXMEE vs Original OKOK Behavior

The important difference is that the MAXXMEE scale is passive advertisement
only. It should not require pairing, and this integration should not need to
connect to it for weight updates.

For the MAXXMEE scale, weight comes from manufacturer data shaped like:

```text
C0 xx ww ww ii ii 00 02 ss D9 14 00 00 98 FE
```

- `ww ww` is the weight as big-endian centi-kg
- `ss` is the status byte
- status `0x25` is treated as stable/final
- status `0x24` is treated as transient and ignored

Example:

```text
C0 EC 1E D2 13 92 00 02 25 D9 14 00 00 98 FE
      1E D2 = 7890 = 78.90 kg
```

Original OKOK/Chipsea variants may use different manufacturer IDs and some
models can be connectable for extra data such as battery or impedance. The
MAXXMEE fork does not expose impedance because the meaning of the candidate
bytes has not been validated.

## Installation

### HACS Custom Repository

Add this fork as a HACS custom repository:

```text
https://github.com/Konermann/homeassistant-okokscale
```

Use category **Integration**, install the integration, then restart Home
Assistant.

### Manual Installation

Copy `custom_components/okokscale` into the `custom_components` directory of
your Home Assistant configuration, then restart Home Assistant.

## Usage

After restart, Home Assistant should discover supported Bluetooth scales. For
the MAXXMEE scale, step on the scale or otherwise wake it so it broadcasts final
stable packets.

Expected MAXXMEE behavior:

- no pairing
- no GATT connection needed for weight
- weight sensor updates from passive BLE advertisements
- unstable packets are ignored until a stable packet arrives

If auto-discovery does not appear, add the integration manually from Home
Assistant. This fork checks both passive and connectable Bluetooth discovery
caches because the MAXXMEE device can emit different advertisement shapes.

Manual setup can also create a debug-only entry. Go to **Settings**,
**Devices & services**, **Add integration**, then choose **MAXXMEE BLE Scale**.
Either select a discovered BLE advertisement or enter a manual debug target such
as `tzc`, `maxxmee`, a MAC/address, or a payload fragment such as `12025002`.
If the target is not recognized as a supported scale yet, the integration adds
only a diagnostic device with the BLE debug protocol button.

## Home Assistant BLE Debug Protocol

The integration adds a diagnostic button entity named **BLE debug protocol** on
both real scale entries and debug-only entries. Press it from the device page to
capture Bluetooth advertisements for 2 minutes from Home Assistant itself. The
protocol also records one connection attempt for the best matching scale
candidate.

When the capture finishes, Home Assistant creates a persistent notification with
links to:

- `report.md`
- `summary.json`
- `protocol.jsonl`

The files are written below `/config/www/okokscale_debug/` and are available in
the UI under `/local/okokscale_debug/...`.

## Local Mac BLE Debug Protocol

For debugging a scale from a MacBook, this repository includes a standalone BLE
capture helper. It scans continuously for 2 minutes by default and writes:

- `protocol.jsonl`: every advertisement and connection attempt as timestamped JSON
- `summary.json`: aggregated devices, RSSI values, payloads, and classifications
- `report.md`: a human-readable protocol summary

Install the local dependency and run a targeted capture:

```bash
python3 -m venv .venv-ble-debug
source .venv-ble-debug/bin/activate
python -m pip install bleak
python scripts/ble_debug_protocol.py --target tzc
```

The `--target` value can be a full address, part of a name, a known payload such
as `12025002`, or a classification such as `maxxmee`. On macOS, CoreBluetooth
usually hides the real BLE MAC address, so `--target tzc` or `--target maxxmee`
is often more useful than a MAC address. When a target is given, the helper
scans for 120 seconds and then tries one BLE connection to the best-matching
device. To only record advertisements, add `--no-connect-check`.

To probe connection attempts during the scan as well:

```bash
python scripts/ble_debug_protocol.py \
  --target tzc \
  --connect-during-scan
```

On macOS, allow Terminal or your editor Bluetooth access if prompted. The output
is written to a timestamped folder below `ble_debug_protocols/`.

If a scan crashes after writing `protocol.jsonl`, rebuild the missing summary
files without rescanning:

```bash
python scripts/ble_debug_protocol.py \
  --rebuild-protocol ble_debug_protocols/<run>/protocol.jsonl
```
