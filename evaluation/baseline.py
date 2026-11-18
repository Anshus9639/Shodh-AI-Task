"""Materialise the frozen Stage 2 baseline without touching the working tree.

The baseline is read from this repository's own git objects with ``git archive``
(no network, no checkout, no patch). Both revisions then run in subprocesses
that import their own ``src`` tree, so the comparison exercises two independent
implementations over identical bundle bytes.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

BASELINE_TAG = "stage2-baseline"
BASELINE_COMMIT = "4702939"
BASELINE_FEATURES = (
    "no --model flag (the baseline has no model layer; deterministic review is its only mode)",
    "no domain-partitioning switch (partitioning is always on)",
)


class BaselineError(RuntimeError):
    """The baseline revision cannot be materialised or is not the frozen one."""


@dataclass(frozen=True)
class Baseline:
    tag: str
    commit: str
    root: Path
    dependency_lock_sha256: str | None
    dependency_lock_path: str | None
    missing_features: tuple[str, ...]

    @property
    def src(self) -> Path:
        return self.root / "src"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise BaselineError(
            f"git {' '.join(args)} failed with exit {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def resolve_commit(repo: Path, ref: str = BASELINE_TAG) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _lock_hash(repo: Path, commit: str) -> tuple[str | None, str | None]:
    for candidate in ("uv.lock", "pyproject.toml"):
        probe = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{candidate}"],
            check=False,
            capture_output=True,
        )
        if probe.returncode != 0:
            continue
        data = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{candidate}"],
            check=True,
            capture_output=True,
        ).stdout
        return hashlib.sha256(data).hexdigest(), candidate
    return None, None


def prepare(repo: Path, cache_root: Path | None = None) -> Baseline:
    """Extract the frozen baseline commit and return its isolated source root."""
    commit = resolve_commit(repo)
    if not commit.startswith(BASELINE_COMMIT):
        raise BaselineError(
            f"tag {BASELINE_TAG!r} resolves to {commit}, expected {BASELINE_COMMIT}…; "
            "the frozen baseline tag must not move"
        )
    cache = cache_root or Path(tempfile.gettempdir()) / f"verityflow-baseline-{commit[:12]}"
    marker = cache / ".baseline-commit"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == commit:
        lock_sha, lock_path = _lock_hash(repo, commit)
        return Baseline(
            tag=BASELINE_TAG,
            commit=commit,
            root=cache,
            dependency_lock_sha256=lock_sha,
            dependency_lock_path=lock_path,
            missing_features=BASELINE_FEATURES,
        )
    if cache.exists():
        raise BaselineError(
            f"{cache} exists but does not hold baseline {commit}; refusing to overwrite it"
        )
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", commit],
        check=True,
        capture_output=True,
    ).stdout
    staging = cache.with_name(cache.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(staging, filter="data")
    (staging / ".baseline-commit").write_text(commit + "\n", encoding="utf-8")
    staging.replace(cache)
    if not (cache / "src" / "verityflow" / "__init__.py").is_file():
        raise BaselineError(f"baseline archive at {commit} does not contain src/verityflow")
    lock_sha, lock_path = _lock_hash(repo, commit)
    return Baseline(
        tag=BASELINE_TAG,
        commit=commit,
        root=cache,
        dependency_lock_sha256=lock_sha,
        dependency_lock_path=lock_path,
        missing_features=BASELINE_FEATURES,
    )


__all__ = [
    "BASELINE_COMMIT",
    "BASELINE_FEATURES",
    "BASELINE_TAG",
    "Baseline",
    "BaselineError",
    "prepare",
    "resolve_commit",
]
