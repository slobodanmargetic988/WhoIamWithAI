"""Read transcript locations and project labels from Codex's local metadata.

Queries intentionally select metadata columns only. Credentials, first-message
previews, full history, and application settings are never opened.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .discovery import SourceRoot


def _profile_directory(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.name in {"sessions", "archived_sessions"}:
            return candidate.parent
    if path.is_dir():
        return path
    return None


def _databases(roots: Iterable[SourceRoot]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        if root.provider != "codex":
            continue
        profile = _profile_directory(Path(root.path))
        if profile is not None:
            found.update(path.resolve() for path in profile.glob("state_*.sqlite") if path.is_file())
    return sorted(found)


def _connect(path: Path) -> sqlite3.Connection:
    # Unlike immutable=1, mode=ro includes committed records in a live WAL file.
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def _thread_rows(connection: sqlite3.Connection):
    allowed = ("id", "cwd", "title", "name", "source", "rollout_path", "project_id",
               "thread_source", "originator", "created_at", "updated_at",
               "created_at_ms", "updated_at_ms", "archived", "git_origin_url",
               "git_branch", "agent_path", "agent_nickname", "agent_role")
    available = _columns(connection, "threads")
    columns = [column for column in allowed if column in available]
    if "id" not in columns:
        return []
    return connection.execute("SELECT " + ",".join(columns) + " FROM threads")


def _project_data(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not {"id", "name"}.issubset(_columns(connection, "projects")):
        return {}
    projects = {row["id"]: {"project_name": row["name"], "project_paths": []}
                for row in connection.execute("SELECT id,name FROM projects")}
    if {"project_id", "path"}.issubset(_columns(connection, "project_roots")):
        order = " ORDER BY position" if "position" in _columns(connection, "project_roots") else ""
        for row in connection.execute("SELECT project_id,path FROM project_roots" + order):
            if row["project_id"] in projects and row["path"]:
                projects[row["project_id"]]["project_paths"].append(row["path"])
    return projects


def _project_path(cwd: str | None, paths: list[str]) -> str | None:
    if not paths:
        return None
    matches = []
    if cwd:
        current = Path(cwd).expanduser()
        for value in paths:
            path = Path(value).expanduser()
            try:
                current.relative_to(path)
                matches.append(value)
            except ValueError:
                pass
    return max(matches, key=len) if matches else paths[0]


def load_metadata(roots: Iterable[SourceRoot]) -> dict[tuple[str, str], dict[str, Any]]:
    """Return thread/project metadata keyed by (provider, native thread ID)."""
    result: dict[tuple[str, str], dict[str, Any]] = {}
    ranks: dict[tuple[str, str], tuple[int, int]] = {}
    for database in _databases(roots):
        try:
            with _connect(database) as connection:
                projects = _project_data(connection)
                for row in _thread_rows(connection):
                    metadata = dict(row)
                    thread_id = metadata.pop("id")
                    metadata["title"] = metadata.get("name") or metadata.get("title")
                    metadata["source_db"] = str(database)
                    metadata["thread_native_id"] = thread_id
                    metadata.update(projects.get(metadata.get("project_id"), {}))
                    metadata["project_path"] = _project_path(metadata.get("cwd"), metadata.get("project_paths", []))
                    source = metadata.get("source")
                    if isinstance(source, str) and source.startswith("{"):
                        try:
                            metadata["source"] = json.loads(source)
                        except ValueError:
                            pass
                    key = ("codex", thread_id)
                    # Prefer the most recently updated row and richer project
                    # metadata. The same thread can exist in copied profiles.
                    updated = metadata.get("updated_at_ms") or (metadata.get("updated_at") or 0) * 1000
                    rank = (int(updated), int(bool(metadata.get("project_id"))))
                    if key not in result or rank > ranks[key]:
                        previous = result.get(key, {})
                        result[key] = {**previous, **{k: v for k, v in metadata.items() if v is not None}}
                        ranks[key] = rank
                    else:
                        for field, value in metadata.items():
                            if result[key].get(field) is None and value is not None:
                                result[key][field] = value
        except (sqlite3.Error, OSError, ValueError):
            # Transcripts remain importable without optional application labels.
            continue
    return result


def discovered_roots_from_state(roots: Iterable[SourceRoot]) -> list[SourceRoot]:
    """Include existing rollouts referenced by the app even after profiles moved."""
    roots = list(roots)
    known = {Path(root.path).resolve() for root in roots if root.provider == "codex"}
    added: dict[Path, SourceRoot] = {}
    for database in _databases(roots):
        try:
            with _connect(database) as connection:
                if "rollout_path" not in _columns(connection, "threads"):
                    continue
                for row in connection.execute("SELECT DISTINCT rollout_path FROM threads WHERE rollout_path IS NOT NULL"):
                    path = Path(row["rollout_path"]).expanduser()
                    if not path.is_absolute():
                        path = database.parent / path
                    if not path.is_file():
                        continue
                    # A profile root groups its sessions into one provenance
                    # profile, rather than manufacturing one profile per file.
                    profile = _profile_directory(path)
                    location = profile if profile is not None else path
                    resolved = location.resolve()
                    if resolved not in known:
                        added[resolved] = SourceRoot("codex", location)
        except (sqlite3.Error, OSError, ValueError):
            continue
    return sorted(added.values(), key=lambda root: str(root.path))
