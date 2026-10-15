import sqlite3
import tempfile
from pathlib import Path
import unittest

from ai_archive.discovery import SourceRoot
from ai_archive.metadata import discovered_roots_from_state, load_metadata


class MetadataTests(unittest.TestCase):
    def make_db(self, root, rollout, updated=1, rich=True):
        root.mkdir(parents=True, exist_ok=True)
        database = root / "state_5.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE threads(id TEXT, cwd TEXT, title TEXT, source TEXT, rollout_path TEXT, project_id TEXT, updated_at_ms INTEGER, first_user_message TEXT)")
            connection.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)", ("thread-a", "/projects/example/worktree", "Renamed project thread", "vscode", str(rollout), "project-a" if rich else None, updated, "must not be read"))
            connection.execute("CREATE TABLE projects(id TEXT, name TEXT)")
            connection.execute("CREATE TABLE project_roots(project_id TEXT, position INTEGER, path TEXT)")
            if rich:
                connection.execute("INSERT INTO projects VALUES('project-a','Example')")
                connection.execute("INSERT INTO project_roots VALUES('project-a',0,'/projects/example')")
        return database

    def test_safe_metadata_and_project_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex"
            self.make_db(root, root / "sessions" / "one.jsonl")
            rows = load_metadata([SourceRoot("codex", root)])
            metadata = rows[("codex", "thread-a")]
            self.assertEqual(metadata["title"], "Renamed project thread")
            self.assertEqual(metadata["project_path"], "/projects/example")
            self.assertEqual(metadata["project_name"], "Example")
            self.assertNotIn("first_user_message", metadata)

    def test_rollout_reference_discovers_one_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex"
            other = Path(directory) / "alternate-home"
            file = other / "sessions" / "2026" / "one.jsonl"
            file.parent.mkdir(parents=True)
            file.write_text("")
            self.make_db(root, file)
            self.assertEqual(discovered_roots_from_state([SourceRoot("codex", root)]), [SourceRoot("codex", other)])

    def test_missing_rollouts_are_not_added(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".codex"
            self.make_db(root, root / "sessions" / "missing.jsonl")
            self.assertEqual(discovered_roots_from_state([SourceRoot("codex", root)]), [])

    def test_newer_thread_metadata_wins_across_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "old"
            new = Path(directory) / "new"
            self.make_db(old, old / "sessions" / "a.jsonl", updated=1)
            self.make_db(new, new / "sessions" / "a.jsonl", updated=2, rich=False)
            with sqlite3.connect(new / "state_5.sqlite") as connection:
                connection.execute("UPDATE threads SET title='Newest title'")
            metadata = load_metadata([SourceRoot("codex", old), SourceRoot("codex", new)])[("codex", "thread-a")]
            self.assertEqual(metadata["title"], "Newest title")
            self.assertEqual(metadata["project_path"], "/projects/example")

    def test_old_schema_without_optional_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "state_1.sqlite") as connection:
                connection.execute("CREATE TABLE threads(id TEXT,cwd TEXT,source TEXT)")
                connection.execute("INSERT INTO threads VALUES('old-thread','/example','exec')")
            metadata = load_metadata([SourceRoot("codex", root)])[("codex", "old-thread")]
            self.assertEqual(metadata["cwd"], "/example")
            self.assertEqual(metadata["source"], "exec")


if __name__ == "__main__":
    unittest.main()
