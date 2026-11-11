from io import BytesIO
import zipfile

import pytest
import requests

from solar_alerts.state import StateError, StateStore


class Response:
    def __init__(self, data=None, content=b""):
        self.data = data
        self.content = content

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


def responses(monkeypatch, checkpoint, *, artifact_state="available", persist="failure"):
    def get(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer test"
        if url.endswith("/workflows/renewables-alerts.yml/runs"):
            return Response({"workflow_runs": [{"id": 7, "conclusion": "failure", "head_branch": "main"}]})
        if url.endswith("/runs/7/jobs"):
            return Response({"jobs": [{"steps": [
                {"name": "Poll, summarize, and email new stories", "conclusion": "success"},
                {"name": "Persist deduplication state", "conclusion": persist},
            ]}]})
        if url.endswith("/runs/7/artifacts"):
            artifacts = [] if artifact_state == "missing" else [{
                "id": 17, "name": "alert-state-checkpoint", "expired": artifact_state == "expired",
            }]
            return Response({"artifacts": artifacts})
        if url.endswith("/artifacts/17/zip"):
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("state.json", checkpoint)
            return Response(content=buffer.getvalue())
        raise AssertionError(url)

    monkeypatch.setattr("solar_alerts.recovery.requests.get", get)


def test_checkpoint_is_merged_locally_and_persisted_before_sending(monkeypatch, tmp_path):
    from solar_alerts.recovery import recover_checkpoint

    path = tmp_path / "state.json"
    local = StateStore(path)
    local.initialize(["articleshow:1"])
    local.save()
    checkpoint = StateStore(path)
    checkpoint.initialize(["articleshow:2"])
    responses(monkeypatch, checkpoint.to_json())
    persisted = []

    def persist(state_path, **kwargs):
        persisted.append(StateStore.load(state_path).seen_ids)
        return True

    monkeypatch.setattr("solar_alerts.recovery.persist_state", persist)
    assert recover_checkpoint(path, repository="owner/repo", token="test")
    assert StateStore.load(path).seen_ids == {"articleshow:1", "articleshow:2"}
    assert persisted == [{"articleshow:1", "articleshow:2"}]


@pytest.mark.parametrize("artifact_state", ["missing", "expired", "available"])
def test_unrecoverable_failed_persistence_blocks_next_sender(monkeypatch, tmp_path, artifact_state):
    from solar_alerts.recovery import recover_checkpoint

    path = tmp_path / "state.json"
    state = StateStore(path)
    state.initialize(["articleshow:1"])
    state.save()
    before = path.read_bytes()
    responses(monkeypatch, "invalid JSON", artifact_state=artifact_state)
    with pytest.raises(StateError):
        recover_checkpoint(path, repository="owner/repo", token="test")
    assert path.read_bytes() == before


def test_successful_prior_persistence_requires_no_artifact(monkeypatch, tmp_path):
    from solar_alerts.recovery import recover_checkpoint

    responses(monkeypatch, "", persist="success")
    assert not recover_checkpoint(tmp_path / "state.json", repository="owner/repo", token="test")


def test_failed_recovery_push_blocks_sending(monkeypatch, tmp_path):
    from solar_alerts.recovery import recover_checkpoint

    path = tmp_path / "state.json"
    checkpoint = StateStore(path)
    checkpoint.initialize(["articleshow:2"])
    responses(monkeypatch, checkpoint.to_json())

    def fail(*args, **kwargs):
        raise StateError("Push unavailable")

    monkeypatch.setattr("solar_alerts.recovery.persist_state", fail)
    with pytest.raises(StateError, match="Push unavailable"):
        recover_checkpoint(path, repository="owner/repo", token="test")


def test_recovery_api_failure_blocks_sending(monkeypatch, tmp_path):
    from solar_alerts.recovery import recover_checkpoint

    def fail(*args, **kwargs):
        raise requests.ConnectionError("GitHub unavailable")

    monkeypatch.setattr("solar_alerts.recovery.requests.get", fail)
    with pytest.raises(StateError):
        recover_checkpoint(tmp_path / "state.json", repository="owner/repo", token="test")


def test_failed_recovery_runs_do_not_hide_previous_unpersisted_sender(monkeypatch, tmp_path):
    from solar_alerts.recovery import recover_checkpoint

    def get(url, **kwargs):
        if url.endswith("/workflows/renewables-alerts.yml/runs"):
            return Response({"workflow_runs": [
                {"id": 8, "conclusion": "failure", "head_branch": "main"},
                {"id": 7, "conclusion": "failure", "head_branch": "main"},
            ]})
        if url.endswith("/runs/8/jobs"):
            return Response({"jobs": [{"steps": [{"name": "Poll, summarize, and email new stories", "conclusion": "skipped"}]}]})
        if url.endswith("/runs/7/jobs"):
            return Response({"jobs": [{"steps": [
                {"name": "Poll, summarize, and email new stories", "conclusion": "success"},
                {"name": "Persist deduplication state", "conclusion": "failure"},
            ]}]})
        if url.endswith("/runs/7/artifacts"):
            return Response({"artifacts": []})
        raise AssertionError(url)

    monkeypatch.setattr("solar_alerts.recovery.requests.get", get)
    with pytest.raises(StateError):
        recover_checkpoint(tmp_path / "state.json", repository="owner/repo", token="test")
