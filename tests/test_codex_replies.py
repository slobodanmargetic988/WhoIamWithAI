import json
import unittest

from ai_archive.codex_parser import CodexParser
from ai_archive.codex_replies import CodexReplyContext


def record(kind, payload, timestamp="2026-10-08T08:00:00Z", **values):
    return {"type": kind, "payload": payload, "timestamp": timestamp, **values}


def user(text, native_id, **values):
    return record("event_msg", {"type": "item_completed", "thread_id": "thread-a",
                               "item": {"type": "UserMessage", "id": native_id,
                                        "content": [{"type": "text", "text": text}]}, **values})


def answer(text, phase="final_answer", **values):
    return record("response_item", {"type": "message", "role": "assistant", "phase": phase,
                                    "content": [{"type": "output_text", "text": text}], **values})


class CodexReplyContextTests(unittest.TestCase):
    def parse(self, records, **kwargs):
        parser = CodexParser(thread_native_id="thread-a")
        context = CodexReplyContext(thread_native_id="thread-a", **kwargs)
        for line, item in enumerate(records, 1):
            parser.feed(item, line)
            self.assertIsNone(context.feed(item, line, record_sha256=f"sha-{line}"))
        candidates = parser.finalize()
        self.assertIs(context.finalize(candidates), candidates)
        return context, candidates

    def test_first_message_has_no_reply_then_followup_has_verbatim_final(self):
        original = "A **plan**:\n\n1. Keep spacing.\n2. Build it.\n"
        context, rows = self.parse([user("Make a plan", "u1"), answer(original),
                                    user("Implement steps 1 and 2", "u2")])
        self.assertIsNone(rows[0]["previous_reply"])
        self.assertEqual(rows[0]["previous_reply_status"], "no_previous_agent_reply")
        reply = rows[1]["previous_reply"]
        self.assertEqual(reply["text"], original)
        self.assertEqual(reply["source_line"], 2)
        self.assertEqual(reply["record_sha256"], "sha-2")
        self.assertEqual(reply["kind"], "final")
        self.assertEqual(reply["selection_reason"], "explicit_final_response_item")
        self.assertEqual(rows[1]["previous_reply_status"], "available")
        self.assertEqual(context.replies, [reply])

    def test_explicit_final_channel_is_supported(self):
        _, rows = self.parse([answer("Done", phase=None, channel="final"), user("Next", "u1")])
        self.assertEqual(rows[0]["previous_reply"]["text"], "Done")

    def test_commentary_reasoning_and_tool_outputs_never_become_replies(self):
        records = [user("Start", "u1"), answer("Interim update", "commentary"),
                   record("response_item", {"type": "reasoning", "summary": [{"text": "private reasoning"}]}),
                   record("response_item", {"type": "function_call_output", "output": "tool result"}),
                   user("Continue", "u2")]
        context, rows = self.parse(records)
        self.assertIsNone(rows[1]["previous_reply"])
        self.assertEqual(rows[1]["previous_reply_status"], "previous_turn_without_final_reply")
        self.assertEqual(context.replies, [])
        self.assertNotIn("private reasoning", json.dumps(rows))
        self.assertNotIn("tool result", json.dumps(rows))

    def test_old_final_is_not_reused_after_a_tool_only_turn(self):
        _, rows = self.parse([answer("Old plan"), user("Implement it", "u1"),
                              record("event_msg", {"type": "item_completed", "item": {
                                  "type": "CommandExecution", "aggregated_output": "work"}}),
                              user("Any progress?", "u2")])
        self.assertEqual(rows[0]["previous_reply"]["text"], "Old plan")
        self.assertIsNone(rows[1]["previous_reply"])

    def test_consecutive_followups_keep_the_last_visible_final(self):
        context, rows = self.parse([answer("A plan"), user("Implement it", "u1"),
                                    user("Also add a footer", "u2")])
        self.assertIs(rows[0]["previous_reply"], rows[1]["previous_reply"])
        self.assertEqual(len(context.replies), 1)

    def test_same_timestamp_uses_source_order_and_never_a_future_reply(self):
        _, rows = self.parse([user("First", "u1"), answer("First response"),
                              user("Second", "u2"), answer("Second response"), user("Third", "u3")])
        self.assertIsNone(rows[0]["previous_reply"])
        self.assertEqual(rows[1]["previous_reply"]["text"], "First response")
        self.assertEqual(rows[2]["previous_reply"]["text"], "Second response")

    def test_modern_completed_item_beats_response_and_event_mirrors(self):
        modern = record("event_msg", {"type": "item_completed", "thread_id": "thread-a",
                                       "completed_at_ms": 1791446400123,
                                       "item": {"type": "AgentMessage", "phase": "final_answer",
                                                "id": "agent-item", "content": [{"type": "text", "text": "Done"}]}})
        context, rows = self.parse([
            record("event_msg", {"type": "agent_message", "phase": "final_answer", "message": "Done"}),
            modern, answer("Done"),
            record("event_msg", {"type": "task_complete", "last_agent_message": "Done"}),
            user("Thanks", "u1", completed_at_ms=1791446401123)])
        reply = rows[0]["previous_reply"]
        self.assertEqual(reply["native_id"], "agent-item")
        self.assertEqual(reply["source_line"], 2)
        self.assertEqual(reply["sent_at"], "2026-10-08T08:00:00.123000Z")
        self.assertEqual(reply["selection_reason"], "explicit_final_completed_item")
        self.assertEqual(reply["record_sha256"], "sha-2")
        self.assertEqual(len(context.replies), 1)

    def test_task_complete_recovers_an_older_unlabeled_final(self):
        _, rows = self.parse([user("Build", "u1"), answer("Finished", phase=None),
                              record("event_msg", {"type": "task_complete", "last_agent_message": "Finished"}),
                              user("Add something", "u2")])
        reply = rows[1]["previous_reply"]
        self.assertEqual(reply["text"], "Finished")
        self.assertEqual(reply["source_line"], 3)
        self.assertEqual(reply["selection_reason"], "task_complete_last_agent_message")

    def test_unlabeled_assistant_without_completion_is_not_assumed_final(self):
        _, rows = self.parse([answer("Might be an interim message", phase=None), user("Continue", "u1")])
        self.assertIsNone(rows[0]["previous_reply"])

    def test_unlabeled_duplicate_projection_keeps_the_explicit_final(self):
        _, rows = self.parse([answer("Finished"), answer("Finished", phase=None), user("Next", "u1")])
        self.assertEqual(rows[0]["previous_reply"]["selection_reason"], "explicit_final_response_item")
        self.assertEqual(rows[0]["previous_reply"]["source_line"], 1)

    def test_identical_answer_from_a_new_turn_gets_its_own_provenance(self):
        context, rows = self.parse([answer("Done"), user("Try again", "u1"),
                                    answer("Done"), user("Next", "u2")])
        self.assertEqual(rows[0]["previous_reply"]["source_line"], 1)
        self.assertEqual(rows[1]["previous_reply"]["source_line"], 3)
        self.assertEqual(len(context.replies), 2)

    def test_multiple_final_fragments_choose_last_completed_reply(self):
        _, rows = self.parse([answer("First final fragment"), answer("Last final fragment"),
                              user("Next", "u1")])
        self.assertEqual(rows[0]["previous_reply"]["text"], "Last final fragment")

    def test_completion_does_not_promote_known_commentary(self):
        _, rows = self.parse([answer("Working on it", phase="commentary"),
                              record("event_msg", {"type": "task_complete", "last_agent_message": "Working on it"}),
                              user("Next", "u1")])
        self.assertIsNone(rows[0]["previous_reply"])

    def test_completion_with_no_final_clears_an_older_reply(self):
        _, rows = self.parse([answer("Old answer"), user("Try it", "u1"),
                              record("event_msg", {"type": "task_complete", "last_agent_message": None}),
                              user("Again", "u2")])
        self.assertIsNone(rows[1]["previous_reply"])
        self.assertEqual(rows[1]["previous_reply_status"], "previous_turn_without_final_reply")

    def test_empty_completion_keeps_an_explicit_final_from_its_turn(self):
        _, rows = self.parse([user("Try it", "u1"), answer("Finished"),
                              record("event_msg", {"type": "task_complete", "last_agent_message": None}),
                              user("Again", "u2")])
        self.assertEqual(rows[1]["previous_reply"]["text"], "Finished")

    def test_interruption_clears_old_reply_and_cannot_be_revived_by_completion(self):
        _, rows = self.parse([answer("Old answer"), user("Try it", "u1"),
                              record("event_msg", {"type": "turn_aborted"}),
                              record("response_item", {"type": "function_call_output", "output": "late tool end"}),
                              record("event_msg", {"type": "task_complete", "last_agent_message": "Old answer"}),
                              user("Again", "u2")])
        self.assertIsNone(rows[1]["previous_reply"])
        self.assertEqual(rows[1]["previous_reply_status"], "previous_turn_interrupted")

    def test_started_turn_does_not_hide_reply_before_its_first_user_input(self):
        _, rows = self.parse([answer("Plan"), record("event_msg", {"type": "task_started", "turn_id": "new"}),
                              user("Implement it", "u1", turn_id="new")])
        self.assertEqual(rows[0]["previous_reply"]["text"], "Plan")

    def test_empty_autonomous_completion_is_a_boundary_even_without_turn_id(self):
        _, rows = self.parse([answer("Old answer"), record("event_msg", {"type": "task_started"}),
                              record("event_msg", {"type": "task_complete", "last_agent_message": None}),
                              user("Next", "u1")])
        self.assertIsNone(rows[0]["previous_reply"])

    def test_foreign_thread_reply_and_activity_are_ignored(self):
        _, rows = self.parse([answer("Own answer"), answer("Foreign answer", thread_id="thread-b"),
                              record("event_msg", {"type": "turn_aborted", "thread_id": "thread-b"}),
                              user("Next", "u1")])
        self.assertEqual(rows[0]["previous_reply"]["text"], "Own answer")
        self.assertEqual(rows[0]["previous_reply"]["thread_native_id"], "thread-a")

    def test_first_new_message_in_fork_uses_copied_parent_final_as_context(self):
        own_meta = record("session_meta", {"id": "thread-a", "forked_from_id": "parent",
                                            "timestamp": "2026-10-08T08:00:00Z"})
        _, rows = self.parse([own_meta, record("session_meta", {"id": "parent"}),
                              record("response_item", {"type": "message", "role": "assistant", "phase": "final_answer",
                                                         "content": [{"type": "output_text", "text": "Parent reply"}]},
                                     timestamp="2026-10-08T07:59:00Z"), user("Own new request", "u1")])
        self.assertEqual(rows[0]["previous_reply"]["text"], "Parent reply")
        self.assertEqual(rows[0]["previous_reply"]["thread_native_id"], "parent")

    def test_parent_reply_does_not_replace_child_context_after_child_work_starts(self):
        own_meta = record("session_meta", {"id": "thread-a", "forked_from_id": "parent",
                                            "timestamp": "2026-10-08T08:00:00Z"})
        parent_reply = answer("Unrelated later parent reply", thread_id="parent")
        parent_reply["timestamp"] = "2026-10-08T09:00:00Z"
        _, rows = self.parse([own_meta, user("New child work", "u1"), answer("Child answer"),
                              parent_reply, user("Child followup", "u2")])
        self.assertEqual(rows[1]["previous_reply"]["text"], "Child answer")

    def test_queued_user_with_earlier_create_time_never_gets_a_future_reply(self):
        future_reply = answer("Work finished after user sent followup")
        future_reply["timestamp"] = "2026-10-08T08:02:00Z"
        mirror = record("response_item", {"type": "message", "role": "user",
                                           "content": [{"type": "input_text", "text": "Actually, change this"}],
                                           "internal_chat_message_metadata_passthrough": {
                                               "turn_id": "queued-turn", "create_time": 1791446400.0}},
                        timestamp="2026-10-08T08:02:10Z")
        _, rows = self.parse([future_reply, mirror,
                              user("Actually, change this", "u1", turn_id="queued-turn",
                                   completed_at_ms=1791446530000)])
        self.assertEqual(rows[0]["sent_at"], "2026-10-08T08:00:00Z")
        self.assertIsNone(rows[0]["previous_reply"])
        self.assertEqual(rows[0]["previous_reply_status"], "reply_after_user_message")

    def test_subagent_history_ordinal_filters_copied_final(self):
        old = answer("Copied answer")
        old["ordinal"] = 5
        _, rows = self.parse([old, user("Own request", "u1")], metadata={"subagent_history_start_ordinal": 10})
        self.assertIsNone(rows[0]["previous_reply"])

    def test_final_addressed_to_tool_is_not_a_user_reply(self):
        _, rows = self.parse([answer("Tool instructions", recipient="functions.exec"), user("Continue", "u1")])
        self.assertIsNone(rows[0]["previous_reply"])

    def test_native_fallback_id_is_deterministic_across_rescans(self):
        records = [answer("Done"), user("Next", "u1")]
        _, first = self.parse(records)
        _, second = self.parse(records)
        self.assertEqual(first[0]["previous_reply"]["native_id"], second[0]["previous_reply"]["native_id"])

    def test_huge_interim_output_is_never_buffered(self):
        context = CodexReplyContext(thread_native_id="thread-a")
        marker = "PRIVATE_INTERIM_BODY" * 10000
        context.feed(answer(marker, "commentary"), 1)
        context.feed(record("response_item", {"type": "function_call_output", "output": marker}), 2)
        self.assertNotIn("PRIVATE_INTERIM_BODY", repr(context.__dict__))
        self.assertLess(len(repr(context.__dict__)), 2000)

    def test_reply_only_records_are_not_persisted_until_referenced(self):
        context, rows = self.parse([answer("No followup yet")])
        self.assertEqual(rows, [])
        self.assertEqual(context.replies, [])
        self.assertEqual(context._events, [])

    def test_finalized_reader_rejects_new_records(self):
        context, _ = self.parse([])
        with self.assertRaises(RuntimeError):
            context.feed(answer("Later"), 1)


if __name__ == "__main__":
    unittest.main()
