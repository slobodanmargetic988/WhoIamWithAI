"""Keep the original completed Claude reply preceding each human input.

This adapter observes the same size-bounded stream as the user-message parser.
It retains final reply text and small lineage pointers, never tool results,
thinking blocks, or inline attachments. Unknown stop reasons require an
explicit completion marker; a new human input alone cannot establish that
preceding text was the completed response rather than interrupted narration.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any

from .claude_parser import ClaudeParser, _timestamp


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


class ClaudeReplyContext:
    """Feed records, then enrich the supplied user candidates in place.

    Same-message assistant fragments share ``message.id``. Their textual blocks
    are assembled before selecting a reply; tool-bearing messages never become
    final summaries. Parent UUIDs keep rewound branches from receiving a reply
    from another branch simply because it occurs nearby in the source file.
    """

    def __init__(self, source_path: str = "") -> None:
        self.source_path = str(source_path or "")
        self._parser = ClaudeParser(self.source_path)
        self._states: dict[str, dict[str, Any]] = {}
        self._groups: dict[tuple[str, str], tuple[dict[str, Any] | None, str]] = {}
        # UUID -> (parent UUID, session, assistant API message key). No bodies.
        self._nodes: dict[str, tuple[str | None, str, tuple[str, str] | None]] = {}
        self._contexts: dict[int, tuple[dict[str, Any] | None, str]] = {}
        self._ignored = "subagents" in Path(self.source_path).parts or Path(self.source_path).stem.startswith("agent-")

    @staticmethod
    def _source(line: int, digest: str | None) -> dict[str, Any]:
        source: dict[str, Any] = {"line": line}
        if digest:
            source["record_sha256"] = digest
        return source

    @staticmethod
    def _merge_text(group: dict[str, Any], pieces: list[str]) -> None:
        """Accept block fragments and cumulative snapshots without repetition."""
        if not pieces:
            return
        old = group["pieces"]
        if not old:
            old.extend(pieces)
        elif pieces == old or old[:len(pieces)] == pieces:
            return
        elif pieces[:len(old)] == old:
            group["pieces"] = list(pieces)
        elif len(pieces) == 1 and pieces[0].startswith(old[-1]):
            old[-1] = pieces[0]
        elif len(pieces) == 1 and old[-1].startswith(pieces[0]):
            return
        else:
            old.extend(pieces)

    def _commit(self, state: dict[str, Any], reason: str | None = None,
                rank: int = 0) -> tuple[dict[str, Any] | None, str]:
        group = state.get("pending")
        if not group:
            return state.get("reply"), state.get("status", "no_previous_reply")
        stop = group.get("stop_reason")
        if group.get("error"):
            status = "previous_turn_failed"
        elif group.get("has_tool"):
            status = "previous_turn_incomplete"
        elif stop not in {None, "end_turn", "stop_sequence"}:
            status = "previous_turn_incomplete"
        elif not any(piece.strip() for piece in group["pieces"]):
            status = "previous_reply_has_no_text"
        else:
            if stop == "end_turn":
                reason, rank = "claude_end_turn", 3
            elif stop == "stop_sequence":
                reason, rank = "claude_stop_sequence", 3
            previous, _ = self._groups.get(group["key"], (None, ""))
            if previous and previous["text"] == "\n".join(group["pieces"]) and previous["evidence_rank"] > rank:
                reason, rank = previous["selection_reason"], previous["evidence_rank"]
            if reason:
                reply = {
                    "native_id": group["native_id"],
                    "text": "\n".join(group["pieces"]),
                    "sent_at": group["sent_at"],
                    "thread_native_id": group["thread"],
                    "source_line": group["source_line"],
                    "kind": "final", "selection_reason": reason,
                    "evidence_rank": rank,
                    "source_records": list(group["source_records"]),
                }
                if group.get("record_sha256"):
                    reply["record_sha256"] = group["record_sha256"]
                state.update(reply=reply, status="available")
                self._groups[group["key"]] = (reply, "available")
                return reply, "available"
            status = "previous_turn_incomplete"
        state.update(reply=None, status=status)
        self._groups[group["key"]] = (None, status)
        return None, status

    def _preceding(self, record: dict[str, Any], thread: str,
                   state: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        parent = record.get("parentUuid")
        if parent is None:
            # An explicit root starts a new branch. Audit logs omit this field.
            if "parentUuid" in record:
                return None, "no_previous_reply"
            return state.get("reply"), state.get("status", "no_previous_reply")
        seen: set[str] = set()
        while isinstance(parent, str) and parent not in seen:
            seen.add(parent)
            node = self._nodes.get(parent)
            if node is None:
                return None, "previous_reply_lineage_missing"
            parent, node_thread, key = node
            if node_thread != thread:
                return None, "previous_reply_thread_mismatch"
            if key is not None:
                return self._groups.get(key, (None, "previous_turn_incomplete"))
        return None, "no_previous_reply"

    def feed(self, record: dict[str, Any], line: int = 0,
             record_sha256: str | None = None) -> None:
        if not isinstance(record, dict) or self._ignored:
            return
        found = self._parser.feed(record, line)
        if record.get("isSidechain") or record.get("parent_tool_use_id"):
            return
        thread = str(record.get("sessionId") or record.get("session_id")
                     or self._parser.metadata.get("thread_native_id")
                     or Path(self.source_path).stem or "unknown")
        state = self._states.setdefault(thread, {"reply": None, "status": "no_previous_reply"})
        kind = record.get("type")
        group_key = None

        if kind == "assistant" and not record.get("isCompactSummary") and not record.get("isMeta"):
            message = record.get("message")
            if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
                return
            native = str(message.get("id") or record.get("uuid") or "fallback:" + hashlib.sha256(
                f"{thread}:{line}".encode()).hexdigest())
            group_key = (thread, native)
            group = state.get("pending")
            if group is None or group["key"] != group_key:
                if group is not None:
                    self._commit(state)
                group = {"key": group_key, "native_id": native, "thread": thread,
                         "pieces": [], "has_tool": False, "error": False,
                         "source_records": [], "source_line": line,
                         "record_sha256": record_sha256, "sent_at": _timestamp(record),
                         "stop_reason": None}
                state.update(pending=group, reply=None, status="previous_turn_incomplete")
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            pieces = ([content] if isinstance(content, str) else [
                item["text"] for item in blocks if isinstance(item, dict)
                and item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str)
            ])
            if pieces and not group["pieces"]:
                group.update(source_line=line, record_sha256=record_sha256)
            self._merge_text(group, pieces)
            group["has_tool"] |= any(isinstance(item, dict) and item.get("type") in {"tool_use", "server_tool_use"} for item in blocks)
            group["error"] |= bool(record.get("isApiErrorMessage") or record.get("error") or record.get("isSynthetic"))
            if message.get("stop_reason") is not None:
                group["stop_reason"] = message["stop_reason"]
            if _timestamp(record):
                group["sent_at"] = _timestamp(record)
            if pieces or message.get("stop_reason") is not None:
                source = self._source(line, record_sha256)
                if source not in group["source_records"]:
                    group["source_records"].append(source)
            self._commit(state)

        elif kind == "result":
            group = state.get("pending")
            success = record.get("subtype") == "success" and record.get("is_error") is False
            stop = record.get("stop_reason")
            result = record.get("result")
            if success and stop in {"end_turn", "stop_sequence"} and isinstance(result, str) and result.strip():
                same = group is not None and "\n".join(group["pieces"]) == result and not group["has_tool"]
                native = group["native_id"] if same else str(record.get("uuid") or "result:" + hashlib.sha256(f"{thread}:{line}:{result}".encode()).hexdigest())
                source = self._source(line, record_sha256)
                reply = {"native_id": native, "text": result, "sent_at": _timestamp(record),
                         "thread_native_id": thread, "source_line": group["source_line"] if same else line,
                         "kind": "final", "selection_reason": "claude_success_result", "evidence_rank": 3,
                         "source_records": [*(group["source_records"] if same else []), source]}
                chosen_hash = group.get("record_sha256") if same else record_sha256
                if chosen_hash:
                    reply["record_sha256"] = chosen_hash
                state.update(reply=reply, status="available", pending=None)
                group_key = (thread, native)
                self._groups[group_key] = (reply, "available")
                if group:
                    self._groups[group["key"]] = (reply, "available")
            elif record.get("is_error") or record.get("subtype") != "success":
                if group:
                    group["error"] = True
                    self._commit(state)
                else:
                    state.update(reply=None, status="previous_turn_failed")

        elif kind == "system" and record.get("subtype") in {"stop_hook_summary", "turn_duration"}:
            if record.get("subtype") == "turn_duration" or (record.get("preventedContinuation") is False and not record.get("hookErrors")):
                group = state.get("pending")
                if group:
                    group["source_records"].append(self._source(line, record_sha256))
                    self._commit(state, "claude_stop_hook_completed" if record.get("subtype") == "stop_hook_summary" else "claude_turn_completed", 2)

        elif kind == "user" and not found:
            raw_content = record.get("message", {}).get("content") if isinstance(record.get("message"), dict) else None
            tool_result = "toolUseResult" in record or "tool_use_result" in record or (
                isinstance(raw_content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in raw_content))
            if tool_result and state.get("pending"):
                # A missing tool-use fragment must not turn its preceding
                # narration into a final reply just because work was interrupted.
                state["pending"]["has_tool"] = True
                self._commit(state)

        if found:
            self._commit(state)
            reply, status = self._preceding(record, thread, state)
            if reply is not None:
                reply_time, input_time = _epoch(reply.get("sent_at")), _epoch(found[0].get("sent_at"))
                if reply_time is not None and input_time is not None and reply_time > input_time:
                    reply, status = None, "previous_reply_after_message"
            self._contexts[line] = (reply, status)
            state.pop("pending", None)

        uuid = record.get("uuid")
        if isinstance(uuid, str) and uuid:
            parent = record.get("parentUuid")
            self._nodes[uuid] = (parent if isinstance(parent, str) else None, thread, group_key)

    def finalize(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for candidate in candidates:
            reply, status = self._contexts.get(candidate.get("source_line"), (None, "no_previous_reply_in_source"))
            if reply is not None and str(candidate.get("thread_native_id")) != reply["thread_native_id"]:
                reply, status = None, "previous_reply_thread_mismatch"
            candidate["previous_reply"] = reply
            candidate["previous_reply_status"] = status
        return candidates
