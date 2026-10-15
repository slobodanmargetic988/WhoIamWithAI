import unittest
import json

from ai_archive.codex_parser import CodexParser


def record(kind, payload, ordinal=None, timestamp="2026-10-08T07:52:12.282Z"):
    result = {"timestamp": timestamp, "type": kind, "payload": payload}
    if ordinal is not None:
        result["ordinal"] = ordinal
    return result


def meta(thread="thread-a", **values):
    return record("session_meta", {"id": thread, "session_id": thread,
                                   "cwd": "/projects/example", "source": "vscode", **values}, 0)


def modern(text="Build a page", **values):
    return record("event_msg", {"type": "item_completed", "thread_id": "thread-a",
                               "turn_id": "turn-a", "completed_at_ms": 1791445932282,
                               "item": {"type": "UserMessage", "id": "item-a",
                                        "client_id": "client-a", "content": [{"type": "text", "text": text}]},
                               **values}, 12)


class CodexParserTests(unittest.TestCase):
    def parse(self, records, **kwargs):
        parser = CodexParser(**kwargs)
        for number, item in enumerate(records, 1):
            self.assertEqual(parser.feed(item, number), [])
        return parser, parser.finalize()

    def test_modern_event_ignores_injected_context_and_response_mirror(self):
        response = record("response_item", {"type": "message", "role": "user", "id": "different-id",
                          "content": [{"type": "input_text", "text": "Build a page"}],
                          "internal_chat_message_metadata_passthrough": {
                              "turn_id": "turn-a", "create_time": 1791445932.176147}})
        parser, rows = self.parse([meta(), response, modern()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["native_id"], "item-a")
        self.assertEqual(rows[0]["origin"], "human")
        self.assertEqual(rows[0]["source_line"], 3)
        self.assertEqual(rows[0]["sent_at"], "2026-10-08T07:52:12.176147Z")
        self.assertEqual(parser.skipped["response_user_mirrors_or_context"], 1)

    def test_old_event_uses_human_text_once(self):
        event = record("event_msg", {"type": "user_message", "message": "Make the logo bigger",
                                   "images": [], "local_images": [], "text_elements": []})
        response = record("response_item", {"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": "Make the logo bigger"}]})
        _, rows = self.parse([meta(), response, event])
        self.assertEqual([row["text"] for row in rows], ["Make the logo bigger"])

    def test_mixed_file_keeps_historical_response_only_user_turn_for_review(self):
        old = record("response_item", {"type": "message", "role": "user", "id": "old-id",
                                      "content": [{"type": "input_text", "text": "Original request"}]},
                     timestamp="2026-10-01T00:00:00Z")
        _, rows = self.parse([meta(), old, modern()])
        self.assertEqual([(row["text"], row["origin"]) for row in rows],
                         [("Original request", "review"), ("Build a page", "human")])

    def test_mirror_matches_normalized_text_and_nearby_time(self):
        response = record("response_item", {"type": "message", "role": "user",
                                           "content": [{"type": "input_text", "text": "Build  a\npage"}]},
                          timestamp="2026-10-08T07:52:12.180Z")
        _, rows = self.parse([meta(), response, modern()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin"], "human")

    def test_child_meta_wins_over_later_copied_parent_meta(self):
        child = meta("child", session_id="parent", parent_thread_id="parent", forked_from_id="parent",
                     subagent_history_start_ordinal=10,
                     source={"subagent": {"thread_spawn": {"parent_thread_id": "parent", "depth": 1}}})
        parser, rows = self.parse([child, meta("parent"), modern(thread_id="child")], thread_native_id="child")
        self.assertEqual(parser.metadata["id"], "child")
        self.assertEqual(rows[0]["thread_native_id"], "child")
        self.assertEqual(rows[0]["parent_thread_id"], "parent")
        self.assertEqual(rows[0]["origin"], "review")

    def test_inherited_events_are_not_new_actions(self):
        inherited = modern(thread_id="parent")
        _, rows = self.parse([meta("child", subagent_history_start_ordinal=10), inherited], thread_native_id="child")
        self.assertEqual(rows, [])

    def test_legacy_fork_events_before_fork_creation_are_inherited(self):
        inherited = record("event_msg", {"type": "user_message", "message": "Original request"},
                           timestamp="2026-10-08T07:00:00Z")
        current = record("event_msg", {"type": "user_message", "message": "New fork request"},
                         timestamp="2026-10-08T08:00:00Z")
        metadata = meta(forked_from_id="parent", timestamp="2026-10-08T07:30:00Z")
        parser, rows = self.parse([metadata, inherited, current])
        self.assertEqual([row["text"] for row in rows], ["New fork request"])
        self.assertEqual(parser.skipped["inherited_history"], 1)

    def test_exec_prompts_are_review_not_asserted_human(self):
        _, rows = self.parse([meta(source="exec", originator="codex_exec"), modern()])
        self.assertEqual(rows[0]["reason"], "programmatic_exec_session")
        self.assertEqual(rows[0]["origin"], "review")

    def test_response_only_fallback_is_review_and_filters_injected_context(self):
        def response(text):
            return record("response_item", {"type": "message", "role": "user",
                                           "content": [{"type": "input_text", "text": text}]})
        parser, rows = self.parse([meta(), response("<environment_context>injected</environment_context>"),
                                  response("Add a billing page")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["origin"], "review")
        self.assertEqual(parser.skipped["injected_context"], 1)

    def test_repeated_prompts_at_different_times_remain_distinct(self):
        first = record("event_msg", {"type": "user_message", "message": "Continue"}, timestamp="2026-01-01T00:00:00Z")
        second = record("event_msg", {"type": "user_message", "message": "Continue"}, timestamp="2026-01-01T00:01:00Z")
        _, rows = self.parse([meta(), first, second])
        self.assertEqual(len(rows), 2)

    def test_legacy_same_timestamp_submissions_keep_occurrence_order(self):
        event = record("event_msg", {"type": "user_message", "message": "Continue"})
        _, rows = self.parse([meta(), event, event])
        self.assertEqual([row["ordinal"] for row in rows], [1, 2])

    def test_duplicate_projection_by_native_id_is_removed(self):
        parser, rows = self.parse([meta(), modern(), modern()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(parser.skipped["duplicate_user_event"], 1)

    def test_attachments_are_preserved(self):
        item = modern()
        item["payload"]["item"]["content"].append({"type": "local_image", "path": "/example/image.png"})
        _, rows = self.parse([meta(), item])
        self.assertEqual(rows[0]["content"][1]["path"], "/example/image.png")

    def test_inline_image_binary_is_not_copied_and_image_only_has_text(self):
        item = modern()
        item["payload"]["item"]["content"] = [{"type": "image", "image_url": "data:image/png;base64,SECRETIMAGE",
                                             "source": {"type": "base64", "data": "SECRETIMAGE"}}]
        _, rows = self.parse([meta(), item])
        self.assertEqual(rows[0]["text"], "[Image attachment]")
        self.assertNotIn("SECRETIMAGE", json.dumps(rows))

    def test_legacy_inline_image_binary_is_not_copied(self):
        item = record("event_msg", {"type": "user_message", "message": "", "images": ["data:image/png;base64,SECRETIMAGE"]})
        _, rows = self.parse([meta(), item])
        self.assertEqual(rows[0]["text"], "[Image attachment]")
        self.assertNotIn("SECRETIMAGE", json.dumps(rows))

    def test_heartbeat_is_review(self):
        _, rows = self.parse([meta(), modern("<heartbeat>Check progress</heartbeat>")])
        self.assertEqual(rows[0]["origin"], "review")

    def test_buffers_no_tool_or_assistant_output(self):
        parser, rows = self.parse([meta(), record("response_item", {"type": "function_call_output", "output": "x" * 10000}),
                                  record("response_item", {"type": "message", "role": "assistant", "content": []})])
        self.assertEqual(rows, [])
        self.assertEqual(parser._responses, [])


if __name__ == "__main__":
    unittest.main()
