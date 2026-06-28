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

## Measurement History and Users

The integration stores stable weight measurements in Home Assistant storage.
Duplicate broadcasts from the same weighing burst are suppressed.

By default, one user named **Person 1** is created. Additional users can be
added from **Developer tools**, **Actions** by calling:

- `okokscale.add_user`
- `okokscale.rename_user`

When more than one user exists, new measurements are assigned to the user whose
latest stored weight is closest to the new weight. For example, a `68 kg`
measurement is assigned to a user last measured near `60 kg` rather than a user
last measured near `80 kg`.

Wrong measurements can be edited, reassigned, or deleted from **Developer
tools**, **Actions**:

- `okokscale.add_measurement`
- `okokscale.update_measurement`
- `okokscale.delete_measurement`

The latest user measurement sensors expose `measurement_id`, `user_id`,
`measured_at`, and `recent_measurements` attributes. Use those IDs when editing,
reassigning, or deleting a measurement.

Editing and deleting measurements updates this integration's stored history and
current sensor states. Home Assistant's recorder may still retain already
recorded historical sensor states until your recorder retention or purge policy
removes them.

## Per-User Graph Sensors

Each user gets graphable sensors for:

- latest weight
- latest morning weight, from `05:00` to `10:59`
- latest midday weight, from `11:00` to `16:59`
- latest evening weight, from `17:00` to `04:59`

These are ordinary Home Assistant sensors, so their history can be shown in
Lovelace history/statistics graphs.

## Apple Health via iOS Shortcuts

Every stored measurement fires the Home Assistant event:

```text
okokscale_weight_recorded
```

The event data includes `user_id`, `user_name`, `weight_kg`, `measured_at`,
`measurement_id`, and `apple_health_shortcut_text`.

`apple_health_shortcut_text` is formatted as:

```text
Name;78.35;2026-06-28T23:58:08+02:00
```

Home Assistant can send this text to an iPhone notification action. Tapping that
action opens an iOS Shortcut, and the Shortcut writes the weight sample to Apple
Health.

Apple Health is personal to the iPhone/Apple ID. Send a user's weight event only
to that user's phone, otherwise the weight will be imported into the wrong
Health profile.

### 1. Create the iOS Shortcut

Create an iOS Shortcut named:

```text
OKOK Weight to Health
```

If you use a different name, also change the URL in the Home Assistant
automation below.

The Shortcut should do this:

1. Receive **Text** from **Apps and 18 more**.
2. If there is no input: **Ask For Text**.
3. Get text from **Shortcut Input**.
4. Split text by custom separator `;`.
5. Get item at index `2` from **Split Text**. This is the weight.
6. Replace `.` with `,` in the weight text on German/European iPhones.
7. Convert the replaced weight text to **Number**.
8. Get item at index `3` from **Split Text**. This is the timestamp.
9. Get dates from the timestamp text.
10. Log Health Sample:
    - Type: **Weight**
    - Value: the converted number
    - Unit: `kg`
    - Date: the parsed date

On the first real run, iOS should ask whether Shortcuts may write data to
Health. Allow it.

### 2. Create the Home Assistant Automation

Example:

```yaml
alias: Add OKOK weight to Apple Health
triggers:
  - trigger: event
    event_type: okokscale_weight_recorded
    event_data:
      user_id: person_1
actions:
  - action: notify.mobile_app_your_iphone
    data:
      title: New weight
      message: "{{ trigger.event.data.apple_health_shortcut_text }}"
      data:
        actions:
          - action: URI
            title: Add to Apple Health
            activationMode: foreground
            uri: "shortcuts://run-shortcut?name=OKOK%20Weight%20to%20Health&input=text&text={{ trigger.event.data.apple_health_shortcut_text | urlencode }}"
mode: single
```

Change these values:

- `user_id: person_1`: use the `user_id` from the user's weight sensor
  attributes.
- `notify.mobile_app_your_iphone`: use the notify service for the target
  iPhone, for example `notify.mobile_app_ich`. In Home Assistant, check
  **Developer tools**, **Actions**, then search for `notify.mobile_app_`.
- `OKOK%20Weight%20to%20Health`: this must match the Shortcut name. Spaces are
  encoded as `%20`.

Important: iOS does not let a Home Assistant notification silently write to
Apple Health in the background. The notification appears first. Long-press or
expand it, then tap **Add to Apple Health**.

### 3. Test the Shortcut URL

Open this URL in Safari on the iPhone:

```text
shortcuts://run-shortcut?name=OKOK%20Weight%20to%20Health&input=text&text=Henning%3B78.35%3B2026-06-28T23%3A58%3A08%2B02%3A00
```

If the Shortcut imports `78.35 kg` with the supplied date, the Shortcut is
ready. If it imports `7835 kg`, add the **Replace Text** step from `.` to `,`
before converting the weight to a number.

## Home Assistant BLE Debug Protocol

The integration adds two diagnostic button entities on both real scale entries
and debug-only entries.

**BLE debug protocol** captures the broader Bluetooth advertisement stream for
2 minutes from Home Assistant itself. The protocol also records one connection
attempt for the best matching scale candidate.

**BLE focused debug protocol** captures for 1 minute, locks on to the first
matching scale/target address, ignores other devices, skips the connection
attempt, and writes packet-by-packet weight time series data. Use this mode when
you are actively stepping on the scale and want to inspect the short burst of
weight advertisements.

When the capture finishes, Home Assistant creates a persistent notification with
links to:

- `report.md`
- `summary.json`
- `protocol.jsonl`

The files are written below `/config/okokscale_debug/` and are served by the
integration under `/api/okokscale_debug/<run>/...`. The persistent notification
contains direct links to the generated files.

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
