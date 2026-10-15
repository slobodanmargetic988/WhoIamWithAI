import json
import unittest

from ai_archive.claude_parser import ClaudeHistoryParser, ClaudeParser


def user(content="Please add a search box.", **extra):
    return {"type": "user", "uuid": "u1", "sessionId": "thread-1", "timestamp": "2026-10-15T08:00:00.000Z", "cwd": "/projects/demo", "message": {"role": "user", "content": content}, **extra}


class ClaudeParserTests(unittest.TestCase):
    def test_text_with_chronology_and_metadata(self):
        parser = ClaudeParser()
        self.assertEqual(parser.feed({"type": "ai-title", "aiTitle": "Search", "sessionId": "thread-1"}, 1), [])
        message = parser.feed(user(), 2)[0]
        self.assertEqual((message["text"], message["origin"], message["ordinal"]), ("Please add a search box.", "human", 2))
        self.assertEqual((message["thread_native_id"], message["cwd"], message["title"]), ("thread-1", "/projects/demo", "Search"))
        parser.feed({"type": "custom-title", "customTitle": "My search"}, 3)
        parser.feed({"type": "ai-title", "aiTitle": "Replacement"}, 4)
        self.assertEqual(parser.metadata["title"], "My search")

    def test_tool_result_is_never_a_human_message(self):
        for extra, content in [({"toolUseResult": {}}, "tool output"), ({}, [{"type": "tool_result", "content": "output"}]), ({"tool_use_result": []}, "audit output")]:
            with self.subTest(extra=extra, content=content):
                parser = ClaudeParser()
                self.assertEqual(parser.feed(user(content, **extra), 1), [])
                self.assertEqual(parser.skipped["tool_result"], 1)

    def test_subagents_and_generated_events_are_excluded(self):
        cases = [
            user(isSidechain=True), user(isMeta=True), user(isCompactSummary=True),
            user(isSynthetic=True), user(parent_tool_use_id="tool-1"),
            user(origin={"kind": "task-notification"}),
            user("<task-notification><summary>Finished</summary></task-notification>"),
            user("[Request interrupted by user for tool use]"),
            user("<local-command-stdout>Output</local-command-stdout>"),
            user("This session is being continued from a previous conversation which ran out of context."),
        ]
        for record in cases:
            self.assertEqual(ClaudeParser().feed(record, 1), [])
        parser = ClaudeParser("/home/.claude/projects/demo/thread-1/subagents/agent-1.jsonl")
        self.assertEqual(parser.feed(user(), 1), [])
        self.assertEqual(parser.skipped["agent_input"], 1)

    def test_image_and_document_only_messages_survive_without_binary_payload(self):
        parser = ClaudeParser()
        raw = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "PRIVATE_BINARY"}}, {"type": "document", "title": "Plan", "source": {"data": "PRIVATE_DOCUMENT", "media_type": "application/pdf"}}]
        message = parser.feed(user(raw), 4)[0]
        self.assertIn("[Image attachment]", message["text"])
        self.assertIn("[Document attachment: Plan]", message["text"])
        self.assertNotIn("PRIVATE", json.dumps(message["content"]))
        self.assertEqual(message["content"][0]["source"]["media_type"], "image/png")

    def test_unknown_automation_requires_review_but_explicit_human_overrides_sdk(self):
        for extra, content in [({"entrypoint": "sdk-cli"}, "Add search"), ({"promptSource": "sdk"}, "Add search"), ({}, "<scheduled-task>Do work</scheduled-task>"), ({"origin": {"kind": "future-type"}}, "Add search")]:
            message = ClaudeParser().feed(user(content, **extra), 1)[0]
            self.assertEqual(message["origin"], "review")
        explicit = ClaudeParser().feed(user(promptSource="sdk", origin={"kind": "human"}), 1)[0]
        self.assertEqual(explicit["origin"], "human")

    def test_slash_command_remains_an_action(self):
        message = ClaudeParser().feed(user("<command-name>/model</command-name><command-args>opus</command-args>"), 1)[0]
        self.assertEqual(message["text"], "/model opus")

    def test_audit_format_and_timestamp_fallback(self):
        record = user()
        record["session_id"] = record.pop("sessionId")
        record["_audit_timestamp"] = record.pop("timestamp")
        record["parent_tool_use_id"] = None
        message = ClaudeParser("audit.jsonl").feed(record, 9)[0]
        self.assertEqual(message["thread_native_id"], "thread-1")
        self.assertEqual(message["sent_at"], "2026-10-15T08:00:00.000Z")

    def test_quoted_machine_markers_in_human_text_are_preserved(self):
        message = ClaudeParser().feed(user("Explain the <task-notification> tag."), 1)[0]
        self.assertEqual(message["origin"], "human")

    def test_missing_uuid_keeps_repeated_actions_distinct_and_stable(self):
        record = user()
        del record["uuid"]
        parser = ClaudeParser()
        first = parser.feed(record, 1)[0]
        self.assertEqual(first["native_id"], ClaudeParser().feed(record, 1)[0]["native_id"])
        self.assertNotEqual(first["native_id"], parser.feed(record, 2)[0]["native_id"])


class ClaudeHistoryParserTests(unittest.TestCase):
    def history(self, **extra):
        return {"display": "Add a project timeline", "pastedContents": {}, "timestamp": 0,
                "project": "/projects/demo", "sessionId": "history-thread", **extra}

    def test_fallback_is_review_with_recorded_time_project_and_thread(self):
        parser = ClaudeHistoryParser("/profile/history.jsonl")
        message = parser.feed(self.history(), 3)[0]
        self.assertEqual((message["origin"], message["reason"]), ("review", "history_only_unverified"))
        self.assertEqual((message["thread_native_id"], message["cwd"], message["source_line"]), ("history-thread", "/projects/demo", 3))
        self.assertEqual(message["sent_at"], "1970-01-01T00:00:00Z")
        self.assertTrue(message["native_id"].startswith("claude-history:"))

    def test_native_id_survives_profile_copies_and_line_changes(self):
        first = ClaudeHistoryParser("/profile-a/history.jsonl").feed(self.history(), 1)[0]
        copied = ClaudeHistoryParser("/profile-b/history.jsonl").feed(self.history(), 9)[0]
        later = ClaudeHistoryParser().feed(self.history(timestamp=1000), 1)[0]
        self.assertEqual(first["native_id"], copied["native_id"])
        self.assertNotEqual(first["native_id"], later["native_id"])

    def test_missing_session_and_timestamp_are_explicitly_unknown(self):
        parser = ClaudeHistoryParser()
        record = self.history()
        del record["sessionId"]
        del record["timestamp"]
        message = parser.feed(record, 2)[0]
        self.assertTrue(message["thread_native_id"].startswith("history-unassigned:"))
        self.assertIsNone(message["sent_at"])
        self.assertEqual(parser.warnings["missing_thread_id"], 1)
        self.assertEqual(parser.warnings["missing_timestamp"], 1)

    def test_pasted_text_is_recovered_and_image_bytes_are_never_retained(self):
        raw = self.history(display="Please use [Pasted text #1 +3 lines]", pastedContents={
            "1": {"id": 1, "type": "text", "content": "accessible search"},
            "2": {"id": 2, "type": "image", "content": "SECRET_BASE64", "data": "SECRET_BASE64", "mediaType": "image/png", "source": {"type": "base64", "data": "SECRET_BASE64"}},
        })
        message = ClaudeHistoryParser().feed(raw, 1)[0]
        self.assertEqual(message["text"], "Please use accessible search")
        self.assertNotIn("SECRET", json.dumps(message))
        self.assertEqual(message["content"][-1]["mediaType"], "image/png")

    def test_image_only_history_remains_visible(self):
        message = ClaudeHistoryParser().feed(self.history(display="", pastedContents={"1": {"type": "image", "content": "SECRET_BASE64"}}), 1)[0]
        self.assertEqual(message["text"], "[Image attachment]")
        self.assertNotIn("SECRET", json.dumps(message))

    def test_empty_and_non_history_records_are_skipped(self):
        parser = ClaudeHistoryParser()
        self.assertEqual(parser.feed({"type": "user"}, 1), [])
        self.assertEqual(parser.feed(self.history(display=""), 2), [])
        self.assertEqual(parser.feed(self.history(display=123), 3), [])


if __name__ == "__main__":
    unittest.main()
