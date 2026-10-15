"""Recover project identity from repository and desktop worktree evidence.

Deleted worktrees cannot be resolved through a .git marker. Their recorded
origin URL can still identify an existing checkout. A path is merged only when
the repository identity has one canonical checkout, or when desktop metadata
explicitly records the worktree's origin. Directory names are never evidence.
"""

from __future__ import annotations

from collections import defaultdict
import configparser
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from .discovery import SourceRoot, transcript_files


_UUID = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl$", re.I)


def normalize_remote(value: Any) -> str | None:
    """Identify a host/repository across HTTPS and SSH clone URL spellings.

    Hostnames and GitHub repository paths are case-insensitive. Other server
    paths retain their case. Credentials and queries never enter the result.
    Different hosts, owners, and nondefault ports remain different identities.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if "://" not in value:
        match = re.fullmatch(r"(?:[^@/\s]+@)?([^/:\s]+):(.+)", value)
        if not match:
            return None  # Relative/local paths are not host repository identities.
        host, path = match.groups()
        port = None
    else:
        try:
            parsed = urlsplit(value)
            host, path, port = parsed.hostname, parsed.path, parsed.port
        except ValueError:
            return None
        if not host or parsed.scheme not in {"ssh", "https", "http", "git"}:
            return None
        if (parsed.scheme, port) in {("ssh", 22), ("https", 443), ("http", 80), ("git", 9418)}:
            port = None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or not path:
        return None
    host = host.lower()
    if host == "github.com":
        path = path.lower()
    return host + (f":{port}" if port else "") + "/" + path


def _path(value: Any) -> str | None:
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        return None
    return os.path.normpath(str(path))


def _remote(metadata: dict[str, Any]) -> str | None:
    git = metadata.get("git")
    value = metadata.get("git_origin_url")
    if not value and isinstance(git, dict):
        value = git.get("repository_url") or git.get("origin_url")
    return normalize_remote(value)


def _git_info(value: str) -> tuple[str, Path] | None:
    """Return an existing canonical checkout and its repository config path."""
    path = Path(value).resolve()
    for ancestor in (path, *path.parents):
        marker = ancestor / ".git"
        if marker.is_dir():
            return str(ancestor), marker / "config"
        if marker.is_file():
            try:
                text = marker.read_text().strip()
                if not text.startswith("gitdir:"):
                    continue
                gitdir = (ancestor / text[7:].strip()).resolve()
                if not gitdir.is_dir():
                    continue
                common = gitdir / "commondir"
                if common.is_file():
                    common_dir = (gitdir / common.read_text().strip()).resolve()
                    if common_dir.name == ".git" and common_dir.is_dir():
                        return str(common_dir.parent), common_dir / "config"
                    # Linked worktrees of a submodule share .git/modules/name.
                    # Its explicit core.worktree points to the main checkout.
                    parser = configparser.RawConfigParser(strict=False)
                    parser.read(common_dir / "config")
                    worktree = parser.get("core", "worktree", fallback=None)
                    if worktree:
                        checkout = (common_dir / worktree).resolve()
                        main_marker = checkout / ".git"
                        if main_marker.is_file():
                            main_text = main_marker.read_text().strip()
                            if main_text.startswith("gitdir:") and (checkout / main_text[7:].strip()).resolve() == common_dir:
                                return str(checkout), common_dir / "config"
                    return str(ancestor), common_dir / "config"
                # A submodule has its own Git identity even though its gitdir
                # lives in the parent repository's .git/modules directory.
                return str(ancestor), gitdir / "config"
            except (OSError, UnicodeError, configparser.Error):
                continue
    return None


def _config_origin(path: Path) -> str | None:
    parser = configparser.RawConfigParser(strict=False)
    try:
        with path.open() as stream:
            parser.read_file(stream)
        return normalize_remote(parser.get('remote "origin"', "url", fallback=None))
    except (OSError, UnicodeError, configparser.Error):
        return None


def _codex_headers(roots: Iterable[SourceRoot]) -> list[dict[str, Any]]:
    """Read bounded session headers, never the multi-GB message/tool bodies."""
    records = []
    seen = set()
    for root in roots:
        if root.provider != "codex":
            continue
        for source in transcript_files(root):
            path = source.resolve()
            if path in seen:
                continue
            seen.add(path)
            match = _UUID.search(path.name)
            expected_id = match.group(1) if match else None
            try:
                with path.open("rb") as stream:
                    for _ in range(16):
                        remaining = 512 * 1024 - stream.tell()
                        if remaining <= 0:
                            break
                        line = stream.readline(remaining)
                        if not line:
                            break
                        try:
                            item = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(item, dict):
                            continue
                        payload = item.get("payload")
                        if item.get("type") != "session_meta" or not isinstance(payload, dict):
                            continue
                        if expected_id and payload.get("id") != expected_id:
                            continue
                        records.append({"cwd": payload.get("cwd"), "git": payload.get("git")})
                        break
            except OSError:
                continue
    return records


def _claude_metadata_files(roots: Iterable[SourceRoot]):
    candidates: set[Path] = set()
    for root in roots:
        here = Path(root.path)
        if here.is_file():
            here = here.parent
        if root.provider == "claude":
            candidates.add(here)
        # Local-agent-mode-sessions is discovered independently of the sibling
        # Claude Code metadata directory. Include that sibling explicitly.
        for ancestor in (here, *here.parents):
            if ancestor.name in {"Claude", "claude-code-sessions"}:
                candidates.add(ancestor)
            if ancestor.name.startswith((".claude", ".codex")):
                candidates.add(ancestor.parent / "Library" / "Application Support" / "Claude")
                break
    directories: set[Path] = set()
    for base in candidates:
        if not base.is_dir():
            continue
        if base.name == "claude-code-sessions":
            directories.add(base.resolve())
            continue
        for current, dirs, _ in os.walk(base, followlinks=False):
            here = Path(current)
            if len(here.relative_to(base).parts) > 4:
                dirs[:] = []
                continue
            if here.name == "claude-code-sessions":
                directories.add(here.resolve())
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if name not in {
                "projects", "local-agent-mode-sessions", ".git", "node_modules",
                "Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache"
            } and not name.startswith("-Users-")]
    for directory in sorted(directories):
        yield from directory.rglob("*.json")


def load_project_aliases(roots: Iterable[SourceRoot],
                         metadata: dict[tuple[str, str], dict[str, Any]]) -> dict[str, str]:
    """Return evidence-backed original cwd → canonical project-root aliases.

    Multiple existing checkouts for the same remote remain ambiguous. Explicit
    worktree origins can disambiguate individual paths. Conflicting metadata
    and cycles are left unmapped instead of silently choosing a project.
    """
    roots = list(roots)
    records = [{"cwd": value.get("cwd"), "git_origin_url": value.get("git_origin_url"),
                "git": value.get("git")} for value in metadata.values()]
    records.extend(_codex_headers(roots))
    explicit: list[tuple[str, str]] = []
    for file in _claude_metadata_files(roots):
        try:
            item = json.loads(file.read_text())
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(item, dict) or not item.get("cliSessionId"):
            continue
        origin = _path(item.get("originCwd"))
        cwd, worktree = _path(item.get("cwd")), _path(item.get("worktreePath"))
        for location in {origin, cwd, worktree} - {None}:
            records.append({"cwd": location})
        if origin:
            if worktree and worktree != origin:
                explicit.append((worktree, origin))
            # cwd can be a normal subdirectory; originCwd is still a direct
            # recorded relationship and safely groups that session's location.
            if cwd and cwd != origin:
                explicit.append((cwd, origin))

    observations: dict[str, set[str]] = defaultdict(set)
    paths: set[str] = set()
    for record in records:
        location = _path(record.get("cwd"))
        if not location:
            continue
        paths.add(location)
        remote = _remote(record)
        if remote:
            observations[location].add(remote)

    infos = {location: _git_info(location) for location in paths}
    remote_checkouts: dict[str, set[str]] = defaultdict(set)
    for location, info in infos.items():
        if info is None:
            continue
        canonical, config = info
        current_origin = _config_origin(config)
        if current_origin:
            remote_checkouts[current_origin].add(canonical)
        # Historical origins on the existing checkout bridge an intentional
        # host migration. A nested vanished repo's origin is never attributed
        # to whichever parent repository happens to exist now.
        if str(Path(location).resolve()) == canonical:
            for remote in observations.get(location, []):
                remote_checkouts[remote].add(canonical)

    proposals: dict[str, set[str]] = defaultdict(set)
    for location, info in infos.items():
        # An absent nested folder may once have been a separate repository.
        # Its surviving parent alone is not evidence of the original identity.
        if info and location != info[0] and Path(location).exists():
            proposals[location].add(info[0])
    for location, remotes in observations.items():
        alternatives = [remote_checkouts.get(remote, set()) for remote in remotes]
        targets = set().union(*alternatives) if alternatives else set()
        if len(targets) == 1 and all(alternatives):
            target = next(iter(targets))
            if location != target:
                proposals[location].add(target)
    for old, origin in explicit:
        info = infos.get(origin)
        target = info[0] if info else origin
        if old != target:
            proposals[old].add(target)

    def terminal(location: str, trail: set[str]) -> set[str] | None:
        if location in trail:
            return None
        targets = proposals.get(location)
        if not targets:
            return {location}
        resolved: set[str] = set()
        for target in targets:
            end = terminal(target, trail | {location})
            if end is None:
                return None
            resolved.update(end)
        return resolved

    aliases = {}
    for old in sorted(proposals):
        resolved = terminal(old, set())
        if resolved and len(resolved) == 1:
            target = next(iter(resolved))
            if target != old:
                aliases[old] = target
    return aliases
