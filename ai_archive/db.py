from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

DEFAULT_DB = Path(__file__).resolve().parent.parent / 'data' / 'archive.sqlite3'


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def stable_id(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def timestamp(value: object) -> str | None:
    if value is None or value == '':
        return None
    try:
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value / 1000 if abs(value) > 100_000_000_000 else value, timezone.utc)
        else:
            value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if value.tzinfo is None:
                return None  # An unknown zone must not silently become the computer's zone.
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def connect(path: Path | str = DEFAULT_DB, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path).expanduser().resolve()
    if readonly:
        con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        con = sqlite3.connect(path, timeout=30)
        os.chmod(path, 0o600)
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(Path(__file__).with_name('schema.sql').read_text())
        # Additive migration preserves existing message IDs and imported data.
        columns = {row[1] for row in con.execute('PRAGMA table_info(source_files)')}
        if 'context_version' not in columns:
            con.execute('ALTER TABLE source_files ADD COLUMN context_version INTEGER NOT NULL DEFAULT 0')
        con.execute('INSERT OR IGNORE INTO schema_version VALUES(2)')
        con.commit()
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=30000')
    return con


class ProjectResolver:
    """Resolve existing repositories/worktrees; never merge unrelated paths by basename."""

    def __init__(self, con: sqlite3.Connection, aliases: dict[str, str] | None = None,
                 inferred_aliases: set[str] | None = None):
        self.con = con
        self.aliases = {str(Path(k).expanduser()): str(Path(v).expanduser()) for k, v in (aliases or {}).items()}
        self.cache: dict[str, tuple[str, str]] = {}
        self.inferred_aliases = inferred_aliases or set()

    def resolve(self, cwd: str | None) -> tuple[str, str]:
        original = str(cwd or 'unassigned')
        if original in self.cache:
            return self.cache[original]
        method = 'recorded_path'
        path = original
        # Longest prefix wins, so a moved repository's subdirectories follow its alias.
        for old in sorted(self.aliases, key=len, reverse=True):
            if path == old or path.startswith(old.rstrip('/') + '/'):
                path = self.aliases[old]
                method = 'metadata_alias' if old in self.inferred_aliases else 'explicit_alias'
                break
        if path != 'unassigned':
            path = str(Path(os.path.normpath(os.path.abspath(os.path.expanduser(path)))).resolve())
            if method not in {'explicit_alias', 'metadata_alias'}:
                actual = Path(path).resolve()
                # An absent nested checkout may have belonged to another repo.
                # An existing parent's marker alone cannot prove its identity.
                ancestors = (actual, *actual.parents) if actual.exists() else ()
                for ancestor in ancestors:
                    marker = ancestor / '.git'
                    if marker.is_dir():
                        path, method = str(ancestor), 'git_root'
                        break
                    if marker.is_file():
                        try:
                            text = marker.read_text().strip()
                            if text.startswith('gitdir:'):
                                gitdir = (ancestor / text[7:].strip()).resolve()
                                common = gitdir / 'commondir'
                                if common.is_file():
                                    common_dir = (gitdir / common.read_text().strip()).resolve()
                                    if common_dir.name == '.git':
                                        path, method = str(common_dir.parent), 'git_common_directory'
                                    else:
                                        path, method = str(ancestor), 'git_worktree'
                                else:
                                    path, method = str(ancestor), 'git_root'
                                break
                        except (OSError, UnicodeError):
                            pass
        ident = stable_id('project', path)
        name = Path(path).name if path != 'unassigned' else 'Unassigned project'
        self.con.execute('INSERT OR IGNORE INTO projects VALUES(?,?,?)', (ident, name, path))
        self.con.execute('INSERT OR REPLACE INTO project_paths VALUES(?,?,?)', (original, ident, method))
        self.cache[original] = (ident, path)
        return self.cache[original]
