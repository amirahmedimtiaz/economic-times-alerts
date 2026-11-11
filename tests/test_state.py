from solar_alerts.state import StateStore

import json
import pytest

from solar_alerts.state import StateError


def test_state_baselines_and_persists(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = StateStore.load(path)
    assert not state.initialized
    state.initialize(["articleshow:1", "articleshow:2"])
    state.save()

    loaded = StateStore.load(path)
    assert loaded.initialized
    assert loaded.has_seen("articleshow:1")
    assert not loaded.has_seen("articleshow:3")

    loaded.mark_seen("articleshow:3")
    loaded.save()
    assert StateStore.load(path).has_seen("articleshow:3")


@pytest.mark.parametrize("payload", [[], None, {"initialized": "false", "seen_ids": []}, {"initialized": True, "seen_ids": "articleshow:1"}, {"initialized": True, "seen_ids": [None]}, {"version": 99, "initialized": True, "seen_ids": []}, {"initialized": True}, {"version": True, "seen_ids": []}])
def test_malformed_state_fails_closed(tmp_path, payload):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(StateError):
        StateStore.load(path)


def test_attempt_order_survives_reload_and_clears_delivered_items(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.initialize([])
    state.record_attempt("articleshow:1")
    state.save()
    loaded = StateStore.load(state.path)
    assert loaded.last_attempted["articleshow:1"]
    loaded.mark_seen("articleshow:1")
    assert "articleshow:1" not in loaded.last_attempted


def test_merge_keeps_latest_pending_attempt_and_clears_processed(tmp_path):
    local = StateStore(tmp_path / "state.json")
    local.last_attempted = {"a": "2026-11-11T22:00:00+00:00", "b": "2026-11-11T23:00:00+00:00"}
    other = StateStore(local.path)
    other.last_attempted = {"a": "2026-11-11T21:00:00+00:00", "c": "2026-11-11T23:00:00+00:00"}
    other.seen_ids = {"b"}
    local.merge_from(other)
    assert local.last_attempted == {"a": "2026-11-11T22:00:00+00:00", "c": "2026-11-11T23:00:00+00:00"}
