"""Committing the vault after a capture.

The vault is the database, and until now it lived on one disk with no
history: a bad edit or a dead SSD took the lot. This commits it.

Two rules shape everything here. The first is that a capture must never
fail because of git — the message is already on disk by the time we are
called, and a broken remote or an unfinished rebase is not a reason to
tell the user their note was lost. Every failure below is logged and
swallowed, and the changes simply stay in the working tree for the next
attempt to pick up.

The second is that this runs headless. A daemon has no terminal, so
anything git might stop to ask about — credentials, an SSH passphrase, a
GPG pin — has to be turned into an error instead of a process that hangs
forever holding the index lock.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from .config import VaultGitConfig

logger = logging.getLogger(__name__)

# Local git calls are fast or wedged; a push crosses the network.
_LOCAL_TIMEOUT = 30
_PUSH_TIMEOUT = 120

# Enough of the commit body to tell what a commit was, not so much that a
# 300-photo sweep writes an essay.
_MAX_LISTED_PATHS = 20

# Used only when the vault repo has no identity of its own. Commits have
# to have an author, and a daemon cannot stop to ask for one.
FALLBACK_NAME = "Satemshi"
FALLBACK_EMAIL = "satemshi@localhost"

# States where "git add -A && git commit" would not mean what it says —
# it would finish someone's half-done merge for them.
_IN_PROGRESS = {
    "MERGE_HEAD": "merge",
    "CHERRY_PICK_HEAD": "cherry-pick",
    "REVERT_HEAD": "revert",
    "rebase-merge": "rebase",
    "rebase-apply": "rebase",
}


def _status_paths(raw: str) -> list[str]:
    """The changed paths out of ``git status --porcelain -z``.

    NUL-separated rather than line-separated because the default output
    quotes any path holding a space, and "Daily Notes/" has one — every
    path in the vault would arrive wearing quotation marks.

    A rename entry is followed by a second field holding the old path.
    It is the same change, so it is consumed rather than counted twice.
    """
    fields = [field for field in raw.split("\0") if field]
    paths: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":
            index += 1
        paths.append(path)
    return paths


def _commit_message(when: datetime, paths: Sequence[str]) -> str:
    """Subject plus the paths that changed."""
    plural = "" if len(paths) == 1 else "s"
    subject = f"capture {when:%Y-%m-%d %H:%M} ({len(paths)} file{plural})"
    listed = paths[:_MAX_LISTED_PATHS]
    body = "\n".join(listed)
    if len(paths) > len(listed):
        body += f"\n… and {len(paths) - len(listed)} more"
    return f"{subject}\n\n{body}\n"


class VaultGit:
    """Coalescing auto-commit for the vault working tree.

    Call :meth:`note_change` after every write. The first call arms a
    timer; calls landing inside that window join the same commit. Call
    :meth:`aclose` at shutdown so a pending window is not lost.
    """

    def __init__(
        self,
        vault_path: Path,
        config: VaultGitConfig,
        now: datetime | None = None,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.config = config
        self._now = (lambda: now) if now is not None else datetime.now
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task | None = None
        self._pending = False
        self._identity: tuple[str, ...] | None = None
        self._warned: set[str] = set()

    # -- the write path ------------------------------------------------

    def note_change(self) -> None:
        """Record that the vault changed. Never raises, never blocks."""
        if not self.config.enabled:
            return
        self._pending = True
        if self._timer is not None and not self._timer.done():
            return  # A window is already open; this change joins it.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — a synchronous caller, or a test. The change
            # is remembered; commit_now() will pick it up.
            return
        self._timer = loop.create_task(self._commit_after_window())

    async def _commit_after_window(self) -> None:
        while True:
            await asyncio.sleep(self.config.coalesce_seconds)
            await self.commit_now()
            if not self._pending:
                return  # Nothing arrived while we were committing.

    async def commit_now(self) -> bool:
        """Commit immediately. Returns whether a commit was made."""
        if not self.config.enabled or not self._pending:
            return False
        async with self._lock:
            if not self._pending:
                return False
            # Cleared before the work, so a change arriving mid-commit
            # re-arms rather than being swallowed by this one.
            self._pending = False
            return await asyncio.to_thread(self._commit)

    async def aclose(self) -> None:
        """Commit anything pending, rather than losing the open window."""
        timer, self._timer = self._timer, None
        if timer is not None and not timer.done():
            timer.cancel()
            with suppress(asyncio.CancelledError):
                await timer
        await self.commit_now()

    async def preflight(self) -> None:
        """Say at startup what would otherwise surface at first capture."""
        if not self.config.enabled:
            logger.info("Vault auto-commit is off (vault_git.enabled).")
            return
        if not await asyncio.to_thread(self._is_repo):
            self._warn_repo_missing()
            return
        detail = "committing and pushing" if self.config.auto_push else "committing"
        logger.info(
            "Vault auto-commit is on: %s %s every %ds.",
            detail,
            self.vault_path,
            self.config.coalesce_seconds,
        )

    # -- git -----------------------------------------------------------

    def _commit(self) -> bool:
        """The whole commit, synchronously. Runs off the event loop."""
        if not self._is_repo():
            self._warn_repo_missing()
            return False

        unfinished = self._unfinished_operation()
        if unfinished is not None:
            logger.warning(
                "The vault has an unfinished %s — leaving the working tree "
                "alone. Captures are still being written; they will be "
                "committed once it is resolved.",
                unfinished,
            )
            return False

        # -uall because the default collapses an untracked directory to a
        # single entry, which would report a 40-photo sweep as one file.
        # The "-- ." keeps both calls to the vault directory: a vault that
        # is a subdirectory of some larger repo must not drag the rest of
        # that repo into a capture commit.
        status = self._git(
            "status", "--porcelain", "-z", "--untracked-files=all", "--", "."
        )
        if status is None:
            return False
        paths = _status_paths(status.stdout)
        if not paths:
            return False

        if self._git("add", "-A", "--", ".") is None:
            return False
        message = _commit_message(self._now(), paths)
        if self._git("commit", "-m", message, config=self._commit_config()) is None:
            return False
        logger.info("Committed %d change(s) in the vault.", len(paths))

        if self.config.auto_push:
            self._push()
        return True

    def _push(self) -> None:
        remotes = self._git("remote", quiet=True)
        if remotes is None or "origin" not in remotes.stdout.split():
            self._warn_once(
                "no-origin",
                "vault_git.auto_push is on but the vault has no 'origin' "
                "remote, so commits stay on this disk. Add one, or set "
                "auto_push to false to stop this warning.",
            )
            return
        if self._git("push", "origin", "HEAD", timeout=_PUSH_TIMEOUT) is not None:
            logger.info("Pushed the vault to origin.")

    def _is_repo(self) -> bool:
        result = self._git("rev-parse", "--is-inside-work-tree", quiet=True)
        return result is not None and result.stdout.strip() == "true"

    def _unfinished_operation(self) -> str | None:
        result = self._git("rev-parse", "--git-dir", quiet=True)
        if result is None:
            return None
        git_dir = Path(result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = self.vault_path / git_dir
        return next(
            (
                name
                for marker, name in _IN_PROGRESS.items()
                if (git_dir / marker).exists()
            ),
            None,
        )

    def _commit_config(self) -> tuple[str, ...]:
        # A daemon cannot answer a GPG pinentry prompt, so it must not be
        # asked one, however the user's global config is set.
        return ("-c", "commit.gpgsign=false", *self._identity_config())

    def _identity_config(self) -> tuple[str, ...]:
        """Supply an author only when the vault repo has none."""
        if self._identity is None:
            probe = self._git("config", "--get", "user.email", quiet=True)
            if probe is not None and probe.stdout.strip():
                self._identity = ()
            else:
                logger.info(
                    "The vault repo has no user.email — committing as %s <%s>. "
                    "Set one with 'git -C %s config user.email you@example.com' "
                    "to use your own.",
                    FALLBACK_NAME,
                    FALLBACK_EMAIL,
                    self.vault_path,
                )
                self._identity = (
                    "-c",
                    f"user.name={FALLBACK_NAME}",
                    "-c",
                    f"user.email={FALLBACK_EMAIL}",
                )
        return self._identity

    def _git(
        self,
        *args: str,
        config: Sequence[str] = (),
        timeout: int = _LOCAL_TIMEOUT,
        quiet: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        """Run one git command. ``None`` means it did not succeed.

        ``quiet`` is for the calls whose failure is a normal answer —
        "this is not a repo", "no identity is configured" — which should
        not be reported as if something went wrong.
        """
        command = ["git", "-C", str(self.vault_path), *config, *args]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env(),
            )
        except FileNotFoundError:
            self._warn_once(
                "no-git",
                "git is not on PATH, so the vault cannot be committed. A "
                "LaunchDaemon does not inherit your shell's PATH — see the "
                "EnvironmentVariables key in the plist.",
            )
            return None
        except OSError as exc:
            logger.warning("Could not run git in the vault: %s", exc)
            return None
        except subprocess.TimeoutExpired:
            logger.warning(
                "git %s timed out after %ds in the vault.", args[0], timeout
            )
            return None

        if result.returncode != 0:
            if not quiet:
                detail = (result.stderr or result.stdout).strip()
                logger.warning("git %s failed in the vault: %s", args[0], detail)
            return None
        return result

    @staticmethod
    def _env() -> dict[str, str]:
        env = dict(os.environ)
        # Turn every "stop and ask a human" into an error instead. Without
        # these a push to a remote wanting credentials hangs the commit
        # loop indefinitely, and nothing is ever committed again.
        env["GIT_TERMINAL_PROMPT"] = "0"
        env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
        return env

    # -- logging -------------------------------------------------------

    def _warn_repo_missing(self) -> None:
        self._warn_once(
            "not-a-repo",
            f"vault_git.enabled is on but {self.vault_path} is not a git "
            "repository, so captures are not being committed. Run 'git init' "
            "there, or set vault_git.enabled to false.",
        )

    def _warn_once(self, key: str, message: str) -> None:
        """Log a standing misconfiguration once, not every window."""
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(message)
