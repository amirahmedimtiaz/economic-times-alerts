from solar_alerts.state import StateStore


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
