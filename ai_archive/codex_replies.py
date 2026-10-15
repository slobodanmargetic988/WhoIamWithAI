"""Recover the actual completed Codex reply visible before a human message.

This complements CodexParser: that parser decides which user records are real
messages, while this reader buffers only final replies and compact activity
markers. Reasoning, tool results, and interim commentary are never retained.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .codex_parser import _UUID, _epoch, _timestamp


_FINAL_PHASES = ("final", "final_answer")
_INTERRUPTED = {"turn_aborted", "turn_cancelled", "turn_canceled",
                "task_cancelled", "task_canceled", "task_interrupted"}
_ACTIVITY_PREFIXES = ("exec_command_", "mcp_tool_call_", "web_search_",
                      "apply_patch_", "collab_agent_", "image_generation_")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reply_text(content: Any) -> str:
    """Extract text without interpreting markup or inventing media descriptions."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(part["text"] for part in content
                     if isinstance(part, dict) and isinstance(part.get("text"), str)
                     and part.get("type", "text") in {"text", "output_text", "Text"})


class CodexReplyContext:
    """Associate finalized user candidates with the previous end-of-work reply.

    Source order, rather than timestamps, determines what a user could react
    to. A completed reply stays visible across consecutive follow-up messages
    until the agent starts responding. Once the agent has done work without a
    final reply, or the turn is interrupted, an older reply is not reused.

    ``replies`` contains the unique replies referenced by finalized candidates.
    ``record_sha256`` may be supplied by the streaming importer for exact raw
    line provenance; no tool or commentary body is kept in memory.
    """

    def __init__(self, thread_native_id: str | None = None,
                 metadata: dict[str, Any] | None = None,
                 source_path: str | Path | None = None):
        if thread_native_id is None and source_path is not None:
            match = _UUID.search(Path(source_path).name)
            thread_native_id = match.group(1) if match else None
        self.thread_native_id = thread_native_id
        self.metadata = dict(metadata or {})
        self.replies: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._turn_id: str | None = None
        self._own_meta = False
        self._finalized = False

    def feed(self, record: dict[str, Any], line_number: int = 0,
             record_sha256: str | None = None) -> None:
        if self._finalized:
            raise RuntimeError("Cannot feed a finalized reply context")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        kind, subtype = record.get("type"), payload.get("type")
        if kind == "session_meta":
            native = payload.get("id") or payload.get("session_id")
            if self.thread_native_id is None:
                self.thread_native_id = native
            if not self._own_meta and native == self.thread_native_id:
                for key in ("id", "session_id", "timestamp", "forked_from_id",
                            "parent_thread_id", "subagent_history_start_ordinal"):
                    if key in payload:
                        self.metadata[key] = payload[key]
                self._own_meta = True
            return
        if kind == "turn_context":
            if payload.get("thread_id") and self.thread_native_id and payload["thread_id"] != self.thread_native_id:
                return
            self._turn_id = payload.get("turn_id") or self._turn_id
            return

        event: dict[str, Any] = {
            "line": line_number,
            "thread": payload.get("thread_id") or record.get("thread_id"),
            "turn": payload.get("turn_id") or self._turn_id,
            "timestamp": _timestamp(record.get("timestamp")),
            "ordinal": record.get("ordinal"),
        }
        if kind == "event_msg" and subtype == "task_started":
            if event["thread"] and self.thread_native_id and event["thread"] != self.thread_native_id:
                return
            self._turn_id = payload.get("turn_id") or self._turn_id
            event.update(kind="start", turn=self._turn_id)
        elif kind == "event_msg" and subtype in _INTERRUPTED:
            event["kind"] = "interrupted"
        elif kind == "event_msg" and subtype == "task_complete":
            text = _reply_text(payload.get("last_agent_message"))
            event["kind"] = "complete"
            if text.strip():
                event["reply"] = self._reply(
                    event, text, None, "task_complete_last_agent_message", record_sha256)
                event["priority"] = 1
        elif kind == "event_msg" and subtype in {"item_completed", "item_started"}:
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") == "UserMessage":
                return
            phase = item.get("phase") or item.get("channel")
            text = _reply_text(item.get("content")) if item.get("type") == "AgentMessage" else ""
            if subtype == "item_completed" and phase in _FINAL_PHASES and text.strip():
                event.update(kind="final", priority=4)
                event["timestamp"] = _timestamp(payload.get("completed_at_ms")) or event["timestamp"]
                event["reply"] = self._reply(
                    event, text, item.get("id"), "explicit_final_completed_item", record_sha256)
            else:
                event["kind"] = "activity"
                if item.get("type") == "AgentMessage" and phase and phase not in _FINAL_PHASES:
                    event["commentary_digest"] = _digest(text)
                elif item.get("type") == "AgentMessage" and not phase:
                    event["unlabeled_message_digest"] = _digest(text)
        elif kind == "response_item":
            if subtype == "message":
                if payload.get("role") != "assistant":
                    return
                text = _reply_text(payload.get("content"))
                phase = payload.get("channel") or payload.get("phase")
                recipient = payload.get("recipient")
                if phase in _FINAL_PHASES and recipient in (None, "all", "user") and text.strip():
                    event.update(kind="final", priority=3)
                    event["reply"] = self._reply(
                        event, text, payload.get("id"), "explicit_final_response_item", record_sha256)
                else:
                    event["kind"] = "activity"
                    if phase and phase not in _FINAL_PHASES:
                        event["commentary_digest"] = _digest(text)
                    elif not phase:
                        event["unlabeled_message_digest"] = _digest(text)
            elif subtype in {"function_call", "function_call_output", "custom_tool_call",
                             "custom_tool_call_output", "local_shell_call", "reasoning",
                             "web_search_call", "image_generation_call"}:
                event["kind"] = "activity"
            else:
                return
        elif kind == "event_msg" and subtype == "agent_message":
            text = _reply_text(payload.get("message"))
            phase = payload.get("channel") or payload.get("phase")
            if phase in _FINAL_PHASES and text.strip():
                event.update(kind="final", priority=2)
                event["reply"] = self._reply(
                    event, text, payload.get("id"), "explicit_final_agent_message", record_sha256)
            else:
                event["kind"] = "activity"
                if phase and phase not in _FINAL_PHASES:
                    event["commentary_digest"] = _digest(text)
                elif not phase:
                    event["unlabeled_message_digest"] = _digest(text)
        elif kind == "event_msg" and (subtype in {"agent_reasoning", "agent_reasoning_raw_content"}
                                            or isinstance(subtype, str) and subtype.startswith(_ACTIVITY_PREFIXES)):
            event["kind"] = "activity"
        else:
            return
        self._events.append(event)

    def _reply(self, event: dict[str, Any], text: str, native_id: str | None,
               reason: str, record_sha256: str | None) -> dict[str, Any]:
        if not native_id:
            identity = [self.thread_native_id, event["turn"], event["timestamp"], text]
            native_id = "final:" + _digest(json.dumps(identity, ensure_ascii=False, separators=(",", ":")))
        result = {
            "native_id": native_id, "text": text, "sent_at": event["timestamp"],
            "thread_native_id": event["thread"] or self.thread_native_id,
            "source_line": event["line"], "kind": "final", "selection_reason": reason,
        }
        if record_sha256:
            result["record_sha256"] = record_sha256
        return result

    def _eligible(self, event: dict[str, Any], own_started: bool) -> tuple[bool, bool]:
        parent = self.metadata.get("forked_from_id") or self.metadata.get("parent_thread_id")
        foreign = bool(event.get("thread") and self.thread_native_id and event["thread"] != self.thread_native_id)
        if foreign and event["thread"] != parent:
            return False, False
        start = self.metadata.get("subagent_history_start_ordinal")
        ordinal = event.get("ordinal")
        inherited = isinstance(start, int) and isinstance(ordinal, int) and ordinal < start
        fork_time = _epoch(self.metadata.get("timestamp")) if self.metadata.get("forked_from_id") else None
        event_time = _epoch(event.get("timestamp"))
        before_fork = fork_time is not None and event_time is not None and event_time < fork_time
        inherited = inherited or before_fork or foreign
        if inherited:
            # Copied history is useful context for the first action in a fork,
            # but must not become fresh output after the child starts working.
            if not parent or own_started:
                return False, True
            if foreign and fork_time is not None and event_time is not None and event_time >= fork_time:
                return False, True
            if event.get("reply"):
                event["reply"]["thread_native_id"] = parent
        return True, inherited

    def finalize(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._finalized:
            return candidates
        self._finalized = True
        stream = self._events + [
            {"kind": "user", "line": candidate.get("source_line") or 0, "candidate": candidate}
            for candidate in candidates
        ]
        stream.sort(key=lambda event: event["line"])
        previous: dict[str, Any] | None = None
        previous_generation = -1
        previous_priority = 0
        generation = 0
        active_turn = None
        commentary: set[str] = set()
        status = "no_previous_agent_reply"
        interrupted = False
        own_started = False
        for event in stream:
            kind = event["kind"]
            if kind == "user":
                candidate = event["candidate"]
                thread = candidate.get("thread_native_id")
                if thread and self.thread_native_id and thread != self.thread_native_id:
                    candidate.update(previous_reply=None, previous_reply_status="thread_mismatch")
                    continue
                candidate_time = _epoch(candidate.get("sent_at"))
                reply_time = _epoch(previous.get("sent_at")) if previous else None
                if reply_time is not None and candidate_time is not None and reply_time > candidate_time:
                    candidate.update(previous_reply=None, previous_reply_status="reply_after_user_message")
                else:
                    candidate.update(previous_reply=previous, previous_reply_status="available" if previous else status)
                generation += 1
                commentary.clear()
                interrupted = False
                own_started = True
                continue
            eligible, inherited = self._eligible(event, own_started)
            if not eligible:
                continue
            if kind == "start":
                if event["turn"] is None or event["turn"] != active_turn:
                    active_turn = event["turn"]
                    generation += 1
                    commentary.clear()
                    interrupted = False
                continue
            if not inherited:
                own_started = True
            if kind == "interrupted":
                previous = None
                status = "previous_turn_interrupted"
                interrupted = True
                continue
            if kind == "activity":
                if (previous is not None and previous_generation == generation
                        and event.get("unlabeled_message_digest") == _digest(previous["text"])):
                    continue
                previous = None
                if not interrupted:
                    status = "previous_turn_without_final_reply"
                if event.get("commentary_digest"):
                    commentary.add(event["commentary_digest"])
                continue
            reply = event.get("reply")
            if kind == "complete":
                # Completion can recover older unlabeled assistant messages,
                # but must never relabel known interim commentary as a final.
                if interrupted:
                    continue
                if reply and _digest(reply["text"]) in commentary:
                    reply = None
                if previous is not None and previous_generation == generation:
                    if not reply or reply["text"] != previous["text"]:
                        continue
                if reply is None:
                    previous = None
                    status = "previous_turn_without_final_reply"
                    continue
            if reply is not None:
                interrupted = False
                if (previous is not None and previous_generation == generation
                        and previous["text"] == reply["text"]):
                    if event["priority"] > previous_priority:
                        previous.clear()
                        previous.update(reply)
                        previous_priority = event["priority"]
                else:
                    previous = reply
                    previous_generation = generation
                    previous_priority = event["priority"]
                status = "available"
        seen: set[int] = set()
        for candidate in candidates:
            reply = candidate.get("previous_reply")
            if reply is not None and id(reply) not in seen:
                self.replies.append(reply)
                seen.add(id(reply))
        self._events.clear()
        return candidates
