"""Small end-to-end checks for archive identity, chronology and source evidence."""

import json
from pathlib import Path
import tempfile
import unittest

from ai_archive.db import connect, timestamp
from ai_archive.discovery import SourceRoot
from ai_archive.importer import import_archive


WHEN = "2026-10-15T08:00:00.000Z"


def claude_message(native="shared-input", thread="claude-thread", text="Add project search", **extra):
    return {"type": "user", "uuid": native, "sessionId": thread, "timestamp": WHEN,
            "cwd": "/example/project", "message": {"role": "user", "content": text}, **extra}


def codex_session(thread="codex-thread", cwd="/example/project"):
    return {"type": "session_meta", "timestamp": WHEN,
            "payload": {"id": thread, "session_id": thread, "cwd": cwd, "source": "vscode"}}


def codex_message(native="shared-input", thread="codex-thread", text="Add project search"):
    return {"type": "event_msg", "timestamp": WHEN, "ordinal": 2,
            "payload": {"type": "item_completed", "thread_id": thread, "turn_id": "turn-1",
                        "item": {"type": "UserMessage", "id": native,
                                 "content": [{"type": "text", "text": text}]}}}


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.con = connect(self.base / "archive.sqlite3")
        self.addCleanup(self.con.close)

    def source(self, provider, name, records, file="conversation.jsonl"):
        root = self.base / name
        path = root / ("sessions" if provider == "codex" else "projects") / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        return SourceRoot(provider, root), path

    def count(self, table):
        return self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_profiles_are_one_identity_and_backups_deduplicate_with_provenance(self):
        rows = [codex_session(), codex_message()]
        original, _ = self.source("codex", ".codex", rows)
        backup, _ = self.source("codex", ".codex-second", rows)
        # A coincidentally identical UUID from another provider is a new event.
        claude, _ = self.source("claude", ".claude", [claude_message()])
        result = import_archive(self.con, [original, backup, claude])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["messages"], 2)
        self.assertEqual(self.count("identities"), 1)
        self.assertEqual(self.count("profiles"), 3)
        self.assertEqual(self.count("message_sources"), 3)
        self.assertEqual(self.count("thread_messages"), 2)
        self.assertEqual({r[0] for r in self.con.execute("SELECT identity_id FROM messages")}, {1})
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_copied_history_belongs_to_both_threads_without_a_second_action(self):
        first, _ = self.source("claude", "claude-first", [claude_message(thread="parent")])
        fork, _ = self.source("claude", "claude-fork", [claude_message(thread="fork")])
        result = import_archive(self.con, [first, fork])
        self.assertEqual(result["messages"], 1)
        self.assertEqual(self.count("threads"), 2)
        self.assertEqual(self.count("thread_messages"), 2)
        self.assertEqual(self.count("message_sources"), 2)

    def test_codex_local_item_ids_remain_distinct_across_threads(self):
        roots = []
        for thread, date, text in (("thread-a", "2026-05-04", "Build the first project"),
                                   ("thread-b", "2026-07-21", "Change the second project")):
            session = codex_session(thread=thread)
            session["timestamp"] = f"{date}T10:00:00Z"
            # Older Codex versions reuse short item IDs in different threads.
            # Identical short final text also must retain its own timestamp.
            reply = {
                "type": "event_msg", "timestamp": f"{date}T10:00:00Z",
                "payload": {"type": "item_completed", "thread_id": thread, "turn_id": "prior-turn",
                            "item": {"type": "AgentMessage", "id": "item-13", "phase": "final_answer",
                                     "content": [{"type": "text", "text": "Done"}]}},
            }
            message = codex_message(native="item-14", thread=thread, text=text)
            message["timestamp"] = f"{date}T10:01:00Z"
            root, _ = self.source("codex", thread, [session, reply, message])
            roots.append(root)
        result = import_archive(self.con, roots)
        self.assertEqual(result["messages"], 2)
        self.assertEqual(self.count("agent_replies"), 2)
        self.assertEqual(self.count("message_contexts"), 2)
        rows = self.con.execute('''
            SELECT m.text,m.sent_at,t.native_id,r.sent_at,r.native_thread_id
            FROM preferred_message_contexts c
            JOIN messages m ON m.id=c.message_id
            JOIN threads t ON t.id=c.thread_id
            JOIN agent_replies r ON r.id=c.reply_id ORDER BY t.native_id
        ''').fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("Build the first project", timestamp("2026-05-04T10:01:00Z"), "thread-a",
             timestamp("2026-05-04T10:00:00Z"), "thread-a"),
            ("Change the second project", timestamp("2026-07-21T10:01:00Z"), "thread-b",
             timestamp("2026-07-21T10:00:00Z"), "thread-b"),
        ])

    def test_repeat_import_is_unchanged_and_search_works(self):
        root, _ = self.source("claude", "claude", [claude_message(text="Build accessible project search")])
        first = import_archive(self.con, [root])
        second = import_archive(self.con, [root])
        self.assertEqual(first["files_imported"], 1)
        self.assertEqual(second["files_unchanged"], 1)
        self.assertEqual(second["files_imported"], 0)
        self.assertEqual(self.count("messages"), 1)
        self.assertEqual(self.count("message_sources"), 1)
        found = self.con.execute("SELECT text FROM messages_fts WHERE messages_fts MATCH 'accessible'").fetchall()
        self.assertEqual([r[0] for r in found], ["Build accessible project search"])

    def test_malformed_tail_retains_good_messages_and_retries_the_source(self):
        root, path = self.source("claude", "claude", [claude_message()])
        with path.open("a") as stream:
            stream.write('{"type":')
        result = import_archive(self.con, [root])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["malformed_lines"], 1)
        self.assertEqual(result["messages"], 1)
        issue = self.con.execute("SELECT kind,line FROM import_issues").fetchone()
        self.assertEqual(tuple(issue), ("partial_last_line", 2))
        # A complete final line arriving later must import on the next run.
        with path.open("a") as stream:
            stream.write('"user","uuid":"later","sessionId":"claude-thread","timestamp":"2026-10-15T08:01:00Z","cwd":"/example/project","message":{"role":"user","content":"Continue"}}\n')
        repaired = import_archive(self.con, [root])
        self.assertEqual(repaired["status"], "complete")
        self.assertEqual(repaired["messages"], 2)

    def test_missing_timestamp_is_unknown_and_never_fabricated(self):
        record = claude_message()
        del record["timestamp"]
        root, _ = self.source("claude", "claude", [record])
        result = import_archive(self.con, [root])
        self.assertEqual(result["messages"], 1)
        message = self.con.execute("SELECT sent_at,sent_at_raw FROM messages").fetchone()
        self.assertEqual(tuple(message), (None, None))
        thread = self.con.execute("SELECT started_at,last_message_at FROM threads").fetchone()
        self.assertEqual(tuple(thread), (None, None))

    def test_compaction_does_not_destroy_an_already_imported_action(self):
        root, path = self.source("claude", "claude", [claude_message("first"), claude_message("second", text="Make it blue")])
        import_archive(self.con, [root])
        path.write_text(json.dumps(claude_message("second", text="Make it blue")) + "\n")
        result = import_archive(self.con, [root])
        self.assertEqual(result["messages"], 2)
        self.assertEqual(self.count("message_sources"), 3)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM message_sources WHERE is_current=1').fetchone()[0], 1)
        self.assertEqual(self.count("thread_messages"), 2)

    def test_review_inputs_do_not_enter_human_counts(self):
        root, _ = self.source("claude", "claude", [claude_message(), claude_message("automated", entrypoint="sdk-cli"), claude_message("tool", toolUseResult={})])
        result = import_archive(self.con, [root])
        self.assertEqual((result["messages"], result["review_messages"]), (1, 1))
        skipped = json.loads(self.con.execute("SELECT skipped_json FROM source_files").fetchone()[0])
        self.assertEqual(skipped["tool_result"], 1)

    def test_stray_session_metadata_prefix_is_recovered_without_guessing_message_text(self):
        root, path = self.source('codex', '.codex', [codex_session(), codex_message()])
        path.write_bytes(b' Terms' + path.read_bytes())
        result = import_archive(self.con, [root])
        self.assertEqual(result['messages'], 1)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['recovered_metadata_lines'], 1)
        self.assertEqual(self.con.execute('SELECT kind FROM import_issues').fetchone()[0], 'recovered_metadata_prefix')

    def test_prefixed_user_fragment_is_not_guessed_as_a_human_message(self):
        root, path = self.source('claude', '.claude', [claude_message()])
        path.write_bytes(b'broken prefix ' + path.read_bytes())
        result = import_archive(self.con, [root])
        self.assertEqual(result['messages'], 0)
        self.assertEqual(result['malformed_lines'], 1)

    def test_history_adds_unmatched_review_input_and_not_another_transcript_action(self):
        root, _ = self.source('claude', '.claude', [claude_message()])
        history = [
            {'display': 'Add project search', 'timestamp': 1792051200000, 'project': '/example/project', 'sessionId': 'claude-thread'},
            {'display': 'Uncovered history-only action', 'timestamp': 1792051260000, 'project': '/example/project', 'sessionId': 'missing-thread'},
        ]
        (root.path / 'history.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in history))
        result = import_archive(self.con, [root])
        self.assertEqual(result['messages'], 1)
        self.assertEqual(result['review_messages'], 1)
        self.assertEqual(self.count('messages'), 2)

    def test_audit_files_are_imported_and_deduplicate_with_transcripts(self):
        root, _ = self.source("claude", "local-agent-mode-sessions", [claude_message()], file="project/transcript.jsonl")
        audit = root.path / "audit.jsonl"
        audit_record = claude_message()
        audit_record["session_id"] = audit_record.pop("sessionId")
        audit_record["_audit_timestamp"] = audit_record.pop("timestamp")
        audit.write_text(json.dumps(audit_record) + "\n")
        # Use the directory containing both transcript/audit, matching Cowork's
        # discovery root, which has no top-level projects folder.
        direct = SourceRoot("claude", root.path)
        project_path = root.path / "projects"
        project_path.rename(root.path / "local_1")
        result = import_archive(self.con, [direct])
        self.assertEqual(result["files_discovered"], 2)
        self.assertEqual(result["messages"], 1)
        self.assertEqual(self.count("message_sources"), 2)


if __name__ == "__main__":
    unittest.main()
