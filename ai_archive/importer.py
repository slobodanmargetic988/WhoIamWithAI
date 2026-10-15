from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from pathlib import Path
import sqlite3

from .db import ProjectResolver, now, stable_id, timestamp
from .discovery import SourceRoot, transcript_files

UUID = re.compile(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', re.I)
CONTEXT_VERSION = 1


def migrate_codex_identity_scope(con):
    """Repair v1 global Codex item IDs, retaining each original primary action.

    A full Codex rescan then restores the additional actions previously merged
    across threads. Old cross-thread memberships/context cannot be trusted.
    """
    legacy = [dict(row) for row in con.execute('''SELECT m.*,t.native_id AS thread_native_id
              FROM messages m JOIN threads t ON t.id=m.primary_thread_id
              WHERE m.provider='codex' AND m.native_id IS NOT NULL''')
              if row['id'] == stable_id('message', 'codex', row['native_id'])]
    if not legacy:
        return 0
    for message in legacy:
        old_id = message['id']
        new_id = stable_id('message', 'codex', message['thread_native_id'], message['native_id'])
        columns = [key for key in message if key != 'thread_native_id']
        values = [new_id if key == 'id' else message[key] for key in columns]
        con.execute(f"INSERT OR IGNORE INTO messages({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
        con.execute('''INSERT OR IGNORE INTO thread_messages
                       SELECT thread_id,?,ordinal FROM thread_messages WHERE message_id=? AND thread_id=?''',
                    (new_id, old_id, message['primary_thread_id']))
        # The filename identifies the original thread in Codex rollouts. Do not
        # copy provenance belonging to another thread that reused its item ID.
        con.execute('''INSERT OR IGNORE INTO message_sources
                       SELECT ?,ms.file_id,ms.line,ms.record_sha256,ms.source_sha256,0
                       FROM message_sources ms JOIN source_files s ON s.id=ms.file_id
                       WHERE ms.message_id=? AND INSTR(s.path,?)>0''',
                    (new_id, old_id, message['thread_native_id']))
        for table in ('message_contexts', 'message_sources', 'thread_messages'):
            con.execute(f'DELETE FROM {table} WHERE message_id=?', (old_id,))
        con.execute('DELETE FROM messages WHERE id=?', (old_id,))
    con.execute("UPDATE source_files SET context_version=0 WHERE provider='codex'")
    con.commit()
    return len(legacy)


def decode_record(raw: bytes) -> tuple[dict, bool]:
    """Recover a known metadata-prefix defect without guessing at message text."""
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeError):
        start = raw.find(b'{')
        if start <= 0:
            raise
        record = json.loads(raw[start:])
        if not (isinstance(record, dict) and record.get('type') == 'session_meta'
                and isinstance(record.get('payload'), dict)
                and (record['payload'].get('id') or record['payload'].get('session_id'))):
            raise ValueError('Unrecognized prefixed JSON record') from None
        return record, True
    if not isinstance(record, dict):
        raise ValueError('JSON value is not an object')
    return record, False


def candidate_line(record: dict) -> bool:
    if record.get('type') == 'user' or 'display' in record:
        return True
    payload = record.get('payload') or {}
    if not isinstance(payload, dict):
        return False
    return (payload.get('role') == 'user' or payload.get('type') == 'user_message'
            or (isinstance(payload.get('item'), dict) and payload['item'].get('type') == 'UserMessage'))


def persist_message(con, resolver, provider, candidate, file_id, hashes, imported_at, source_hash):
    text = candidate.get('text') or ''
    native_thread = candidate.get('thread_native_id')
    if not native_thread:
        native_thread = 'unknown:' + file_id
    thread_id = stable_id('thread', provider, native_thread)
    cwd = candidate.get('cwd')
    project_id, _ = resolver.resolve(cwd)
    raw_time = candidate.get('sent_at')
    sent_at = timestamp(raw_time)
    origin = candidate.get('origin', 'review')
    reason = candidate.get('reason') or 'No explicit classification reason'
    native_id = candidate.get('native_id')
    # Codex item-N identifiers are local to a thread, unlike Claude's UUIDs.
    # Copied profiles still deduplicate because they retain the thread ID.
    # Legacy records have
    # no event ID: the parser supplies a within-thread occurrence ordinal so two
    # identical submissions do not accidentally collapse into one action.
    if native_id:
        message_id = (stable_id('message', provider, native_thread, native_id)
                      if provider == 'codex' else stable_id('message', provider, native_id))
    else:
        message_id = stable_id('message', provider, native_thread, raw_time, text, candidate.get('ordinal'))
    title = (candidate.get('title') or text.strip().split('\n')[0] or 'Untitled conversation')[:120]
    con.execute('''INSERT INTO threads(id,provider,native_id,project_id,title,cwd,parent_native_id,
                   is_agent_thread,started_at,last_message_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                   project_id=CASE WHEN excluded.cwd IS NOT NULL THEN excluded.project_id ELSE threads.project_id END,
                   cwd=COALESCE(excluded.cwd,threads.cwd),
                   parent_native_id=COALESCE(excluded.parent_native_id,threads.parent_native_id),
                   is_agent_thread=MAX(threads.is_agent_thread,excluded.is_agent_thread),
                   started_at=CASE WHEN threads.started_at IS NULL OR excluded.started_at<threads.started_at
                                   THEN excluded.started_at ELSE threads.started_at END,
                   last_message_at=CASE WHEN threads.last_message_at IS NULL OR excluded.last_message_at>threads.last_message_at
                                        THEN excluded.last_message_at ELSE threads.last_message_at END''',
                (thread_id, provider, native_thread, project_id, title, cwd,
                 candidate.get('parent_thread_id'), int(bool(candidate.get('is_agent_thread'))), sent_at, sent_at))
    content = json.dumps(candidate.get('content', []), ensure_ascii=False, separators=(',', ':'))
    con.execute('''INSERT INTO messages(id,provider,native_id,primary_thread_id,text,content_json,
                   sent_at,sent_at_raw,origin,classification_reason,first_imported_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   origin=CASE WHEN excluded.origin='human' THEN 'human' ELSE messages.origin END,
                   classification_reason=CASE WHEN excluded.origin='human' THEN excluded.classification_reason
                                              ELSE messages.classification_reason END,
                   sent_at=COALESCE(messages.sent_at,excluded.sent_at)''',
                (message_id, provider, native_id, thread_id, text, content, sent_at,
                 str(raw_time) if raw_time is not None else None, origin, reason, imported_at))
    con.execute('INSERT OR IGNORE INTO thread_messages VALUES(?,?,?)',
                (thread_id, message_id, candidate.get('sort_ordinal', candidate.get('source_line', candidate.get('ordinal')))))
    line = candidate.get('source_line') or 0
    record_hash = hashes.get(line) or stable_id(candidate.get('content'), text, raw_time)
    con.execute('''INSERT INTO message_sources VALUES(?,?,?,?,?,1)
                   ON CONFLICT(message_id,file_id,line) DO UPDATE SET
                   record_sha256=excluded.record_sha256,source_sha256=excluded.source_sha256,is_current=1''',
                (message_id, file_id, line, record_hash, source_hash))
    persist_context(con, provider, candidate, message_id, thread_id, file_id, source_hash, imported_at)


def persist_context(con, provider, candidate, message_id, thread_id, file_id, source_hash, imported_at):
    reply = candidate.get('previous_reply')
    reply_id = None
    reason = candidate.get('previous_reply_status') or 'not_in_available_history'
    rank = 0
    if isinstance(reply, dict):
        canonical_time = con.execute('SELECT sent_at FROM messages WHERE id=?', (message_id,)).fetchone()[0]
        reply_time = timestamp(reply.get('sent_at'))
        if canonical_time and reply_time and reply_time > canonical_time:
            # Copies can have shifted record timestamps. Check against the
            # deduplicated message's canonical time, not only this source copy.
            reply = None
            candidate['previous_reply_status'] = 'reply_after_user_message'
            reason = 'reply_after_user_message'
    if isinstance(reply, dict) and reply.get('text', '').strip():
        text = reply['text']
        raw_time = reply.get('sent_at')
        native_id = reply.get('native_id')
        reply_id = (stable_id('agent_reply', provider, reply.get('thread_native_id'), native_id, raw_time, text)
                    if provider == 'codex' else stable_id('agent_reply', provider,
                        native_id or (reply.get('thread_native_id'), raw_time), text))
        reason = reply.get('selection_reason') or 'completed_turn'
        # Prefer an explicit final projection over turn-completion fallbacks,
        # and both over legacy boundary inference when copies differ.
        rank = reply.get('evidence_rank') or (3 if reason.startswith('explicit_') else
                                             (1 if 'infer' in reason or 'boundary' in reason else 2))
        con.execute('''INSERT OR IGNORE INTO agent_replies VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (reply_id, provider, native_id, reply.get('thread_native_id'), text, timestamp(raw_time),
                     str(raw_time) if raw_time is not None else None, 'final', reason, imported_at))
        records = reply.get('source_records') or [{'line': reply.get('source_line', 0),
                                                  'record_sha256': reply.get('record_sha256')}]
        for record in records:
            line = record.get('line', record.get('source_line', 0))
            # Native parsers provide the hash of the exact original JSONL bytes.
            record_hash = record.get('record_sha256') or stable_id(text, raw_time, line)
            con.execute('''INSERT INTO agent_reply_sources VALUES(?,?,?,?,?,1)
                           ON CONFLICT(reply_id,file_id,line,source_sha256) DO UPDATE SET is_current=1''',
                        (reply_id, file_id, line, record_hash, source_hash))
    con.execute('''INSERT INTO message_contexts VALUES(?,?,?,?,?,?,?,?,1,?)
                   ON CONFLICT(message_id,thread_id,file_id,source_sha256) DO UPDATE SET
                   reply_id=excluded.reply_id,status=excluded.status,selection_reason=excluded.selection_reason,
                   evidence_rank=excluded.evidence_rank,is_current=1,observed_at=excluded.observed_at''',
                (message_id, thread_id, file_id, source_hash, reply_id,
                 'available' if reply_id else candidate.get('previous_reply_status', 'not_in_available_history'),
                 reason, rank, imported_at))


def import_archive(con: sqlite3.Connection, roots: list[SourceRoot], *,
                   aliases: dict | None = None, force=False, progress=None,
                   discovery_warnings: list[str] | None = None) -> dict:
    from .codex_parser import CodexParser
    from .claude_parser import ClaudeParser, ClaudeHistoryParser
    from .metadata import load_metadata, discovered_roots_from_state
    from .project_mapping import load_project_aliases
    from .codex_replies import CodexReplyContext
    from .claude_replies import ClaudeReplyContext
    roots = sorted(set(roots + discovered_roots_from_state(roots)), key=lambda root: (root.provider, str(root.path)))
    thread_metadata = load_metadata(roots)
    automatic_aliases = load_project_aliases(roots, thread_metadata)
    resolved_aliases = {**automatic_aliases, **(aliases or {})}
    migrated_identities = migrate_codex_identity_scope(con)
    started = now()
    run_id = con.execute("INSERT INTO import_runs(started_at,status) VALUES(?,'running')", (started,)).lastrowid
    con.commit()
    summary = Counter(files_discovered=0, files_imported=0, files_unchanged=0,
                      files_failed=0, malformed_lines=0, recovered_metadata_lines=0, candidates_processed=0)
    summary['codex_identities_repaired'] = migrated_identities
    resolver = ProjectResolver(con, resolved_aliases, set(automatic_aliases) - set(aliases or {}))
    for warning in discovery_warnings or []:
        con.execute('INSERT INTO import_issues(run_id,kind,detail) VALUES(?,?,?)',
                    (run_id, 'discovery', warning))
    files: dict[str, tuple[Path, str, list[tuple[str, str]]]] = {}
    for root in roots:
        profile_id = stable_id('profile', root.provider, str(root.path))
        con.execute('INSERT OR IGNORE INTO profiles VALUES(?,1,?,?,?)',
                    (profile_id, root.provider, root.path.name, str(root.path)))
        for file in transcript_files(root):
            real = file.resolve()
            file_id = stable_id('source', str(real))
            if file_id not in files:
                files[file_id] = (real, root.provider, [])
            files[file_id][2].append((profile_id, str(file)))
    con.commit()
    summary['files_discovered'] = len(files)
    if progress:
        progress({'phase': 'discovered', 'files': len(files), 'profiles': len(roots)})
    try:
        ordered = sorted(files.items(), key=lambda pair: pair[1][0].name == 'history.jsonl')
        for index, (file_id, (path, provider, profiles)) in enumerate(ordered, 1):
            previous = con.execute('SELECT * FROM source_files WHERE id=?', (file_id,)).fetchone()
            con.execute('INSERT OR IGNORE INTO source_files(id,provider,path) VALUES(?,?,?)',
                        (file_id, provider, str(path)))
            for profile_id, discovered_path in profiles:
                con.execute('INSERT OR IGNORE INTO file_profiles VALUES(?,?,?)', (file_id, profile_id, discovered_path))
            try:
                stat = path.stat()
                if not force and previous and previous['context_version'] == CONTEXT_VERSION and not previous['error'] and previous['size_bytes'] == stat.st_size and previous['mtime_ns'] == stat.st_mtime_ns:
                    summary['files_unchanged'] += 1
                    con.commit()
                    continue
                match = UUID.findall(path.stem)
                thread_native = match[-1] if match else None
                parser = (CodexParser(thread_native_id=thread_native,
                                      metadata=thread_metadata.get((provider, thread_native)), source_path=str(path))
                          if provider == 'codex' else (ClaudeHistoryParser(source_path=str(path))
                            if path.name == 'history.jsonl' else ClaudeParser(source_path=str(path))))
                context = (CodexReplyContext(thread_native_id=thread_native,
                            metadata=thread_metadata.get((provider, thread_native)), source_path=str(path))
                           if provider == 'codex' else ClaudeReplyContext(source_path=str(path)))
                hashes = {}
                digest = hashlib.sha256()
                line_count = 0
                malformed = 0
                recovered = 0
                candidates = []
                local_issues = []
                # Read a size-bounded snapshot. A concurrently growing session is
                # picked up on the next run; an incomplete last line is retried.
                with path.open('rb') as stream:
                    while stream.tell() < stat.st_size:
                        position = stream.tell()
                        raw = stream.readline(stat.st_size - position)
                        if not raw:
                            break
                        line_count += 1
                        digest.update(raw)
                        if not raw.strip():
                            continue
                        try:
                            record, was_recovered = decode_record(raw)
                            if was_recovered:
                                recovered += 1
                                local_issues.append((line_count, 'recovered_metadata_prefix', 'A valid session metadata object followed stray non-JSON prefix text; parsed without changing source.'))
                        except (ValueError, UnicodeError):
                            malformed += 1
                            kind = 'partial_last_line' if not raw.endswith(b'\n') and stream.tell() >= stat.st_size else 'malformed_json'
                            local_issues.append((line_count, kind, 'Record could not be decoded; source retained.'))
                            continue
                        if candidate_line(record):
                            hashes[line_count] = hashlib.sha256(raw).hexdigest()
                        context.feed(record, line_count, record_sha256=hashlib.sha256(raw).hexdigest())
                        found = parser.feed(record, line_count) or []
                        for candidate in found:
                            candidate.setdefault('source_line', line_count)
                            candidates.append(candidate)
                if hasattr(parser, 'finalize'):
                    candidates.extend(parser.finalize() or [])
                candidates = context.finalize(candidates)
                # Source replacement and candidate upserts are one transaction.
                # Retain imported messages even if a compacted source drops them;
                # only the current file's source locations are refreshed.
                con.execute('SAVEPOINT source_import')
                con.execute('UPDATE message_sources SET is_current=0 WHERE file_id=?', (file_id,))
                con.execute('UPDATE agent_reply_sources SET is_current=0 WHERE file_id=?', (file_id,))
                con.execute('UPDATE message_contexts SET is_current=0 WHERE file_id=?', (file_id,))
                for candidate in candidates:
                    if candidate.get('reason') == 'history_only_unverified':
                        # History indexes repeat transcript messages but lack
                        # their native UUIDs. Use them only for uncovered inputs.
                        sent = timestamp(candidate.get('sent_at'))
                        existing = con.execute('''SELECT 1 FROM messages m JOIN thread_messages tm ON tm.message_id=m.id
                                      JOIN threads t ON t.id=tm.thread_id WHERE m.provider='claude'
                                      AND t.native_id=? AND TRIM(m.text)=? AND
                                      (ABS(julianday(m.sent_at)-julianday(?))*86400 <= 10
                                       OR (m.sent_at IS NULL AND ? IS NULL)) LIMIT 1''',
                                      (candidate.get('thread_native_id'), candidate.get('text', '').strip(), sent, sent)).fetchone()
                        if existing:
                            parser.skipped['history_transcript_duplicate'] += 1
                            continue
                    enrichment = thread_metadata.get((provider, candidate.get('thread_native_id')), {})
                    if enrichment.get('title'):
                        candidate['title'] = enrichment['title']
                    elif not candidate.get('title') and parser.metadata.get('title'):
                        candidate['title'] = parser.metadata['title']
                    if enrichment.get('project_path'):
                        candidate['cwd'] = enrichment['project_path']
                    elif not candidate.get('cwd') and enrichment.get('cwd'):
                        candidate['cwd'] = enrichment['cwd']
                    persist_message(con, resolver, provider, candidate, file_id, hashes, started, digest.hexdigest())
                for line, kind, detail in local_issues:
                    con.execute('INSERT INTO import_issues(run_id,file_id,line,kind,detail) VALUES(?,?,?,?,?)',
                                (run_id, file_id, line, kind, detail))
                after = path.stat()
                # Force another pass when the file changed while we were reading.
                stamp = stat.st_mtime_ns if (after.st_size == stat.st_size and after.st_mtime_ns == stat.st_mtime_ns and malformed == 0) else -1
                con.execute('''UPDATE source_files SET size_bytes=?,mtime_ns=?,sha256=?,imported_at=?,
                               line_count=?,message_count=?,skipped_json=?,malformed_count=?,context_version=?,error=NULL WHERE id=?''',
                            (stat.st_size, stamp, digest.hexdigest(), now(), line_count, len(candidates),
                             json.dumps(dict(parser.skipped)), malformed, CONTEXT_VERSION, file_id))
                con.execute('RELEASE source_import')
                con.commit()
                summary['files_imported'] += 1
                summary['malformed_lines'] += malformed
                summary['recovered_metadata_lines'] += recovered
                summary['candidates_processed'] += len(candidates)
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                try:
                    con.execute('ROLLBACK TO source_import')
                    con.execute('RELEASE source_import')
                except sqlite3.Error:
                    pass
                summary['files_failed'] += 1
                resolver.cache.clear()
                # Log exception type, not arbitrary source data embedded in errors.
                detail = f'{type(error).__name__}: source import failed'
                con.execute('UPDATE source_files SET error=? WHERE id=?', (detail, file_id))
                con.execute('INSERT INTO import_issues(run_id,file_id,kind,detail) VALUES(?,?,?,?)',
                            (run_id, file_id, 'source_error', detail))
                con.commit()
            if progress and (index % 25 == 0 or index == len(files)):
                progress({'phase': 'importing', 'completed': index, 'total': len(files), **summary})
        # Revisit unchanged threads too: repositories may have moved, worktree
        # metadata may have appeared, or the person may have supplied an alias.
        for thread in con.execute('SELECT id,cwd,provider,native_id FROM threads').fetchall():
            enrichment = thread_metadata.get((thread['provider'], thread['native_id']), {})
            cwd = enrichment.get('project_path') or thread['cwd'] or enrichment.get('cwd')
            project_id, _ = resolver.resolve(cwd)
            con.execute('UPDATE threads SET project_id=? WHERE id=?', (project_id, thread['id']))
        summary['automatic_project_aliases'] = len(automatic_aliases)
        summary['messages'] = con.execute("SELECT COUNT(*) FROM messages WHERE origin='human'").fetchone()[0]
        summary['review_messages'] = con.execute("SELECT COUNT(*) FROM messages WHERE origin='review'").fetchone()[0]
        summary['projects'] = con.execute("SELECT COUNT(DISTINCT t.project_id) FROM threads t JOIN thread_messages tm ON tm.thread_id=t.id JOIN messages m ON m.id=tm.message_id WHERE m.origin='human'").fetchone()[0]
        summary['profiles'] = con.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]
        summary['agent_replies'] = con.execute('SELECT COUNT(*) FROM agent_replies').fetchone()[0]
        summary['messages_with_previous_reply'] = con.execute("SELECT COUNT(DISTINCT c.message_id) FROM preferred_message_contexts c JOIN messages m ON m.id=c.message_id WHERE c.reply_id IS NOT NULL AND m.origin='human'").fetchone()[0]
        summary['discovery_warnings'] = len(discovery_warnings or [])
        status = 'partial' if summary['files_failed'] or summary['malformed_lines'] or summary['discovery_warnings'] else 'complete'
        con.execute('UPDATE import_runs SET finished_at=?,status=?,summary_json=? WHERE id=?',
                    (now(), status, json.dumps(dict(summary)), run_id))
        con.commit()
        return dict(summary, status=status, run_id=run_id)
    except BaseException:
        con.rollback()
        con.execute("UPDATE import_runs SET finished_at=?,status='interrupted',summary_json=? WHERE id=?",
                    (now(), json.dumps(dict(summary)), run_id))
        con.commit()
        raise
