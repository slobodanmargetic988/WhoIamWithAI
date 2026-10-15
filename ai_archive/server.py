from __future__ import annotations

from datetime import date
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .db import DEFAULT_DB, connect

WEB = Path(__file__).resolve().parent.parent / 'web'


def rows(con, query, params=()):
    return [dict(row) for row in con.execute(query, params)]


def overview(con):
    projects = rows(con, '''SELECT p.id,p.name,p.canonical_path,
         COUNT(DISTINCT m.id) AS message_count,COUNT(DISTINCT t.id) AS thread_count,
         MAX(m.sent_at) AS last_message_at,GROUP_CONCAT(DISTINCT m.provider) AS providers
         FROM projects p JOIN threads t ON t.project_id=p.id
         JOIN thread_messages tm ON tm.thread_id=t.id
         JOIN messages m ON m.id=tm.message_id AND m.origin='human'
         GROUP BY p.id ORDER BY last_message_at DESC,p.name COLLATE NOCASE''')
    for project in projects:
        project['providers'] = project['providers'].split(',')
    imports = rows(con, 'SELECT * FROM import_runs ORDER BY id DESC LIMIT 5')
    for record in imports:
        record['summary'] = json.loads(record.pop('summary_json'))
    last = con.execute('SELECT MAX(finished_at) FROM import_runs').fetchone()[0]
    stats = {
        'projects': len(projects),
        'threads': con.execute("SELECT COUNT(DISTINCT thread_id) FROM thread_messages tm JOIN messages m ON m.id=tm.message_id WHERE m.origin='human'").fetchone()[0],
        'messages': con.execute("SELECT COUNT(*) FROM messages WHERE origin='human'").fetchone()[0],
        'review_messages': con.execute("SELECT COUNT(*) FROM messages WHERE origin='review'").fetchone()[0],
        'profiles': con.execute('SELECT COUNT(*) FROM profiles').fetchone()[0],
        'last_import_at': last,
        'missing_timestamps': con.execute("SELECT COUNT(*) FROM messages WHERE origin='human' AND sent_at IS NULL").fetchone()[0],
        'messages_with_previous_reply': con.execute("SELECT COUNT(DISTINCT c.message_id) FROM preferred_message_contexts c JOIN messages m ON m.id=c.message_id WHERE c.reply_id IS NOT NULL AND m.origin='human'").fetchone()[0],
    }
    providers = [row[0] for row in con.execute("SELECT DISTINCT provider FROM messages WHERE origin='human' ORDER BY provider")]
    return dict(stats=stats, projects=projects, providers=providers, imports=imports)


def threads(con, project_id, origin='human'):
    return {'threads': rows(con, '''SELECT t.id,t.title,t.provider,t.cwd,t.is_agent_thread,
          MIN(m.sent_at) AS started_at,MAX(m.sent_at) AS last_message_at,COUNT(DISTINCT m.id) AS message_count
          FROM threads t JOIN thread_messages tm ON tm.thread_id=t.id
          JOIN messages m ON m.id=tm.message_id AND m.origin=? WHERE t.project_id=?
          GROUP BY t.id ORDER BY last_message_at DESC,t.id''', (origin, project_id))}


def messages(con, query):
    def get(key, default=''):
        return query.get(key, [default])[0]
    try:
        limit = min(200, max(1, int(get('limit', '40'))))
        offset = max(0, int(get('offset', '0')))
    except ValueError:
        raise ValueError('limit and offset must be integers') from None
    origin = get('origin', 'human')
    if origin not in {'human', 'review'}:
        raise ValueError('origin must be human or review')
    conditions, params = ['m.origin=?'], [origin]
    message_id = get('message_id')
    if message_id:
        if not re.fullmatch(r'[0-9a-f]{64}', message_id):
            raise ValueError('message_id must be a valid archive message ID')
        conditions.append('m.id=?')
        params.append(message_id)
    project_id, thread_id = get('project_id'), get('thread_id')
    if project_id:
        conditions.append('EXISTS(SELECT 1 FROM thread_messages sm JOIN threads st ON st.id=sm.thread_id WHERE sm.message_id=m.id AND st.project_id=?)')
        params.append(project_id)
    if thread_id:
        conditions.append('EXISTS(SELECT 1 FROM thread_messages sm WHERE sm.message_id=m.id AND sm.thread_id=?)')
        params.append(thread_id)
        if project_id:
            conditions.append('EXISTS(SELECT 1 FROM threads selected WHERE selected.id=? AND selected.project_id=?)')
            params.extend([thread_id, project_id])
    if get('provider'):
        if get('provider') not in {'codex', 'claude'}:
            raise ValueError('Unknown provider')
        conditions.append('m.provider=?')
        params.append(get('provider'))
    search = get('q').strip()
    if search:
        terms = re.findall(r'\w+', search, re.UNICODE)
        if terms:
            expression = ' AND '.join('"' + term.replace('"', '""') + '"' for term in terms[:30])
            conditions.append('m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)')
            params.append(expression)
        else:
            conditions.append('INSTR(m.text,?)>0')
            params.append(search)
    for key, operator in [('from', '>='), ('to', '<=')]:
        if get(key):
            try:
                date.fromisoformat(get(key))
            except ValueError:
                raise ValueError(f'{key} must be a date in YYYY-MM-DD format') from None
            conditions.append(f'SUBSTR(m.sent_at,1,10){operator}?')
            params.append(get(key))
    where = ' AND '.join(conditions)
    total = con.execute(f'SELECT COUNT(*) FROM messages m WHERE {where}', params).fetchone()[0]
    # A copied message can belong to several threads. Select a representative
    # within the requested scope without multiplying the original human action.
    thread_choice = 'm.primary_thread_id'
    choice_params = []
    if thread_id:
        thread_choice = '?'
        choice_params = [thread_id]
    elif project_id:
        thread_choice = '''COALESCE(
            (SELECT st.id FROM threads st WHERE st.id=m.primary_thread_id AND st.project_id=?),
            (SELECT sm.thread_id FROM thread_messages sm JOIN threads st ON st.id=sm.thread_id
             WHERE sm.message_id=m.id AND st.project_id=? ORDER BY sm.thread_id LIMIT 1))'''
        choice_params = [project_id, project_id]
    order = 'DESC' if get('order') == 'desc' else 'ASC'
    result = rows(con, f'''SELECT m.id,m.text,m.sent_at,m.provider,m.origin,m.classification_reason,
              m.content_json,selected_tm.ordinal,t.id AS thread_id,t.title AS thread_title,p.id AS project_id,p.name AS project_name
              FROM messages m JOIN threads t ON t.id={thread_choice} JOIN projects p ON p.id=t.project_id
              JOIN thread_messages selected_tm ON selected_tm.thread_id=t.id AND selected_tm.message_id=m.id
              WHERE {where} ORDER BY (m.sent_at IS NULL) ASC,m.sent_at {order},t.id {order},selected_tm.ordinal {order},m.id {order} LIMIT ? OFFSET ?''',
                  (*choice_params, *params, limit, offset))
    if result:
        ids = [item['id'] for item in result]
        marks = ','.join('?' for _ in ids)
        provenance = rows(con, f'''SELECT ms.message_id,s.path,ms.line,ms.record_sha256,ms.source_sha256,ms.is_current,p.name AS profile_name,
                      p.root_path AS profile_path,s.error FROM message_sources ms
                      JOIN source_files s ON s.id=ms.file_id JOIN file_profiles fp ON fp.file_id=s.id
                      JOIN profiles p ON p.id=fp.profile_id WHERE ms.message_id IN ({marks})
                      ORDER BY s.path,ms.line''', ids)
        grouped = {key: [] for key in ids}
        for entry in provenance:
            grouped[entry.pop('message_id')].append(entry)
        for item in result:
            item['content'] = json.loads(item.pop('content_json'))
            item['provenance'] = grouped[item['id']]
            item['profile_names'] = sorted({p['profile_name'] for p in item['provenance']})
            item['source_count'] = len({(p['path'], p['line']) for p in item['provenance']})
        attach_previous_replies(con, result)
    return dict(messages=result, total=total, offset=offset, limit=limit)


def attach_previous_replies(con, result):
    ids = [item['id'] for item in result]
    marks = ','.join('?' for _ in ids)
    contexts = rows(con, f'''SELECT c.message_id,c.thread_id,c.status,c.selection_reason AS context_selection_reason,
                  r.id,r.provider,r.native_id,r.text,r.sent_at,r.kind,r.selection_reason
                  FROM preferred_message_contexts c LEFT JOIN agent_replies r ON r.id=c.reply_id
                  WHERE c.message_id IN ({marks})''', ids)
    scoped = {(c['message_id'], c['thread_id']): c for c in contexts}
    reply_ids = sorted({c['id'] for c in contexts if c['id']})
    grouped = {key: [] for key in reply_ids}
    if reply_ids:
        placeholders = ','.join('?' for _ in reply_ids)
        provenance = rows(con, f'''SELECT rs.reply_id,s.path,rs.line,rs.record_sha256,rs.source_sha256,
                  rs.is_current,p.name AS profile_name,p.root_path AS profile_path,s.error
                  FROM agent_reply_sources rs JOIN source_files s ON s.id=rs.file_id
                  JOIN file_profiles fp ON fp.file_id=s.id JOIN profiles p ON p.id=fp.profile_id
                  WHERE rs.reply_id IN ({placeholders}) ORDER BY s.path,rs.line''', reply_ids)
        for entry in provenance:
            grouped[entry.pop('reply_id')].append(entry)
    for item in result:
        context = scoped.get((item['id'], item['thread_id']))
        item['previous_reply'] = None
        item['previous_reply_status'] = context['status'] if context else 'not_yet_scanned'
        if context and context['id']:
            item['previous_reply'] = {key: context[key] for key in
                                     ['id', 'provider', 'native_id', 'text', 'sent_at', 'kind', 'selection_reason', 'thread_id']}
            item['previous_reply']['selection_reason'] = context['context_selection_reason']
            item['previous_reply']['provenance'] = grouped[context['id']]


def sources(con):
    files = rows(con, 'SELECT * FROM source_files ORDER BY provider,path')
    excluded = {}
    for file in files:
        file['skipped'] = json.loads(file.pop('skipped_json'))
        for kind, count in file['skipped'].items():
            excluded[kind] = excluded.get(kind, 0) + count
    return dict(profiles=rows(con, 'SELECT * FROM profiles ORDER BY provider,root_path'), files=files,
                summary={'files': len(files), 'bytes': sum(f['size_bytes'] for f in files), 'excluded_records': excluded},
                issues=rows(con, 'SELECT * FROM import_issues ORDER BY id DESC LIMIT 100'))


class ArchiveServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, database, public_origin=None):
        self.database = Path(database)
        self.public_origin = public_origin
        if public_origin:
            parts = urlsplit(public_origin)
            if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.path not in {'', '/'} or parts.query or parts.fragment:
                raise ValueError('PUBLIC_ORIGIN must be an absolute HTTPS origin without a path')
            self.public_origin = public_origin.rstrip('/')
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Search terms and conversation identifiers do not belong in logs.

    def reply(self, status, body, content_type='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store' if content_type.startswith('application/json') else 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        host = self.headers.get('Host', '').lower()
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in allowed:
            self.reply(403, {'error': 'This archive is available only on this computer.'})
            return
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query, keep_blank_values=True)
        if path.startswith('/api/'):
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.reply(403, {'error': 'Cross-site archive access is blocked.'})
                return
            try:
                with connect(self.server.database, readonly=True) as con:
                    if path == '/api/overview':
                        data = overview(con)
                    elif re.fullmatch(r'/api/projects/[0-9a-f]{64}/threads', path):
                        data = threads(con, path.split('/')[3], query.get('origin', ['human'])[0])
                    elif path == '/api/messages':
                        data = messages(con, query)
                    elif path == '/api/sources':
                        data = sources(con)
                    elif path == '/api/export':
                        query.update(limit=['200'], offset=['0'])
                        # Stream-friendly line format, one unique human action per
                        # row. The export is data, never executable spreadsheet cells.
                        exported = []
                        while True:
                            page = messages(con, query)
                            exported.extend(json.dumps(m, ensure_ascii=False) for m in page['messages'])
                            query['offset'] = [str(page['offset'] + len(page['messages']))]
                            if not page['messages'] or int(query['offset'][0]) >= page['total']:
                                break
                        self.reply(200, '\n'.join(exported) + ('\n' if exported else ''),
                                   'application/x-ndjson; charset=utf-8',
                                   {'Content-Disposition': 'attachment; filename="human-messages.jsonl"'})
                        return
                    else:
                        self.reply(404, {'error': 'Endpoint not found'})
                        return
                self.reply(200, data)
            except ValueError as error:
                self.reply(400, {'error': str(error)})
            except Exception:
                self.reply(500, {'error': 'Could not read the archive. Check the local database and retry.'})
            return
        if path == '/health':
            self.reply(200, {'status': 'ok'})
            return
        if path in {'/playbook', '/playbook.txt', '/playbook-clean.txt', '/playbook-with-evidence.txt'}:
            from .playbook import load_playbook, render_playbook, render_empty_playbook, plain_text
            origin = self.server.public_origin or f'http://127.0.0.1:{self.server.server_port}'
            try:
                book = load_playbook()
            except FileNotFoundError:
                if path == '/playbook':
                    self.reply(200, render_empty_playbook(origin), 'text/html; charset=utf-8')
                else:
                    self.reply(404, {'error': 'The playbook draft is not available yet.'})
                return
            except (OSError, ValueError):
                self.reply(404, {'error': 'The playbook draft is not available yet.'})
                return
            if path.endswith('.txt'):
                include_evidence = path != '/playbook-clean.txt'
                filename = {'/playbook-clean.txt': 'playbook.txt',
                            '/playbook-with-evidence.txt': 'playbook-with-evidence.txt',
                            '/playbook.txt': 'customer-facing-app-playbook.txt'}[path]
                self.reply(200, plain_text(book, origin, include_evidence=include_evidence), 'text/plain; charset=utf-8',
                           {'Content-Disposition': f'attachment; filename="{filename}"'})
            else:
                self.reply(200, render_playbook(book, origin), 'text/html; charset=utf-8')
            return
        if path == '/robots.txt':
            self.reply(200, 'User-agent: *\nDisallow: /api/\nAllow: /\nAllow: /share.png\n', 'text/plain; charset=utf-8')
            return
        names = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js',
                 '/playbook.css': 'playbook.css',
                 '/style.css': 'style.css', '/share.png': 'share.png', '/share.svg': 'share.svg', '/favicon.svg': 'favicon.svg'}
        target = WEB / names.get(path, '__missing__')
        if path not in names or not target.is_file():
            self.reply(404, {'error': 'Page not found'})
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        if target.suffix == '.html':
            origin = self.server.public_origin or f'http://127.0.0.1:{self.server.server_port}'
            body = body.replace(b'__PUBLIC_ORIGIN__', html.escape(origin, quote=True).encode())
        self.reply(200, body, content_type + ('; charset=utf-8' if target.suffix in {'.html', '.css', '.js', '.svg'} else ''))


def serve(database=DEFAULT_DB, port=8765, public_origin=None, *, open_browser=False):
    try:
        server = ArchiveServer(('127.0.0.1', port), database, public_origin or os.environ.get('PUBLIC_ORIGIN'))
    except OSError:
        if open_browser:
            from urllib.request import urlopen
            import webbrowser
            try:
                with urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                    healthy = json.load(response).get('status') == 'ok'
                if healthy:
                    webbrowser.open(f'http://127.0.0.1:{port}')
                    return
            except Exception:
                pass
        raise SystemExit(f'Cannot open port {port}. Choose another with --port.') from None
    print(f'Your local archive is ready: http://127.0.0.1:{server.server_port}', flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
