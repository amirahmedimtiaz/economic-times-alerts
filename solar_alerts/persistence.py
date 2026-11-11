"""Publish delivery state on the latest remote tree without overwriting code."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import subprocess
import tempfile

from .state import StateError, StateStore


LOGGER = logging.getLogger(__name__)


def _git(repo: Path, *args: str, input: str | None = None, env=None, check=True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=input, env=env,
        capture_output=True, text=True,
    )
    if check and result.returncode:
        raise StateError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result


def persist_state(state_path: Path, *, repo_dir: Path | None = None, branch: str = "main", attempts: int = 3) -> bool:
    """Union checkpoints with remote state and push a commit atop remote HEAD.

    A temporary Git index preserves every other file from the current remote
    commit, even if source or state advanced while this poll was running.
    Pushes never force-update the branch; a racing writer causes a fresh merge.
    """
    state_path = state_path.resolve()
    if not state_path.exists():
        LOGGER.info("No state file to persist")
        return False
    local = StateStore.load(state_path)
    repo = Path(_git(repo_dir or state_path.parent, "rev-parse", "--show-toplevel").stdout.strip())
    # Recovery checkpoints may be downloaded outside the repository; their
    # location must never change the production state file's destination.
    relative = "state.json"
    for attempt in range(attempts):
        _git(repo, "fetch", "--no-tags", "origin", branch)
        head = _git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
        tracked = _git(repo, "ls-tree", "--name-only", head, "--", relative).stdout.strip()
        remote = (
            StateStore.from_json(state_path, _git(repo, "show", f"{head}:{relative}").stdout)
            if tracked else StateStore(state_path)
        )
        merged = StateStore(
            path=state_path,
            initialized=local.initialized or remote.initialized,
            seen_ids=local.seen_ids | remote.seen_ids,
            stale_ids=local.stale_ids | remote.stale_ids,
        )
        if tracked and (merged.initialized, merged.seen_ids, merged.stale_ids) == (
            remote.initialized, remote.seen_ids, remote.stale_ids,
        ):
            LOGGER.info("Remote state already contains all checkpoints")
            return False
        merged._touch()
        blob = _git(repo, "hash-object", "-w", "--stdin", input=merged.to_json()).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="et-alert-state-") as directory:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
            _git(repo, "read-tree", head, env=env)
            _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{relative}", env=env)
            tree = _git(repo, "write-tree", env=env).stdout.strip()
        commit = _git(repo, "commit-tree", tree, "-p", head, "-m", "chore: update alert state").stdout.strip()
        pushed = _git(repo, "push", "origin", f"{commit}:refs/heads/{branch}", check=False)
        if pushed.returncode == 0:
            LOGGER.info("Persisted state: seen=%d stale=%d commit=%s", len(merged.seen_ids), len(merged.stale_ids), commit[:12])
            return True
        if not any(reason in pushed.stderr for reason in ("[rejected]", "non-fast-forward", "fetch first")):
            raise StateError(f"Could not push alert state: {pushed.stderr.strip()}")
        LOGGER.warning("State push raced another update; merging again (%d/%d)", attempt + 1, attempts)
    raise StateError("Could not persist alert state after concurrent updates; the local checkpoint must be recovered before polling again")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_file", type=Path, nargs="?", default=Path("state.json"))
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        persist_state(args.state_file, repo_dir=Path.cwd(), branch=args.branch)
    except (StateError, OSError) as exc:
        LOGGER.error("State persistence failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
