"""Launcher consent, argument forwarding, and incremental collection checks."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import start


class LauncherTests(unittest.TestCase):
    def quiet_main(self, arguments):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return start.main(arguments)

    def test_check_does_not_start_collection_or_server(self):
        with patch.object(start, "run_archive") as run:
            self.assertEqual(self.quiet_main(["--check"]), 0)
        run.assert_not_called()

    def test_browse_never_imports(self):
        with patch.object(start, "run_archive", return_value=0) as run:
            self.assertEqual(self.quiet_main(["--browse", "--no-open", "--port", "8766"]), 0)
        run.assert_called_once_with(["serve", "--port", "8766"])

    def test_noninteractive_launch_requires_explicit_action(self):
        with patch.object(start.sys, "stdin", io.StringIO()), patch.object(start, "run_archive") as run:
            with self.assertRaises(SystemExit) as error:
                self.quiet_main([])
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_collection_failure_does_not_start_server(self):
        with patch.object(start, "run_archive", return_value=1) as run:
            self.assertEqual(self.quiet_main(["--collect"]), 1)
        run.assert_called_once_with(["import"])

    def test_explicit_collection_is_forwarded_without_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "my archive.sqlite3"
            source = "claude:C:\\Users\\Example Person\\.claude"
            with patch.object(start, "run_archive", return_value=0) as run:
                result = self.quiet_main(["--collect", "--open", "--db", str(database),
                                          "--source", source, "--only-explicit"])
            self.assertEqual(result, 0)
            common = ["--db", str(database.resolve())]
            self.assertEqual(run.call_args_list[0].args[0], common + ["import", "--source", source, "--only-explicit"])
            self.assertEqual(run.call_args_list[1].args[0], common + ["serve", "--port", "8765", "--open"])

    def test_collect_only_is_incremental_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "profile with spaces" / "projects"
            source.mkdir(parents=True)
            history = source / "session.jsonl"
            database = root / "archive.sqlite3"
            def record(native, text):
                return {"type": "user", "uuid": native, "sessionId": "example-thread",
                        "timestamp": "2026-01-01T10:00:00Z", "cwd": str(root / "example-project"),
                        "message": {"role": "user", "content": text}}
            history.write_text(json.dumps(record("first", "First example message")) + "\n", encoding="utf-8")
            command = [sys.executable, str(start.ROOT / "start.py"), "--collect-only", "--db", str(database),
                       "--only-explicit", "--source", "claude:" + str(source.parent)]
            subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True)
            with history.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record("second", "A second example message")) + "\n")
            subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True)
            subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True)
            con = sqlite3.connect(database)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
                self.assertEqual({row[0] for row in con.execute("SELECT text FROM messages")},
                                 {"First example message", "A second example message"})
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
