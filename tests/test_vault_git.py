from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from conftest import run
from satemshi.config import RawConfig, VaultGitConfig
from satemshi.vault import VaultWriter
from satemshi.vault_git import FALLBACK_EMAIL, VaultGit

WHEN = datetime(2026, 8, 4, 14, 30)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch, tmp_path):
    """Keep the machine's own git identity out of these tests.

    Otherwise "the vault has no user.email" is untestable anywhere the
    developer has configured one — which is everywhere.
    """
    missing = str(tmp_path / "no-such-gitconfig")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", missing)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", missing)


@pytest.fixture
def bare_repo(tmp_path) -> Path:
    """A vault that is a git repo but has no identity configured."""
    path = tmp_path / "vault"
    (path / "Daily Notes").mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    return path


@pytest.fixture
def repo(bare_repo: Path) -> Path:
    git(bare_repo, "config", "user.email", "you@example.com")
    git(bare_repo, "config", "user.name", "You")
    return bare_repo


def make(vault: Path, **overrides) -> VaultGit:
    settings = {"enabled": True, "coalesce_seconds": 0, "auto_push": False}
    settings.update(overrides)
    return VaultGit(vault, VaultGitConfig(**settings), now=WHEN)


def capture(vault: Path, name: str = "2026-08-04.md", text: str = "note\n") -> None:
    (vault / "Daily Notes" / name).write_text(text, encoding="utf-8")


# -- the happy path ----------------------------------------------------


def test_commits_the_capture(repo):
    vault_git = make(repo)
    capture(repo)
    vault_git.note_change()

    assert run(vault_git.commit_now()) is True
    committed = git(repo, "show", "--stat", "--format=", "HEAD")
    assert "Daily Notes/2026-08-04.md" in committed


def test_commit_message_names_the_time_and_the_files(repo):
    vault_git = make(repo)
    capture(repo)
    capture(repo, "2026-08-05.md")
    vault_git.note_change()
    run(vault_git.commit_now())

    assert git(repo, "log", "-1", "--format=%s").strip() == (
        "capture 2026-08-04 14:30 (2 files)"
    )
    body = git(repo, "log", "-1", "--format=%b")
    assert "Daily Notes/2026-08-04.md" in body
    assert "Daily Notes/2026-08-05.md" in body


def test_a_path_with_a_space_is_written_plainly(repo):
    """Every path in this vault has one: "Daily Notes/"."""
    vault_git = make(repo)
    capture(repo)
    vault_git.note_change()
    run(vault_git.commit_now())

    assert '"Daily Notes' not in git(repo, "log", "-1", "--format=%b")


def test_a_vault_inside_a_larger_repo_commits_only_the_vault(tmp_path):
    outer = tmp_path / "outer"
    vault = outer / "vault"
    (vault / "Daily Notes").mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(outer)], check=True, capture_output=True
    )
    git(outer, "config", "user.email", "you@example.com")
    git(outer, "config", "user.name", "You")
    (outer / "unrelated.txt").write_text("not mine to commit\n", encoding="utf-8")

    vault_git = make(vault)
    capture(vault)
    vault_git.note_change()

    assert run(vault_git.commit_now()) is True
    committed = git(outer, "show", "--stat", "--format=", "HEAD")
    assert "vault/Daily Notes/2026-08-04.md" in committed
    assert "unrelated.txt" not in committed


def test_several_changes_become_one_commit(repo):
    vault_git = make(repo)
    for day in ("2026-08-04.md", "2026-08-05.md", "2026-08-06.md"):
        capture(repo, day)
        vault_git.note_change()

    assert run(vault_git.commit_now()) is True
    assert len(git(repo, "log", "--format=%H").split()) == 1


def test_nothing_to_commit_is_not_a_commit(repo):
    vault_git = make(repo)
    vault_git.note_change()  # a write that changed nothing on disk

    assert run(vault_git.commit_now()) is False
    assert git(repo, "log", "--format=%H", "--all") == ""


def test_disabled_does_nothing(repo):
    vault_git = make(repo, enabled=False)
    capture(repo)
    vault_git.note_change()

    assert run(vault_git.commit_now()) is False
    assert git(repo, "status", "--porcelain") != ""


# -- the coalescing window ---------------------------------------------


def test_shutdown_commits_without_waiting_for_the_window(repo):
    """An open window must not outlive the process holding it."""

    async def scenario() -> None:
        vault_git = make(repo, coalesce_seconds=3600)
        capture(repo)
        vault_git.note_change()
        await vault_git.aclose()

    run(scenario())

    assert git(repo, "log", "--format=%H").strip() != ""


def test_the_window_commits_on_its_own(repo):
    """No aclose(), no commit_now(): the timer a change arms is enough."""

    async def scenario() -> bool:
        vault_git = make(repo, coalesce_seconds=0)
        capture(repo)
        vault_git.note_change()
        # Yield to the timer task, then to the thread it commits on.
        # "git log" is read-only; polling "git status" here would fight
        # that thread for the index lock.
        for _ in range(200):
            await asyncio.sleep(0.01)
            if git(repo, "log", "--format=%H", "--all").strip():
                return True
        return False

    assert run(scenario()) is True


def test_shutdown_with_nothing_pending_is_quiet(repo):
    run(make(repo).aclose())

    assert git(repo, "log", "--format=%H", "--all") == ""


# -- the things that go wrong ------------------------------------------


def test_a_vault_that_is_not_a_repo_is_not_fatal(tmp_path, caplog):
    vault = tmp_path / "plain-vault"
    (vault / "Daily Notes").mkdir(parents=True)
    vault_git = make(vault)
    capture(vault)
    vault_git.note_change()

    with caplog.at_level(logging.WARNING):
        assert run(vault_git.commit_now()) is False

    assert "not a git repository" in caplog.text


def test_a_standing_misconfiguration_is_logged_once(tmp_path, caplog):
    vault = tmp_path / "plain-vault"
    (vault / "Daily Notes").mkdir(parents=True)
    vault_git = make(vault)

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            capture(vault)
            vault_git.note_change()
            run(vault_git.commit_now())

    assert caplog.text.count("not a git repository") == 1


def test_an_unfinished_merge_is_left_alone(repo, caplog):
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    vault_git = make(repo)
    capture(repo)
    vault_git.note_change()

    with caplog.at_level(logging.WARNING):
        assert run(vault_git.commit_now()) is False

    assert "unfinished merge" in caplog.text
    # The capture is still on disk, waiting to be committed later.
    assert git(repo, "status", "--porcelain") != ""


def test_commits_when_the_vault_has_no_identity(bare_repo):
    vault_git = make(bare_repo)
    capture(bare_repo)
    vault_git.note_change()

    assert run(vault_git.commit_now()) is True
    assert git(bare_repo, "log", "-1", "--format=%ae").strip() == FALLBACK_EMAIL


# -- pushing -----------------------------------------------------------


def test_auto_push_sends_the_commit_to_origin(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))

    vault_git = make(repo, auto_push=True)
    capture(repo)
    vault_git.note_change()

    assert run(vault_git.commit_now()) is True
    assert git(remote, "log", "--format=%s", "main").strip() == (
        "capture 2026-08-04 14:30 (1 file)"
    )


def test_auto_push_without_a_remote_still_commits(repo, caplog):
    vault_git = make(repo, auto_push=True)
    capture(repo)
    vault_git.note_change()

    with caplog.at_level(logging.WARNING):
        assert run(vault_git.commit_now()) is True

    assert "no 'origin' remote" in caplog.text
    assert git(repo, "log", "--format=%H").strip() != ""


# -- the wiring into the write path ------------------------------------


def test_the_writer_reports_only_real_changes(vault):
    from datetime import date

    from conftest import TZ
    from satemshi.models import RawEntry

    changes: list[int] = []
    writer = VaultWriter(vault, RawConfig(), on_change=lambda: changes.append(1))
    entry = RawEntry(
        at=datetime(2026, 8, 4, 12, 5, tzinfo=TZ),
        kind="note",
        title="first capture",
        entry_id="abc",
        fields={},
    )

    assert writer.append(entry) is True
    assert writer.append(entry) is False  # a redelivered webhook event
    assert writer.add_fields(date(2026, 8, 4), "raw-abc", {"where": "home"}) is True

    assert len(changes) == 2  # the write and the added field, not the redelivery
