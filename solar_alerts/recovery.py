"""Recover an unpersisted sender checkpoint before another sending run."""
from __future__ import annotations

import argparse
from io import BytesIO
import logging
import os
from pathlib import Path
import zipfile

import requests

from .persistence import persist_state
from .state import StateError, StateStore


LOGGER = logging.getLogger(__name__)
POLL_STEP = "Poll, summarize, and email new stories"
PERSIST_STEP = "Persist deduplication state"
CHECKPOINT_NAME = "alert-state-checkpoint"


def _get(url: str, token: str, **params):
    try:
        response = requests.get(
            url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params=params, timeout=30,
        )
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise StateError(f"Cannot verify previous delivery state: {exc}") from exc


def _items(url: str, token: str, field: str, **params) -> list:
    try:
        data = _get(url, token, **params).json()
    except ValueError as exc:
        raise StateError("GitHub returned invalid checkpoint metadata") from exc
    if not isinstance(data, dict) or not isinstance(data.get(field), list):
        raise StateError(f"GitHub checkpoint metadata is missing {field}")
    return data[field]


def _pending_run(base: str, token: str, branch: str) -> int | None:
    page = 1
    while True:
        runs = _items(
            f"{base}/actions/workflows/renewables-alerts.yml/runs", token, "workflow_runs",
            branch=branch, status="completed", per_page=100, page=page,
        )
        for run in runs:
            if run.get("head_branch") != branch:
                continue
            if run.get("conclusion") == "success":
                return None
            if run.get("conclusion") in {"startup_failure", "skipped"}:
                continue
            jobs = _items(f"{base}/actions/runs/{run['id']}/jobs", token, "jobs", per_page=100)
            steps = [step for job in jobs for step in job.get("steps", [])]
            poll = next((step for step in steps if step.get("name") == POLL_STEP), None)
            if poll is None or poll.get("conclusion") == "skipped":
                continue
            persisted = next((step for step in steps if step.get("name") == PERSIST_STEP), {})
            return None if persisted.get("conclusion") == "success" else int(run["id"])
        if len(runs) < 100:
            return None
        page += 1


def recover_checkpoint(state_path: Path, *, repository: str, token: str, branch: str = "main") -> bool:
    """Stop on uncertain receipts, including a failed/missing recovery artifact.

    Look back to the latest run that attempted the sender, so intervening
    startup, test, or recovery failures cannot hide a required checkpoint.
    Queries are scoped to this workflow and production branch.
    """
    if not repository or not token:
        raise StateError("GITHUB_REPOSITORY and GITHUB_TOKEN are required to verify delivery state")
    base = f"https://api.github.com/repos/{repository}"
    pending = _pending_run(base, token, branch)
    if pending is None:
        LOGGER.info("Previous sender state was persisted; no checkpoint recovery needed")
        return False
    artifacts = _items(f"{base}/actions/runs/{pending}/artifacts", token, "artifacts", per_page=100)
    checkpoint = next((artifact for artifact in artifacts if artifact.get("name") == CHECKPOINT_NAME), None)
    if checkpoint is None or checkpoint.get("expired"):
        raise StateError(f"Run {pending} did not persist delivery state and has no usable checkpoint; sending is blocked")
    archive_bytes = _get(f"{base}/actions/artifacts/{checkpoint['id']}/zip", token).content
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            content = archive.read("state.json").decode("utf-8")
        recovered = StateStore.from_json(state_path, content)
    except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise StateError(f"Cannot read checkpoint for run {pending}; sending is blocked") from exc
    state_path = state_path.resolve()
    local = StateStore.load(state_path)
    local.merge_from(recovered)
    local.save()
    persist_state(state_path, repo_dir=state_path.parent, branch=branch)
    LOGGER.info("Recovered delivery checkpoint for run %d before polling", pending)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_file", type=Path, nargs="?", default=Path("state.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        recover_checkpoint(args.state_file, repository=os.getenv("GITHUB_REPOSITORY", ""), token=os.getenv("GITHUB_TOKEN", ""))
    except (StateError, OSError) as exc:
        LOGGER.error("Checkpoint recovery failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
