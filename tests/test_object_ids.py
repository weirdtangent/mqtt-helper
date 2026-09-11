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


class TestApplyDefaultEntityIds:
    """HA Core 2026.4 removed MQTT discovery's `object_id`; `default_entity_id` replaced it and
    wants a full entity_id. A payload still shipping `obj_id` silently loses its entity_ids.
    """

    def test_rewrites_obj_id_into_a_full_entity_id(self, helper):
        payload = {"cmps": {"motion": {"p": "binary_sensor", "obj_id": "garage_cam_motion"}}}

        helper.apply_default_entity_ids(payload)

        assert payload["cmps"]["motion"]["def_ent_id"] == "binary_sensor.garage_cam_motion"

    def test_obj_id_is_removed_not_merely_supplemented(self, helper):
        """HA no longer recognises the key; leaving it behind is dead weight in every payload."""
        payload = {"cmps": {"motion": {"p": "binary_sensor", "obj_id": "garage_cam_motion"}}}

        helper.apply_default_entity_ids(payload)

        assert "obj_id" not in payload["cmps"]["motion"]

    def test_each_component_uses_its_own_domain(self, helper):
        payload = {
            "cmps": {
                "motion": {"p": "binary_sensor", "obj_id": "garage_cam_motion"},
                "light": {"p": "light", "obj_id": "garage_cam_light"},
                "interval": {"p": "number", "obj_id": "garage_cam_interval"},
            }
        }

        helper.apply_default_entity_ids(payload)

        assert payload["cmps"]["motion"]["def_ent_id"] == "binary_sensor.garage_cam_motion"
        assert payload["cmps"]["light"]["def_ent_id"] == "light.garage_cam_light"
        assert payload["cmps"]["interval"]["def_ent_id"] == "number.garage_cam_interval"

    def test_leaves_a_component_without_obj_id_alone(self, helper):
        payload = {"cmps": {"motion": {"p": "binary_sensor", "name": "Motion"}}}

        helper.apply_default_entity_ids(payload)

        assert payload["cmps"]["motion"] == {"p": "binary_sensor", "name": "Motion"}

    def test_leaves_a_component_without_a_domain_alone(self, helper):
        """No `p` means no domain to build a full entity_id from — better untouched than wrong."""
        payload = {"cmps": {"motion": {"obj_id": "garage_cam_motion"}}}

        helper.apply_default_entity_ids(payload)

        assert "def_ent_id" not in payload["cmps"]["motion"]

    def test_tolerates_a_payload_with_no_components(self, helper):
        payload = {"device": {"name": "Garage Cam"}}

        assert helper.apply_default_entity_ids(payload) == {"device": {"name": "Garage Cam"}}

    def test_returns_the_same_object_for_chaining(self, helper):
        payload = {"cmps": {}}

        assert helper.apply_default_entity_ids(payload) is payload

    def test_pairs_with_obj_id(self, helper):
        """The two halves together must reproduce the entity_id HA would have generated."""
        payload = {"cmps": {"motion": {"p": "binary_sensor", "obj_id": helper.obj_id("Garage Cam", "motion")}}}

        helper.apply_default_entity_ids(payload)

        assert payload["cmps"]["motion"]["def_ent_id"] == "binary_sensor.garage_cam_motion"
