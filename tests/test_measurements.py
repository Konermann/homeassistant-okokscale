"""Tests for measurement history helpers."""

from __future__ import annotations

import importlib.util
import sys
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

const_spec = importlib.util.spec_from_file_location(
    "custom_components.okokscale.const",
    PACKAGE_ROOT / "const.py",
)
const = importlib.util.module_from_spec(const_spec)
sys.modules[const_spec.name] = const
assert const_spec.loader is not None
const_spec.loader.exec_module(const)

spec = importlib.util.spec_from_file_location(
    "custom_components.okokscale.measurements",
    PACKAGE_ROOT / "measurements.py",
)
measurements = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = measurements
assert spec.loader is not None
spec.loader.exec_module(measurements)


class MeasurementHelperTest(unittest.TestCase):
    """Test pure measurement helpers."""

    def test_default_data_has_one_user(self) -> None:
        data = measurements.default_measurement_data()

        self.assertEqual(data["users"][0]["id"], measurements.DEFAULT_USER_ID)
        self.assertEqual(data["users"][0]["name"], "Person 1")

    def test_closest_user_assignment_uses_latest_user_weights(self) -> None:
        data = {
            "users": [
                {"id": "alice", "name": "Alice"},
                {"id": "bob", "name": "Bob"},
            ],
            "measurements": [],
        }
        measurements.add_measurement_to_data(
            data,
            60.0,
            "2026-06-28T07:00:00+00:00",
            user_id="alice",
        )
        measurements.add_measurement_to_data(
            data,
            80.0,
            "2026-06-28T07:05:00+00:00",
            user_id="bob",
        )

        self.assertEqual(measurements.assign_user_for_weight(data, 68.0), "alice")
        self.assertEqual(measurements.assign_user_for_weight(data, 78.0), "bob")

    def test_duplicate_broadcast_is_suppressed(self) -> None:
        data = measurements.default_measurement_data()

        first = measurements.add_measurement_to_data(
            data,
            78.1,
            "2026-06-28T07:00:00+00:00",
        )
        duplicate = measurements.add_measurement_to_data(
            data,
            78.1,
            "2026-06-28T07:00:10+00:00",
        )

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertEqual(len(data["measurements"]), 1)

    def test_update_and_delete_measurement(self) -> None:
        data = measurements.default_measurement_data()
        measurement = measurements.add_measurement_to_data(
            data,
            78.1,
            "2026-06-28T07:00:00+00:00",
        )

        updated = measurements.update_measurement_in_data(
            data,
            measurement["id"],
            weight_kg=79.95,
            measured_at="2026-06-28T18:00:00+00:00",
        )
        deleted = measurements.delete_measurement_from_data(
            data,
            measurement["id"],
        )

        self.assertEqual(updated["weight_kg"], 79.95)
        self.assertEqual(updated["period"], measurements.PERIOD_EVENING)
        self.assertEqual(deleted["id"], measurement["id"])
        self.assertEqual(data["measurements"], [])

    def test_latest_measurement_can_filter_by_day_period(self) -> None:
        data = measurements.default_measurement_data()
        measurements.add_measurement_to_data(
            data,
            78.1,
            "2026-06-28T06:30:00+00:00",
        )
        measurements.add_measurement_to_data(
            data,
            79.0,
            "2026-06-28T12:30:00+00:00",
        )

        latest_morning = measurements.latest_measurement(
            data,
            measurements.DEFAULT_USER_ID,
            measurements.PERIOD_MORNING,
        )
        latest_midday = measurements.latest_measurement(
            data,
            measurements.DEFAULT_USER_ID,
            measurements.PERIOD_MIDDAY,
        )

        self.assertEqual(latest_morning["weight_kg"], 78.1)
        self.assertEqual(latest_midday["weight_kg"], 79.0)

    def test_recent_measurements_returns_latest_first(self) -> None:
        data = measurements.default_measurement_data()
        measurements.add_measurement_to_data(
            data,
            78.1,
            "2026-06-28T06:30:00+00:00",
        )
        latest = measurements.add_measurement_to_data(
            data,
            79.0,
            "2026-06-28T12:30:00+00:00",
        )

        recent = measurements.recent_measurements(
            data,
            measurements.DEFAULT_USER_ID,
            limit=1,
        )

        self.assertEqual(recent[0]["id"], latest["id"])


if __name__ == "__main__":
    unittest.main()
