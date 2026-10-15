"""Parse local Claude transcripts without mistaking tool/agent input for a person.

The same adapter accepts Claude Code JSONL and Claude Desktop audit JSONL.
It deliberately produces a separate ``review`` origin for inputs whose source
cannot be established from the transcript (for example an SDK caller).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I)
_AUTOMATED_KINDS = {"task-notification", "task_notification", "agent", "subagent", "system", "automation", "scheduled-task"}
_IGNORED_PREFIXES = (
    ("<task-notification", "task_notification"),
    ("<local-command-stdout", "local_command_output"),
    ("<local-command-caveat", "local_command_context"),
    ("[Request interrupted by user", "interruption_marker"),
    ("This session is being continued from a previous conversation", "compaction_summary"),
)


def _sanitize(value: Any) -> Any:
    """Keep attachment descriptions and references; never copy inline binaries."""
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key not in {"data", "base64", "bytes", "_audit_hmac"}
            and not (isinstance(item, str) and item.startswith("data:"))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _content(raw: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(raw, str):
        return raw, [{"type": "text", "text": raw}]
    if not isinstance(raw, list):
        return "", []
    text: list[str] = []
    blocks: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            text.append(item)
            blocks.append({"type": "text", "text": item})
        elif isinstance(item, dict):
            blocks.append(_sanitize(item))
            kind = item.get("type")
            if kind in {"text", "input_text"} and isinstance(item.get("text"), str):
                text.append(item["text"])
            elif kind in {"image", "input_image"}:
                text.append("[Image attachment]")
            elif kind == "document":
                title = item.get("title")
                text.append(f"[Document attachment: {title}]" if title else "[Document attachment]")
            elif kind not in {"tool_result", "tool_use"}:
                text.append(f"[Attachment: {kind or 'unknown'}]")
    return "\n".join(text), blocks


def _timestamp(record: dict[str, Any]) -> str | None:
    value = record.get("timestamp")
    if value is None or value == "":
        value = record.get("_audit_timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Human history uses epoch milliseconds; transcript timestamps use ISO.
        seconds = value / 1000 if abs(value) > 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, OSError, OverflowError):
            return None
    return value if isinstance(value, str) and value else None


class ClaudeParser:
    """Stateful, one-file Claude transcript adapter.

    ``feed(record, line_number)`` returns zero or one standardized candidates.
    Metadata/title events update ``metadata`` but do not create user messages.
    A copied event retains its native UUID so the importer can deduplicate it.
    """

    def __init__(self, source_path: str = "") -> None:
        self.source_path = str(source_path or "")
        self.skipped: Counter[str] = Counter()
        self.warnings: Counter[str] = Counter()
        self.metadata: dict[str, Any] = {}
        self._custom_title = False
        path = Path(self.source_path)
        self._is_agent = "subagents" in path.parts or path.stem.startswith("agent-")
        self._agent_name = path.stem if self._is_agent else None
        self._path_parent = path.parent.parent.name if "subagents" in path.parts else None
        if _UUID.match(path.stem):
            self.metadata["thread_native_id"] = path.stem
        self.metadata["is_agent_thread"] = self._is_agent

    def _skip(self, reason: str) -> list[dict[str, Any]]:
        self.skipped[reason] += 1
        return []

    def feed(self, record: dict[str, Any], line: int = 0) -> list[dict[str, Any]]:
        if not isinstance(record, dict):
            self.warnings["non_object_record"] += 1
            return self._skip("malformed_record")

        session = record.get("sessionId") or record.get("session_id")
        agent = self._is_agent or bool(record.get("isSidechain")) or bool(record.get("parent_tool_use_id"))
        if session:
            self.metadata["thread_native_id"] = str(session)
        if record.get("cwd"):
            self.metadata["cwd"] = record["cwd"]
        if record.get("type") == "custom-title" and record.get("customTitle"):
            self.metadata["title"] = record["customTitle"]
            self._custom_title = True
        elif record.get("type") == "ai-title" and record.get("aiTitle") and not self._custom_title:
            self.metadata["title"] = record["aiTitle"]

        if record.get("type") != "user":
            return self._skip("non_user_event")
        if agent:
            self.metadata["is_agent_thread"] = self._is_agent
            return self._skip("agent_input")
        if record.get("isMeta"):
            return self._skip("meta_input")
        if record.get("isCompactSummary"):
            return self._skip("compaction_summary")
        if record.get("isSynthetic"):
            return self._skip("synthetic_input")

        message = record.get("message")
        if not isinstance(message, dict):
            self.warnings["missing_user_message"] += 1
            return self._skip("malformed_message")
        if message.get("role", "user") != "user":
            return self._skip("non_user_role")
        raw = message.get("content")
        if "toolUseResult" in record or "tool_use_result" in record:
            return self._skip("tool_result")
        if isinstance(raw, list) and any(isinstance(block, dict) and block.get("type") in {"tool_result", "tool_use"} for block in raw):
            return self._skip("tool_result")

        text, blocks = _content(raw)
        stripped = text.lstrip()
        origin_obj = record.get("origin")
        origin_kind = str(origin_obj.get("kind", "")).lower() if isinstance(origin_obj, dict) else str(origin_obj or "").lower()
        turn_origin = str(record.get("turnOrigin") or "").lower()
        if origin_kind in _AUTOMATED_KINDS or turn_origin in _AUTOMATED_KINDS:
            return self._skip("automated_origin")
        for prefix, reason in _IGNORED_PREFIXES:
            if stripped.startswith(prefix):
                return self._skip(reason)
        if not stripped:
            return self._skip("empty_input")

        # Local slash commands are human actions. Keep their actual command and
        # arguments, without XML wrappers generated by the Claude client.
        if stripped.startswith(("<command-name", "<command-message")):
            command = re.search(r"<command-name>(.*?)</command-name>", stripped, re.S)
            args = re.search(r"<command-args>(.*?)</command-args>", stripped, re.S)
            if command:
                text = command.group(1).strip()
                if args and args.group(1).strip():
                    text += " " + args.group(1).strip()

        origin = "human"
        reason = "explicit_human_origin" if origin_kind == "human" or turn_origin == "human" else "top_level_user_input"
        if reason != "explicit_human_origin":
            entrypoint = str(record.get("entrypoint") or "").lower()
            prompt_source = str(record.get("promptSource") or "").lower()
            if entrypoint.startswith("sdk") or prompt_source in {"sdk", "system"} or turn_origin == "sdk":
                origin, reason = "review", "sdk_input_unverified"
            elif stripped.startswith("<scheduled-task"):
                origin, reason = "review", "scheduled_input_unverified"
            elif stripped.startswith("<system-reminder"):
                origin, reason = "review", "system_context_unverified"
            elif origin_kind and origin_kind != "human":
                origin, reason = "review", "unknown_origin"

        sent_at = _timestamp(record)
        if sent_at is None:
            self.warnings["missing_timestamp"] += 1
        thread = str(session or self.metadata.get("thread_native_id") or Path(self.source_path).stem or "unknown")
        native_id = record.get("uuid")
        if not native_id:
            # Line position preserves distinct repeated inputs without UUIDs.
            identity = json.dumps([thread, sent_at, line, raw], ensure_ascii=False, sort_keys=True)
            native_id = "fallback:" + hashlib.sha256(identity.encode()).hexdigest()
            self.warnings["missing_native_id"] += 1
        return [{
            "native_id": str(native_id),
            "text": text,
            "content": blocks,
            "sent_at": sent_at,
            "thread_native_id": thread,
            "cwd": record.get("cwd") or self.metadata.get("cwd"),
            "title": self.metadata.get("title"),
            "origin": origin,
            "reason": reason,
            "ordinal": line,
            "source_line": line,
            "parent_thread_id": self._path_parent if self._is_agent else None,
            "is_agent_thread": False,
        }]


class ClaudeHistoryParser:
    """Recover CLI history entries after authoritative transcript messages.

    History has no event UUID or evidence of a completed agent submission. Every
    recovered entry therefore stays in review until its provenance is confirmed.
    The importer is responsible for suppressing history mirrors already present
    in a transcript; this adapter never guesses which transcript a row mirrors.
    """

    def __init__(self, source_path: str = "") -> None:
        self.source_path = str(source_path or "")
        self.skipped: Counter[str] = Counter()
        self.warnings: Counter[str] = Counter()
        self.metadata: dict[str, Any] = {"is_agent_thread": False}

    def feed(self, record: dict[str, Any], line: int = 0) -> list[dict[str, Any]]:
        if not isinstance(record, dict) or "display" not in record:
            self.skipped["non_history_event"] += 1
            return []
        display = record.get("display")
        if not isinstance(display, str):
            self.skipped["malformed_history_display"] += 1
            return []

        text = display
        pasted_blocks: list[dict[str, Any]] = []
        pasted = record.get("pastedContents")
        if isinstance(pasted, dict):
            for key, raw in pasted.items():
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("type") or "unknown").lower()
                paste_id = str(raw.get("id", key))
                if kind == "text" and isinstance(raw.get("content"), str):
                    pasted_text = raw["content"]
                    pasted_blocks.append({"type": "text", "paste_id": paste_id, "text": pasted_text})
                    # The display stores a short marker instead of large pasted
                    # text. Recover its original wording when it is available.
                    pattern = r"\[Pasted text #" + re.escape(paste_id) + r"(?:[^\]]*)\]"
                    text, replaced = re.subn(pattern, lambda match: pasted_text, text)
                    if not replaced and not text.strip():
                        text = pasted_text
                else:
                    # Image history variants store base64 in either data or
                    # content. Whitelist descriptive metadata instead of copying
                    # an arbitrary binary-bearing object into the database.
                    block = {"type": "image" if kind in {"image", "input_image"} else "attachment", "paste_id": paste_id}
                    for field in ("mediaType", "media_type", "mimeType", "mime_type", "filename", "name", "path", "width", "height"):
                        value = raw.get(field)
                        if isinstance(value, (str, int, float)) and not (isinstance(value, str) and value.startswith("data:")):
                            block[field] = value
                    source = raw.get("source")
                    if isinstance(source, dict):
                        block["source"] = {key: value for key, value in source.items() if key in {"type", "media_type", "mediaType", "path", "url"} and isinstance(value, str) and not value.startswith("data:")}
                    pasted_blocks.append(block)
                    if not text.strip():
                        text = "[Image attachment]" if block["type"] == "image" else "[Attachment]"

        if not text.strip() and not pasted_blocks:
            self.skipped["empty_history_input"] += 1
            return []
        cwd = record.get("project") if isinstance(record.get("project"), str) else None
        session = record.get("sessionId")
        if not isinstance(session, str) or not session:
            # Keep unknown-session actions visible together per recorded project;
            # do not invent a transcript session or discard the evidence.
            session = "history-unassigned:" + hashlib.sha256((cwd or "unassigned").encode()).hexdigest()
            self.warnings["missing_thread_id"] += 1
        sent_at = _timestamp(record)
        if sent_at is None:
            self.warnings["missing_timestamp"] += 1
        identity = json.dumps([session, record.get("timestamp"), display, pasted], ensure_ascii=False, sort_keys=True)
        native_id = "claude-history:" + hashlib.sha256(identity.encode()).hexdigest()
        self.metadata.update(thread_native_id=session, cwd=cwd)
        return [{
            "native_id": native_id,
            "text": text,
            "content": [{"type": "text", "text": text}, *pasted_blocks],
            "sent_at": sent_at,
            "thread_native_id": session,
            "cwd": cwd,
            "title": None,
            "origin": "review",
            "reason": "history_only_unverified",
            "ordinal": line,
            "source_line": line,
            "parent_thread_id": None,
            "is_agent_thread": False,
        }]
