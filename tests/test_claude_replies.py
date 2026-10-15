import unittest

from ai_archive.claude_parser import ClaudeParser
from ai_archive.claude_replies import ClaudeReplyContext


def user(text="Implement steps one to three.", uuid="u1", **extra):
    return {"type": "user", "uuid": uuid, "sessionId": "thread-1",
            "timestamp": "2026-10-15T09:00:00Z", "message": {"role": "user", "content": text}, **extra}


def assistant(text="Here is the plan.", native="a1", stop="end_turn", **extra):
    return {"type": "assistant", "uuid": native + "-event", "sessionId": "thread-1",
            "timestamp": "2026-10-15T08:59:00Z", "message": {
                "id": native, "role": "assistant", "stop_reason": stop,
                "content": [{"type": "text", "text": text}]}, **extra}


def enrich(records, path=""):
    parser, context = ClaudeParser(path), ClaudeReplyContext(path)
    candidates = []
    for line, record in enumerate(records, 1):
        candidates.extend(parser.feed(record, line))
        context.feed(record, line, f"sha-{line}")
    return context.finalize(candidates)


class ClaudeReplyContextTests(unittest.TestCase):
    def test_original_final_reply_is_attached_to_next_input_only(self):
        records = [user("Make a plan.", uuid="u0"), assistant(), user()]
        first, second = enrich(records)
        self.assertIsNone(first["previous_reply"])
        reply = second["previous_reply"]
        self.assertEqual((reply["text"], reply["native_id"], reply["source_line"]), ("Here is the plan.", "a1", 2))
        self.assertEqual((reply["selection_reason"], reply["evidence_rank"], reply["kind"]), ("claude_end_turn", 3, "final"))
        self.assertEqual(reply["record_sha256"], "sha-2")

    def test_final_blocks_exclude_thinking_and_preserve_provenance(self):
        thinking = assistant()
        thinking["message"]["content"] = [{"type": "thinking", "thinking": "private thought"}]
        text1 = assistant("Step one.", uuid="text1")
        text2 = assistant("Step two.", uuid="text2")
        candidate = enrich([thinking, text1, text2, user()])[0]
        reply = candidate["previous_reply"]
        self.assertEqual(reply["text"], "Step one.\nStep two.")
        self.assertNotIn("private thought", str(reply))
        self.assertEqual(reply["source_line"], 2)
        self.assertEqual([row["line"] for row in reply["source_records"]], [1, 2, 3])

    def test_streaming_snapshots_do_not_duplicate_text(self):
        first = assistant("Step one", stop=None)
        second = assistant("Step one. Step two.", uuid="event2")
        self.assertEqual(enrich([first, second, user()])[0]["previous_reply"]["text"], "Step one. Step two.")

    def test_tool_calls_and_outputs_are_not_replies(self):
        tool = assistant("I will inspect the code.", native="tool", stop="tool_use")
        tool["message"]["content"].append({"type": "tool_use", "id": "t1", "name": "read", "input": {"secret": "tool input"}})
        result = user([{"type": "tool_result", "tool_use_id": "t1", "content": "sensitive output"}], uuid="tool-result")
        final = assistant("Implemented and checked.", native="final")
        reply = enrich([tool, result, final, user()])[0]["previous_reply"]
        self.assertEqual(reply["text"], "Implemented and checked.")
        self.assertNotIn("sensitive", str(reply))

    def test_incomplete_work_does_not_reuse_an_older_final(self):
        results = enrich([assistant(), user(uuid="first"), assistant("Working...", native="tool", stop="tool_use"), user(uuid="second")])
        self.assertIsNotNone(results[0]["previous_reply"])
        self.assertIsNone(results[1]["previous_reply"])
        self.assertEqual(results[1]["previous_reply_status"], "previous_turn_incomplete")

    def test_consecutive_inputs_keep_the_preceding_completed_reply(self):
        candidates = enrich([assistant(), user(uuid="first"), user(uuid="second")])
        self.assertEqual(candidates[0]["previous_reply"], candidates[1]["previous_reply"])

    def test_unflagged_text_at_next_human_boundary_is_not_assumed_final(self):
        candidate = enrich([assistant(stop=None), user()])[0]
        self.assertIsNone(candidate["previous_reply"])
        self.assertEqual(candidate["previous_reply_status"], "previous_turn_incomplete")

    def test_stop_hook_is_recorded_as_completion_evidence(self):
        hook = {"type": "system", "subtype": "stop_hook_summary", "sessionId": "thread-1", "preventedContinuation": False, "hookErrors": []}
        reply = enrich([assistant(stop=None), hook, user()])[0]["previous_reply"]
        # A later boundary must preserve the stronger completion evidence.
        self.assertEqual((reply["selection_reason"], reply["evidence_rank"]), ("claude_stop_hook_completed", 2))
        self.assertEqual(reply["source_records"][-1]["line"], 2)

    def test_failed_or_truncated_responses_are_unavailable(self):
        for stop, extra in [("max_tokens", {}), ("end_turn", {"isApiErrorMessage": True}), (None, {"isSynthetic": True})]:
            with self.subTest(stop=stop, extra=extra):
                self.assertIsNone(enrich([assistant(stop=stop, **extra), user()])[0]["previous_reply"])

    def test_sidechains_and_compaction_summary_are_not_context(self):
        records = [assistant(), assistant("Subagent output", native="side", isSidechain=True),
                   user("Synthetic compacted history", uuid="compact", isCompactSummary=True),
                   assistant("Generated compacted reply", native="compact-a", isCompactSummary=True), user()]
        self.assertEqual(enrich(records)[0]["previous_reply"]["text"], "Here is the plan.")
        self.assertEqual(enrich([assistant(), user()], "/tmp/subagents/agent-1.jsonl"), [])

    def test_parent_lineage_selects_the_reply_on_the_actual_branch(self):
        first = assistant("Original plan.", native="a1")
        branch_input = user("Change that plan", uuid="branch-user", parentUuid="a1-event")
        unrelated = assistant("Other branch output.", native="a2")
        current = user(parentUuid="branch-user")
        reply = enrich([first, branch_input, unrelated, current])[-1]["previous_reply"]
        self.assertEqual(reply["text"], "Original plan.")

    def test_unknown_parent_or_explicit_root_does_not_guess_from_other_branch(self):
        for parent in ("not-in-source", None):
            with self.subTest(parent=parent):
                self.assertIsNone(enrich([assistant(), user(parentUuid=parent)])[0]["previous_reply"])

    def test_session_scope_and_future_timestamp_are_checked(self):
        other = assistant(sessionId="other-thread")
        self.assertIsNone(enrich([other, user()])[0]["previous_reply"])
        future = assistant(timestamp="2026-10-15T10:00:00Z")
        candidate = enrich([future, user()])[0]
        self.assertEqual(candidate["previous_reply_status"], "previous_reply_after_message")

    def test_audit_result_recovers_original_final_and_audit_timestamp(self):
        reply = assistant(stop=None)
        reply.pop("sessionId")
        reply.pop("timestamp")
        reply.update(session_id="thread-1", _audit_timestamp="2026-10-15T08:59:00Z")
        result = {"type": "result", "uuid": "result1", "session_id": "thread-1", "_audit_timestamp": "2026-10-15T08:59:01Z",
                  "subtype": "success", "is_error": False, "stop_reason": "end_turn", "result": "Here is the plan."}
        recovered = enrich([reply, result, user()])[0]["previous_reply"]
        self.assertEqual((recovered["text"], recovered["selection_reason"], recovered["evidence_rank"]), ("Here is the plan.", "claude_success_result", 3))
        self.assertEqual(recovered["source_records"][-1]["line"], 2)

    def test_result_error_does_not_expose_success_looking_text(self):
        result = {"type": "result", "session_id": "thread-1", "subtype": "success", "is_error": True,
                  "stop_reason": "stop_sequence", "result": "Failed task detail"}
        candidate = enrich([assistant(stop=None), result, user()])[0]
        self.assertIsNone(candidate["previous_reply"])
        self.assertEqual(candidate["previous_reply_status"], "previous_turn_failed")

    def test_only_later_human_candidates_are_enriched(self):
        context = ClaudeReplyContext()
        context.feed(assistant(), 2)
        candidates = [{"source_line": 1, "thread_native_id": "thread-1"}]
        self.assertIs(context.finalize(candidates), candidates)
        self.assertIsNone(candidates[0]["previous_reply"])


if __name__ == "__main__":
    unittest.main()
