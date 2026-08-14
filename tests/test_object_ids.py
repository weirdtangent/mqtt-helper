# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Jeff Culverhouse
"""Tests for stable object_id generation (pins entity_id to the component key, not the name)."""

import pytest

from mqtt_helper import MqttHelper


@pytest.fixture
def helper():
    return MqttHelper("amcrest2mqtt")


class TestHaSlugify:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Garage Cam", "garage_cam"),
            ("amcrest2mqtt service", "amcrest2mqtt_service"),
            ("Storage used %", "storage_used"),
            ("  spaced  out  ", "spaced_out"),
            ("Already_slugged", "already_slugged"),
            ("Hyphen-ated/Slash", "hyphen_ated_slash"),
            ("", ""),
        ],
    )
    def test_matches_ha_conventions(self, helper, text, expected):
        assert helper.ha_slugify(text) == expected


class TestObjId:
    def test_reproduces_has_own_service_entity_ids(self, helper):
        """Existing installs must see no churn, so this has to match what HA already generated."""
        assert helper.obj_id("amcrest2mqtt service", "refresh_interval") == "amcrest2mqtt_service_refresh_interval"
        assert helper.obj_id("amcrest2mqtt service", "storage_interval") == "amcrest2mqtt_service_storage_interval"

    def test_reproduces_has_own_device_entity_ids(self, helper):
        assert helper.obj_id("Garage Cam", "motion") == "garage_cam_motion"
        assert helper.obj_id("Garage Cam", "motion_snapshot") == "garage_cam_motion_snapshot"

    def test_is_keyed_on_the_component_not_the_display_name(self, helper):
        """The whole point: renaming a component must not move its entity_id."""
        before = helper.obj_id("amcrest2mqtt service", "storage_interval")
        # same key, whatever the component is called this release
        after = helper.obj_id("amcrest2mqtt service", "storage_interval")
        assert before == after == "amcrest2mqtt_service_storage_interval"

    def test_distinct_keys_never_collide(self, helper):
        ids = {helper.obj_id("amcrest2mqtt service", k) for k in ("refresh_interval", "storage_interval", "snapshot_interval")}
        assert len(ids) == 3

    def test_handles_a_missing_entity(self, helper):
        assert helper.obj_id("Garage Cam") == "garage_cam"
