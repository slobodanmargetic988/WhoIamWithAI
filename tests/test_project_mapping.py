import json
from pathlib import Path
import tempfile
import unittest

from ai_archive.discovery import SourceRoot
from ai_archive.project_mapping import load_project_aliases, normalize_remote


def repository(path, remote):
    git = path / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text('[remote "origin"]\nurl = ' + remote + '\n')


class ProjectMappingTests(unittest.TestCase):
    def test_remote_identity_normalizes_transport_but_preserves_host_owner(self):
        self.assertEqual(normalize_remote("git@github.com:owner/Example.git"),
                         normalize_remote("https://github.com/owner/example"))
        self.assertEqual(normalize_remote("ssh://git@example.test/owner/Repo.git"), "example.test/owner/Repo")
        self.assertNotEqual(normalize_remote("https://github.com/a/example"), normalize_remote("https://github.com/b/example"))
        self.assertNotEqual(normalize_remote("https://first.test/a/example"), normalize_remote("https://second.test/a/example"))
        self.assertNotEqual(normalize_remote("ssh://git@host.test:2222/a/repo"), normalize_remote("ssh://git@host.test/a/repo"))
        self.assertIsNone(normalize_remote("../repository"))

    def test_deleted_worktree_maps_by_exact_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "projects" / "Example"
            repository(project, "https://github.com/owner/example.git")
            old = base / ".codex" / "worktrees" / "old" / "different-name"
            metadata = {("codex", "one"): {"cwd": str(project)},
                        ("codex", "two"): {"cwd": str(old), "git_origin_url": "git@github.com:owner/example.git"}}
            self.assertEqual(load_project_aliases([], metadata)[str(old)], str(project.resolve()))

    def test_matching_basename_without_identity_stays_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "projects" / "Example"
            repository(project, "https://github.com/owner/example.git")
            other = base / "deleted" / "Example"
            metadata = {("codex", "a"): {"cwd": str(project)}, ("codex", "b"): {"cwd": str(other)}}
            self.assertNotIn(str(other), load_project_aliases([], metadata))

    def test_two_real_checkouts_leave_remote_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first, second, old = base / "first", base / "second", base / "deleted"
            for path in [first, second]:
                repository(path, "https://github.com/owner/example.git")
            metadata = {("codex", str(index)): {"cwd": str(path), "git_origin_url": "git@github.com:owner/example.git"}
                        for index, path in enumerate([first, second, old])}
            self.assertNotIn(str(old), load_project_aliases([], metadata))

    def test_vanished_nested_repo_is_not_assumed_to_belong_to_surviving_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            parent = base / "parent"
            repository(parent, "https://github.com/owner/parent.git")
            removed = parent / "removed-child"
            metadata = {("codex", "a"): {"cwd": str(parent)},
                        ("codex", "b"): {"cwd": str(removed), "git_origin_url": "https://github.com/other/child.git"}}
            self.assertNotIn(str(removed), load_project_aliases([], metadata))

    def test_desktop_explicit_origin_maps_deleted_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            claude = base / "Library" / "Application Support" / "Claude"
            session = claude / "claude-code-sessions" / "account" / "session.json"
            session.parent.mkdir(parents=True)
            origin, old = base / "project", base / "deleted-worktree"
            session.write_text(json.dumps({"cliSessionId": "thread-a", "originCwd": str(origin),
                                           "cwd": str(old), "worktreePath": str(old)}))
            aliases = load_project_aliases([SourceRoot("claude", claude)], {})
            self.assertEqual(aliases[str(old)], str(origin))

    def test_historical_origin_bridges_repo_host_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project, old = base / "project", base / "deleted"
            repository(project, "ssh://git@new.test/owner/Example.git")
            metadata = {("codex", "a"): {"cwd": str(project), "git_origin_url": "https://old.test/owner/Example.git"},
                        ("codex", "b"): {"cwd": str(old), "git_origin_url": "https://old.test/owner/Example.git"}}
            self.assertEqual(load_project_aliases([], metadata)[str(old)], str(project.resolve()))

    def test_submodule_retains_its_own_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            parent = base / "parent"
            repository(parent, "https://github.com/owner/parent.git")
            child = parent / "child"
            child.mkdir()
            gitdir = parent / ".git" / "modules" / "child"
            gitdir.mkdir(parents=True)
            (gitdir / "config").write_text('[remote "origin"]\nurl=https://github.com/owner/child.git\n')
            (child / ".git").write_text("gitdir: ../.git/modules/child\n")
            old = base / "deleted-child"
            metadata = {("codex", "a"): {"cwd": str(child)},
                        ("codex", "b"): {"cwd": str(old), "git_origin_url": "https://github.com/owner/child.git"}}
            self.assertEqual(load_project_aliases([], metadata)[str(old)], str(child.resolve()))

    def test_linked_worktree_uses_git_common_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project, old = base / "project", base / "worktree"
            repository(project, "https://github.com/owner/repo.git")
            gitdir = project / ".git" / "worktrees" / "one"
            gitdir.mkdir(parents=True)
            (gitdir / "commondir").write_text("../..\n")
            old.mkdir()
            (old / ".git").write_text("gitdir: " + str(gitdir) + "\n")
            metadata = {("codex", "a"): {"cwd": str(old)}}
            self.assertEqual(load_project_aliases([], metadata)[str(old)], str(project.resolve()))

    def test_submodule_linked_worktrees_map_to_recorded_main_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            main = base / "parent" / "child"
            main.mkdir(parents=True)
            common = base / "parent" / ".git" / "modules" / "child"
            common.mkdir(parents=True)
            (common / "config").write_text('[core]\nworktree=../../../child\n[remote "origin"]\nurl=https://github.com/owner/child.git\n')
            (main / ".git").write_text("gitdir: ../.git/modules/child\n")
            worktree = base / "worktree"
            worktree.mkdir()
            gitdir = common / "worktrees" / "one"
            gitdir.mkdir(parents=True)
            (gitdir / "commondir").write_text("../..\n")
            (worktree / ".git").write_text("gitdir: " + str(gitdir) + "\n")
            deleted = base / "deleted"
            metadata = {("codex", "a"): {"cwd": str(main)}, ("codex", "b"): {"cwd": str(worktree)},
                        ("codex", "c"): {"cwd": str(deleted), "git_origin_url": "https://github.com/owner/child.git"}}
            aliases = load_project_aliases([], metadata)
            self.assertEqual(aliases[str(worktree)], str(main.resolve()))
            self.assertEqual(aliases[str(deleted)], str(main.resolve()))

    def test_session_header_provides_remote_without_state_database(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project, old = base / "project", base / "deleted"
            repository(project, "https://github.com/owner/repo.git")
            root = base / ".codex"
            sessions = root / "sessions"
            sessions.mkdir(parents=True)
            session = sessions / "rollout.jsonl"
            session.write_text(json.dumps({"type": "session_meta", "payload": {
                "id": "a", "cwd": str(old), "git": {"repository_url": "git@github.com:owner/repo.git"}}}) + "\n")
            metadata = {("codex", "a"): {"cwd": str(project)}}
            self.assertEqual(load_project_aliases([SourceRoot("codex", root)], metadata)[str(old)], str(project.resolve()))


if __name__ == "__main__":
    unittest.main()
