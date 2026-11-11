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
