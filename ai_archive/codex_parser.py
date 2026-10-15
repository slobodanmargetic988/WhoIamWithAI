"""Stream Codex rollouts without mistaking injected user-role context for a person.

Rollouts have two user-event formats. Recent desktop versions emit completed
UserMessage items; older versions emit user_message events. Model input messages
are retained only as a review fallback because they also contain application
context, replayed history, and agent prompts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any


_UUID = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:\.jsonl)?$")
_INJECTED_PREFIXES = (
    "# AGENTS.md instructions", "<environment_context>", "<INSTRUCTIONS>",
    "<user_instructions>", "<turn_aborted>", "<subagent_notification>",
    "<external_codex_apps_open_page>", "<external_codex_apps_writing_block_edits>",
    "<recommended_plugins>", "<send_user_message_question_reply>",
    "<codex_internal_context", "<skill>",
)
_AUTOMATION_PREFIXES = (
    "<heartbeat>", "<automated", "<goal_continuation", "[AUTOMATION]",
    "Message Type: NEW_TASK", "Message Type: MESSAGE", "You are a worker",
    "You are the worker", "You are an autonomous worker",
)


def _sanitize(value: Any) -> Any:
    """Keep attachment references and descriptions without inline binary data."""
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()
                if key not in {"data", "base64", "bytes"}
                and not (isinstance(item, str) and item.startswith("data:"))}
    if isinstance(value, list):
        return [_sanitize(item) for item in value
                if not (isinstance(item, str) and item.startswith("data:"))]
    return value


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type", "text")
        if kind in {"text", "input_text", "output_text", "Text"} and isinstance(part.get("text"), str) and part["text"]:
            parts.append(part["text"])
        elif kind in {"image", "input_image", "local_image"}:
            parts.append("[Image attachment]")
        elif kind in {"audio", "input_audio"}:
            parts.append("[Audio attachment]")
        elif kind in {"file", "document", "input_file"}:
            parts.append("[Document attachment]")
    return "\n".join(parts)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> str | None:
    """Return UTC ISO text, accepting numeric epoch seconds or milliseconds."""
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        try:
            seconds = value / 1000 if abs(value) > 100_000_000_000 else value
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, OverflowError, OSError):
            return None
    return value if isinstance(value, str) and value else None


def _source_object(value: Any) -> Any:
    # The state database stores source as JSON text; session_meta uses an object.
    if isinstance(value, str) and value.startswith("{"):
        import json
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _epoch(value: Any) -> float | None:
    timestamp = _timestamp(value)
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


class CodexParser:
    """Feed decoded records, then finalize a list of standardized candidates.

    Only user candidates and small metadata are buffered. Tool output, assistant
    responses, instructions, and reasoning are never retained by this parser.
    Supplying the filename/state database thread ID is recommended: forked
    rollouts can contain session_meta records copied from their parent.
    """

    def __init__(self, thread_native_id: str | None = None,
                 metadata: dict[str, Any] | None = None,
                 source_path: str | Path | None = None):
        if thread_native_id is None and source_path is not None:
            match = _UUID.search(Path(source_path).name)
            thread_native_id = match.group(1) if match else None
        self.thread_native_id = thread_native_id
        self.metadata = dict(metadata or {})
        self.skipped: Counter[str] = Counter()
        self._own_meta: dict[str, Any] | None = None
        self._authoritative: list[dict[str, Any]] = []
        self._responses: list[dict[str, Any]] = []
        self._turn_id: str | None = None
        self._cwd = self.metadata.get("cwd")
        self._finalized: list[dict[str, Any]] | None = None

    def feed(self, record: dict[str, Any], line_number: int = 0) -> list[dict[str, Any]]:
        if self._finalized is not None:
            raise RuntimeError("Cannot feed a finalized parser")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return []
        kind, subtype = record.get("type"), payload.get("type")
        if kind == "session_meta":
            meta_id = payload.get("id") or payload.get("session_id")
            if self.thread_native_id is None:
                self.thread_native_id = meta_id
            if self._own_meta is None and meta_id == self.thread_native_id:
                # Whitelist metadata rather than buffering base instructions/tools.
                keys = ("id", "session_id", "timestamp", "cwd", "originator", "source",
                        "thread_source", "forked_from_id", "parent_thread_id",
                        "agent_path", "agent_nickname", "agent_role",
                        "subagent_history_start_ordinal", "git", "cli_version")
                self._own_meta = {key: payload[key] for key in keys if key in payload}
                self.metadata.update(self._own_meta)
                self._cwd = self.metadata.get("cwd")
            elif meta_id != self.thread_native_id:
                self.skipped["inherited_session_metadata"] += 1
            return []
        if kind == "turn_context":
            self._turn_id = payload.get("turn_id") or self._turn_id
            if payload.get("cwd"):
                self._cwd = payload["cwd"]
            return []
        if kind == "event_msg" and subtype == "task_started":
            self._turn_id = payload.get("turn_id") or self._turn_id
            return []
        if kind == "event_msg" and subtype == "thread_name_updated":
            name = payload.get("thread_name") or payload.get("name") or payload.get("title")
            if isinstance(name, str):
                self.metadata["title"] = name
            return []
        if kind == "event_msg" and subtype == "item_completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "UserMessage":
                content = item.get("content", [])
                candidate = self._candidate(record, payload, item, content, line_number)
                candidate["_format"] = "completed_item"
                candidate["sent_at"] = _timestamp(payload.get("completed_at_ms")) or candidate["sent_at"]
                self._authoritative.append(candidate)
            return []
        if kind == "event_msg" and subtype == "user_message":
            content = [{"type": "text", "text": payload.get("message", "")}]
            # Keep attachment references, never open them or copy image bytes.
            for key in ("images", "local_images"):
                for image in payload.get(key, []) or []:
                    content.append({"type": key.rstrip("s"), "reference": image})
            candidate = self._candidate(record, payload, payload, content, line_number)
            candidate["_format"] = "legacy_event"
            self._authoritative.append(candidate)
            return []
        if kind == "response_item" and subtype == "message" and payload.get("role") == "user":
            content = payload.get("content", [])
            candidate = self._candidate(record, payload, payload, content, line_number)
            passthrough = payload.get("internal_chat_message_metadata_passthrough")
            if isinstance(passthrough, dict):
                candidate["turn_native_id"] = passthrough.get("turn_id") or candidate.get("turn_native_id")
                candidate["_explicit_turn"] = bool(passthrough.get("turn_id"))
                candidate["_create_time"] = _timestamp(passthrough.get("create_time"))
            candidate["_format"] = "response_fallback"
            self._responses.append(candidate)
        return []

    def _candidate(self, record: dict[str, Any], payload: dict[str, Any], item: dict[str, Any],
                   content: Any, line_number: int) -> dict[str, Any]:
        content = _sanitize(content)
        return {
            "native_id": item.get("id") or item.get("client_id"),
            "client_id": item.get("client_id"),
            "text": _text(content), "content": content,
            "sent_at": _timestamp(record.get("timestamp")),
            "thread_native_id": payload.get("thread_id") or self.thread_native_id,
            "turn_native_id": payload.get("turn_id") or self._turn_id,
            "cwd": self._cwd or self.metadata.get("cwd"),
            "title": self.metadata.get("title") or self.metadata.get("name"),
            "origin": "human", "reason": "explicit_user_event",
            "ordinal": record.get("ordinal"), "source_line": line_number,
            "parent_thread_id": None, "is_agent_thread": False,
        }

    def finalize(self) -> list[dict[str, Any]]:
        if self._finalized is not None:
            return self._finalized
        source = _source_object(self.metadata.get("source"))
        spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
        is_agent = bool(spawn or self.metadata.get("thread_source") == "subagent"
                        or self.metadata.get("agent_path"))
        parent_id = self.metadata.get("parent_thread_id") or spawn.get("parent_thread_id")
        self.metadata["is_agent_thread"] = is_agent
        self.metadata["parent_thread_id"] = parent_id
        self.metadata["thread_native_id"] = self.thread_native_id
        automated = source == "exec" or self.metadata.get("originator") in {"codex_exec"}
        # Mixed rollouts occur after upgrades/resumes: an authoritative event in
        # one turn must not hide old turns recorded only as model input. Match
        # mirrors using normalized text plus turn identity or nearby timestamps.
        by_text: dict[str, list[dict[str, Any]]] = {}
        for item in self._authoritative:
            normalized = " ".join(item["text"].split())
            by_text.setdefault(_digest(normalized), []).append(item)
        unmatched = []
        for response in self._responses:
            normalized = " ".join(response["text"].split())
            options = by_text.get(_digest(normalized), [])
            response_time = _epoch(response.get("_create_time") or response.get("sent_at"))
            mirrored = False
            for item in options:
                same_turn = bool(response.get("_explicit_turn") and response.get("turn_native_id")
                                 and response.get("turn_native_id") == item.get("turn_native_id"))
                item_time = _epoch(item.get("sent_at"))
                nearby = response_time is not None and item_time is not None and abs(response_time - item_time) <= 5
                if same_turn or nearby:
                    mirrored = True
                    break
            if mirrored:
                self.skipped["response_user_mirrors_or_context"] += 1
            else:
                unmatched.append(response)
        candidates = sorted(self._authoritative + unmatched, key=lambda item: item["source_line"])
        response_times: dict[tuple[Any, str], str] = {}
        for response in self._responses:
            if response.get("_create_time"):
                response_times[(response.get("turn_native_id"), _digest(response["text"]))] = response["_create_time"]
        output: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        occurrences: Counter[tuple[Any, ...]] = Counter()
        for candidate in candidates:
            text = candidate["text"].lstrip()
            fallback = candidate.get("_format") == "response_fallback"
            if fallback and text.startswith(_INJECTED_PREFIXES):
                self.skipped["injected_context"] += 1
                continue
            if not text and not candidate["content"]:
                self.skipped["empty_user_message"] += 1
                continue
            candidate_thread = candidate.get("thread_native_id")
            if candidate_thread and self.thread_native_id and candidate_thread != self.thread_native_id:
                self.skipped["inherited_thread_event"] += 1
                continue
            start = self.metadata.get("subagent_history_start_ordinal")
            ordinal = candidate.get("ordinal")
            if isinstance(start, int) and isinstance(ordinal, int) and ordinal < start:
                self.skipped["inherited_history"] += 1
                continue
            # Older forks copy user events without a thread ID or ordinal
            # boundary. Their original send time predates creation of the fork.
            if self.metadata.get("forked_from_id"):
                fork_time = _epoch(self.metadata.get("timestamp"))
                sent_time = _epoch(candidate.get("sent_at"))
                if fork_time is not None and sent_time is not None and sent_time < fork_time:
                    self.skipped["inherited_history"] += 1
                    continue
            candidate["thread_native_id"] = self.thread_native_id or candidate_thread
            candidate["parent_thread_id"] = parent_id
            candidate["is_agent_thread"] = is_agent
            candidate["title"] = self.metadata.get("title") or self.metadata.get("name") or candidate["title"]
            if is_agent:
                candidate.update(origin="review", reason="agent_thread_prompt")
            elif automated:
                candidate.update(origin="review", reason="programmatic_exec_session")
            elif text.startswith(_AUTOMATION_PREFIXES):
                candidate.update(origin="review", reason="automation_or_forwarded_prompt")
            elif fallback:
                candidate.update(origin="review", reason="user_role_without_explicit_user_event")
            accurate_time = response_times.get((candidate.get("turn_native_id"), _digest(candidate["text"])))
            if accurate_time:
                candidate["sent_at"] = accurate_time
            elif candidate.get("_create_time"):
                candidate["sent_at"] = candidate["_create_time"]
            if candidate.get("ordinal") is None and not candidate.get("native_id"):
                signature = (candidate["sent_at"], candidate.get("turn_native_id"), _digest(candidate["text"]))
                occurrences[signature] += 1
                candidate["ordinal"] = occurrences[signature]
            # Native IDs deduplicate retried event projections. The fallback key
            # preserves repeated identical prompts sent at distinct times/turns.
            identity = ("id", candidate["native_id"]) if candidate["native_id"] else (
                "event", candidate["sent_at"], candidate.get("turn_native_id"),
                _digest(candidate["text"]), candidate.get("ordinal"))
            if identity in seen:
                self.skipped["duplicate_user_event"] += 1
                continue
            seen.add(identity)
            candidate.pop("_format", None)
            candidate.pop("_create_time", None)
            candidate.pop("_explicit_turn", None)
            output.append(candidate)
        self._authoritative.clear()
        self._responses.clear()
        self._finalized = output
        return output
