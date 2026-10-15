#!/usr/bin/env python3
"""Friendly local launcher. Uses only Python's standard library."""

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
MINIMUM_PYTHON = (3, 11)


def check_requirements():
    """Check without creating an archive or reading a transcript."""
    if sys.version_info < MINIMUM_PYTHON:
        raise RuntimeError(
            "Python 3.11 or later is required. Install a current Python from "
            "https://www.python.org/downloads/ and run this launcher again."
        )
    try:
        import sqlite3
    except ImportError as error:
        raise RuntimeError("This Python does not include SQLite. Install a standard Python build from https://www.python.org/downloads/.") from error
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE check_search USING fts5(text)")
        finally:
            con.close()
    except sqlite3.Error as error:
        # A system Python may have sqlite3 but omit its optional FTS5 module.
        raise RuntimeError(
            "This Python needs SQLite with FTS5 search support. The standard "
            "Windows/macOS Python installer includes it. On Linux, install a "
            "Python/SQLite build with FTS5 enabled. No pip packages are needed. "
            "Details: " + str(error)
        ) from error


def parser():
    result = argparse.ArgumentParser(
        description="Collect and browse your own Codex and Claude histories locally.",
        epilog="No accounts, pip packages, administrator access, or cloud uploads are required.",
    )
    action = result.add_mutually_exclusive_group()
    action.add_argument("--collect", action="store_true", help="Collect/update messages, then open the app")
    action.add_argument("--collect-only", action="store_true", help="Collect/update messages and exit")
    action.add_argument("--browse", action="store_true", help="Open the app without scanning histories")
    action.add_argument("--check", action="store_true", help="Check Python and search support, then exit")
    browser = result.add_mutually_exclusive_group()
    browser.add_argument("--open", dest="open_browser", action="store_true", help="Open the default browser (default)")
    browser.add_argument("--no-open", dest="open_browser", action="store_false", help="Print the local address without opening a browser")
    result.set_defaults(open_browser=True)
    result.add_argument("--db", type=Path, help="Archive database location (default: data/archive.sqlite3 in this project)")
    result.add_argument("--home", type=Path, help="Home directory to scan instead of your current home")
    result.add_argument("--source", action="append", default=[], metavar="PROVIDER:PATH", help="Add a codex: or claude: history/profile path; may be repeated")
    result.add_argument("--only-explicit", action="store_true", help="Only scan paths supplied with --source")
    result.add_argument("--port", type=int, default=8765, help="Local app port (default: 8765)")
    return result


def choose_action():
    print("\nWho I Am With AI - your messages, on your computer\n")
    print("1. Collect/update my Codex and Claude messages, then open the app")
    print("2. Open my existing archive without collecting")
    print("3. Quit\n")
    print("Collection reads recognized histories in your user folder and keeps the")
    print("archive in this project's data folder. It does not upload anything.")
    print("Existing messages are kept; repeated collections skip unchanged files.\n")
    while True:
        try:
            choice = input("Choose 1, 2, or 3: ").strip()
        except EOFError:
            return "quit"
        if choice in {"1", "2", "3"}:
            return {"1": "collect", "2": "browse", "3": "quit"}[choice]
        print("Enter 1 to collect, 2 to browse, or 3 to quit.")


def run_archive(arguments):
    # Existing metadata readers use Python's default text encoding. UTF-8 mode
    # keeps Unicode paths and project metadata consistent on Windows too.
    command = [sys.executable, "-X", "utf8", "-m", "ai_archive"] + arguments
    return subprocess.call(command, cwd=str(ROOT))


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if not 1 <= args.port <= 65535:
        arguments.error("--port must be between 1 and 65535")
    if args.only_explicit and not args.source:
        arguments.error("--only-explicit needs at least one --source")
    if (args.browse or args.check) and (args.home or args.source or args.only_explicit):
        arguments.error("history locations are only used when collecting")
    try:
        check_requirements()
    except RuntimeError as error:
        print("\n" + str(error), file=sys.stderr)
        return 1
    if args.check:
        print("Ready: Python 3.11+ and SQLite FTS5 are available. No packages to install.")
        return 0
    action = "collect" if (args.collect or args.collect_only) else "browse" if args.browse else None
    if action is None:
        if not sys.stdin.isatty():
            arguments.error("choose --collect, --collect-only, or --browse when running without an interactive terminal")
        action = choose_action()
    if action == "quit":
        return 0
    common = ["--db", str(args.db.expanduser().resolve())] if args.db else []
    try:
        if action == "collect":
            collection = common + ["import"]
            if args.home:
                collection += ["--home", str(args.home.expanduser().resolve())]
            for source in args.source:
                collection += ["--source", source]
            if args.only_explicit:
                collection.append("--only-explicit")
            print("\nCollecting your local messages. The first collection can take a while.", flush=True)
            result = run_archive(collection)
            if result:
                print("Collection did not finish cleanly. Any successfully collected messages remain in your archive.", file=sys.stderr)
                print("Review the collection report above. You can browse with: python start.py --browse", file=sys.stderr)
                return result
            if args.collect_only:
                return 0
        print("\nYour local app: http://127.0.0.1:" + str(args.port), flush=True)
        print("Keep this window open while browsing. Press Ctrl+C here to stop the app.\n", flush=True)
        serving = common + ["serve", "--port", str(args.port)]
        if args.open_browser:
            serving.append("--open")
        return run_archive(serving)
    except KeyboardInterrupt:
        print("\nStopped. Your archive is saved locally.")
        return 130
    except OSError as error:
        print("Could not start Python: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
