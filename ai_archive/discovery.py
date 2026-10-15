"""Discover local transcripts without opening credentials or following symlink loops."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class SourceRoot:
    provider: str
    path: Path


PRUNE = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.Trash', '.cache',
         'Caches', '.npm', '.pnpm-store', '.rustup', '.cargo', '.next', 'dist', 'build',
         'target', '.gradle', '.m2', '.local', 'Movies', 'Music', 'Pictures'}


def discover(home: Path, extra: list[SourceRoot] | None = None) -> tuple[list[SourceRoot], list[str]]:
    roots: set[SourceRoot] = set(extra or [])
    warnings: list[str] = []
    home = home.expanduser().absolute()
    def onerror(error):
        warnings.append(f'Cannot scan {error.filename}: {error.strerror}')
    scans = [home]
    # Library as a whole is enormous and includes browser caches. Claude keeps
    # Cowork/desktop transcripts in this specific application support directory.
    claude_library = home / 'Library' / 'Application Support' / 'Claude'
    if claude_library.is_dir():
        scans.append(claude_library)
        local_agent = claude_library / 'local-agent-mode-sessions'
        if local_agent.is_dir():
            roots.add(SourceRoot('claude', local_agent))
    for base in scans:
        for current, dirs, files in os.walk(base, onerror=onerror, followlinks=False):
            here = Path(current)
            names = set(dirs)
            codex = ('sessions' in names or 'archived_sessions' in names) and (
                'codex' in here.name.lower() or 'auth.json' in files or 'config.toml' in files)
            claude = 'projects' in names and 'claude' in str(here).lower()
            if codex:
                roots.add(SourceRoot('codex', here))
            if claude:
                roots.add(SourceRoot('claude', here))
            # Named Claude transcript backup directories may have project folders
            # directly inside them, without a surrounding `projects` folder.
            if 'claude' in here.name.lower() and any(d.startswith('-Users-') for d in dirs):
                roots.add(SourceRoot('claude', here))
            # os.walk doesn't follow profile symlinks. Recognize these explicitly.
            for name in list(dirs):
                child = here / name
                if child.is_symlink() and ('codex' in name.lower() or 'claude' in name.lower()):
                    if (child / 'sessions').is_dir() or (child / 'archived_sessions').is_dir():
                        roots.add(SourceRoot('codex', child))
                    if (child / 'projects').is_dir():
                        roots.add(SourceRoot('claude', child))
            dirs[:] = [d for d in dirs if d not in PRUNE and d != 'Library'
                       and not (here == claude_library and d == 'local-agent-mode-sessions')
                       and not (codex and d in {'sessions', 'archived_sessions'})
                       and not (claude and d == 'projects')]
    for variable, provider in [('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude')]:
        if os.environ.get(variable):
            roots.add(SourceRoot(provider, Path(os.environ[variable]).expanduser().absolute()))
    return sorted(roots, key=lambda r: (r.provider, str(r.path))), warnings


def transcript_files(root: SourceRoot):
    if root.path.is_file():
        yield root.path
        return
    if root.provider == 'codex':
        bases = [p for p in (root.path / 'sessions', root.path / 'archived_sessions') if p.is_dir()]
        if not bases:
            bases = [root.path]
    else:
        bases = [root.path / 'projects'] if (root.path / 'projects').is_dir() else [root.path]
    seen = set()
    for base in bases:
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs if d not in PRUNE]
            for name in sorted(files):
                if not name.endswith('.jsonl') or name == 'history.jsonl':
                    continue
                path = Path(current) / name
                real = path.resolve()
                if real not in seen:
                    seen.add(real)
                    yield path
    history = root.path / 'history.jsonl'
    if root.provider == 'claude' and history.is_file() and history.resolve() not in seen:
        yield history
