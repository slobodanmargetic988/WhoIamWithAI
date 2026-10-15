from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .db import DEFAULT_DB, connect
from .discovery import SourceRoot, discover


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Your human messages to AI, in one local archive.')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB, help='SQLite archive location')
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='Discover local profiles and import human messages')
    imp.add_argument('--home', type=Path, default=Path.home(), help='User home to scan')
    imp.add_argument('--source', action='append', default=[], metavar='PROVIDER:PATH', help='Additional codex: or claude: profile/transcript root')
    imp.add_argument('--only-explicit', action='store_true', help='Scan only --source paths')
    imp.add_argument('--aliases', type=Path, help='JSON map of old project paths to canonical project paths')
    imp.add_argument('--force', action='store_true', help='Reparse unchanged files (e.g. after parser updates)')
    imp.add_argument('--quiet', action='store_true', help='Print only final JSON summary')
    scan = sub.add_parser('discover', help='List discovered profile roots without reading transcripts')
    scan.add_argument('--home', type=Path, default=Path.home())
    run = sub.add_parser('serve', help='Open the local browser app')
    run.add_argument('--port', type=int, default=8765)
    run.add_argument('--public-origin', help='Future HTTPS origin for server-rendered social metadata')
    run.add_argument('--open', action='store_true', help='Open the archive in your default browser')
    sub.add_parser('stats', help='Print archive counts')
    args = parser.parse_args(argv)
    if args.command == 'discover':
        roots, warnings = discover(args.home)
        print(json.dumps({'profiles': [{'provider': r.provider, 'path': str(r.path)} for r in roots], 'warnings': warnings}, indent=2))
        return 0
    with connect(args.db) as con:
        if args.command == 'import':
            from .importer import import_archive
            extra = []
            for source in args.source:
                provider, sep, value = source.partition(':')
                if not sep or provider not in {'codex', 'claude'} or not value:
                    parser.error('--source must be codex:/path or claude:/path')
                path = Path(value).expanduser().absolute()
                if not path.exists():
                    parser.error(f'Source does not exist: {path}')
                extra.append(SourceRoot(provider, path))
            roots, warnings = (extra, []) if args.only_explicit else discover(args.home, extra)
            aliases = json.loads(args.aliases.read_text()) if args.aliases else None
            if aliases is not None and (not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items())):
                parser.error('--aliases must contain a JSON object mapping path strings to path strings')
            def progress(event):
                print(json.dumps(event), file=sys.stderr, flush=True)
            result = import_archive(con, roots, aliases=aliases, force=args.force,
                                    progress=None if args.quiet else progress, discovery_warnings=warnings)
            print(json.dumps(result, indent=2))
            return 1 if result['files_failed'] else 0
        if args.command == 'stats':
            from .server import overview
            print(json.dumps(overview(con)['stats'], indent=2))
            return 0
    if args.command == 'serve':
        from .server import serve
        serve(args.db, args.port, args.public_origin, open_browser=args.open)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
