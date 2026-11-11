import json
from pathlib import Path
import subprocess

from solar_alerts.state import StateStore


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def repository(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
    first = tmp_path / "first"
    subprocess.run(["git", "clone", str(remote), str(first)], check=True, capture_output=True)
    for name in ("first", "second"):
        directory = tmp_path / name
        if name == "second":
            subprocess.run(["git", "clone", str(remote), str(directory)], check=True, capture_output=True)
        git(directory, "config", "user.name", "Test")
        git(directory, "config", "user.email", "test@example.com")
        if name == "first":
            state = StateStore(directory / "state.json")
            state.initialize(["articleshow:1"])
            state.save()
            (directory / "app.py").write_text("version = 1\n")
            git(directory, "add", ".")
            git(directory, "commit", "-m", "Initial")
            git(directory, "push", "origin", "main")
    return remote, first, tmp_path / "second"


def test_persistence_merges_remote_receipts_without_reverting_new_code(tmp_path):
    from solar_alerts.persistence import persist_state

    remote, first, second = repository(tmp_path)
    state = StateStore.load(first / "state.json")
    state.mark_seen("articleshow:3")
    state.mark_stale("articleshow:9")
    state.save()
    state = StateStore.load(second / "state.json")
    state.mark_seen("articleshow:2")
    state.save()
    (second / "app.py").write_text("version = 2\n")
    git(second, "add", ".")
    git(second, "commit", "-m", "Concurrent code and state")
    git(second, "push", "origin", "main")

    assert persist_state(first / "state.json")
    saved = json.loads(git(remote, "show", "main:state.json"))
    assert saved["seen_ids"] == ["articleshow:1", "articleshow:2", "articleshow:3"]
    assert saved["stale_ids"] == ["articleshow:9"]
    assert git(remote, "show", "main:app.py") == "version = 2"
    assert (first / "app.py").read_text() == "version = 1\n"


def test_unchanged_state_does_not_create_commits(tmp_path):
    from solar_alerts.persistence import persist_state

    remote, first, second = repository(tmp_path)
    before = git(remote, "rev-parse", "main")
    assert not persist_state(first / "state.json")
    assert git(remote, "rev-parse", "main") == before


def test_external_checkpoint_recovers_into_repository_state(tmp_path):
    from solar_alerts.persistence import persist_state

    remote, first, second = repository(tmp_path)
    recovery = tmp_path / "downloaded-artifact" / "state.json"
    state = StateStore(recovery)
    state.initialize(["articleshow:7"])
    state.save()
    assert persist_state(recovery, repo_dir=first)
    assert json.loads(git(remote, "show", "main:state.json"))["seen_ids"] == [
        "articleshow:1", "articleshow:7",
    ]
    assert "downloaded-artifact" not in git(remote, "ls-tree", "--name-only", "main")


def test_rejected_push_refetches_and_merges_without_dropping_receipts(tmp_path, monkeypatch):
    from solar_alerts.persistence import persist_state

    remote, first, second = repository(tmp_path)
    state = StateStore.load(first / "state.json")
    state.mark_seen("articleshow:3")
    state.save()
    original = subprocess.run
    pushes = []

    def race(command, **kwargs):
        if command[:4] == ["git", "-C", str(first), "push"]:
            pushes.append(command)
            if len(pushes) == 1:
                concurrent = StateStore.load(second / "state.json")
                concurrent.mark_seen("articleshow:2")
                concurrent.save()
                for args in [("add", "."), ("commit", "-m", "Race"), ("push", "origin", "main")]:
                    original(["git", "-C", str(second), *args], check=True, capture_output=True)
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", race)
    assert persist_state(first / "state.json")
    assert len(pushes) == 2
    assert json.loads(git(remote, "show", "main:state.json"))["seen_ids"] == [
        "articleshow:1", "articleshow:2", "articleshow:3",
    ]
