import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ai_archive.db import connect, ProjectResolver
from ai_archive.discovery import SourceRoot
from ai_archive.importer import import_archive
from ai_archive.server import ArchiveServer, messages, overview, threads


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.database = self.base / 'archive.sqlite3'
        self.con = connect(self.database)
        self.addCleanup(self.con.close)
        root = self.base / '.claude'
        source = root / 'projects' / 'test.jsonl'
        source.parent.mkdir(parents=True)
        records = []
        for num, text in enumerate(['Build accessible checkout', 'Add social previews', 'Find quoted "search" syntax']):
            records.append({'type': 'user', 'uuid': f'uuid-{num}', 'sessionId': 'parent',
                            'cwd': '/example/project', 'timestamp': f'2026-10-15T08:0{num}:00Z',
                            'message': {'role': 'user', 'content': text}})
        source.write_text(''.join(json.dumps(record) + '\n' for record in records))
        import_archive(self.con, [SourceRoot('claude', root)])

    def test_project_thread_search_and_chronology(self):
        index = overview(self.con)
        self.assertEqual(index['stats']['messages'], 3)
        project = index['projects'][0]
        result = messages(self.con, {'project_id': [project['id']]})
        self.assertEqual(result['total'], 3)
        self.assertEqual([m['text'] for m in result['messages']], ['Build accessible checkout', 'Add social previews', 'Find quoted "search" syntax'])
        thread = threads(self.con, project['id'])['threads'][0]
        result = messages(self.con, {'thread_id': [thread['id']], 'q': ['social previews']})
        self.assertEqual(result['total'], 1)
        self.assertEqual(len(result['messages'][0]['provenance']), 1)
        self.assertEqual(result['messages'][0]['provenance'][0]['line'], 2)

    def test_search_is_literal_not_an_fts_program(self):
        for term in ['"search"', 'search OR accessible', "' ;DROP TABLE messages;--", '(']:
            messages(self.con, {'q': [term]})
        self.assertEqual(overview(self.con)['stats']['messages'], 3)

    def test_message_permalink_selects_an_exact_action_in_its_thread(self):
        message_id, thread_id = self.con.execute('SELECT id,primary_thread_id FROM messages WHERE text=?',
                                                ('Add social previews',)).fetchone()
        result = messages(self.con, {'message_id': [message_id], 'thread_id': [thread_id]})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['messages'][0]['id'], message_id)
        self.assertEqual(result['messages'][0]['thread_id'], thread_id)
        self.assertIn('previous_reply', result['messages'][0])
        self.assertEqual(messages(self.con, {'message_id': ['f' * 64]})['total'], 0)
        with self.assertRaises(ValueError):
            messages(self.con, {'message_id': ['not-a-message-id']})

    def test_pagination_dates_and_provider(self):
        result = messages(self.con, {'limit': ['1'], 'offset': ['1'], 'from': ['2026-10-15'], 'to': ['2026-10-15']})
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['messages'][0]['text'], 'Add social previews')
        self.assertEqual(messages(self.con, {'provider': ['codex']})['total'], 0)
        self.assertEqual(messages(self.con, {'order': ['desc']})['messages'][0]['text'], 'Find quoted "search" syntax')
        with self.assertRaises(ValueError):
            messages(self.con, {'from': ['not-a-date']})

    def test_cross_project_fork_filters_do_not_leak_other_project_scope(self):
        primary = self.con.execute("SELECT primary_thread_id FROM messages LIMIT 1").fetchone()[0]
        other, _ = ProjectResolver(self.con).resolve('/example/other-project')
        self.con.execute("INSERT INTO threads(id,provider,native_id,project_id,title) VALUES('fork','claude','fork',?,'Fork')", (other,))
        shared = self.con.execute('SELECT id FROM messages LIMIT 1').fetchone()[0]
        self.con.execute("INSERT INTO thread_messages VALUES('fork',?,1)", (shared,))
        original = self.con.execute('SELECT project_id FROM threads WHERE id=?', (primary,)).fetchone()[0]
        result = messages(self.con, {'project_id': [original], 'thread_id': ['fork']})
        self.assertEqual(result['total'], 0)
        self.assertEqual(messages(self.con, {'project_id': [other], 'thread_id': ['fork']})['total'], 1)

    def test_equal_timestamps_follow_source_order_in_thread(self):
        self.con.execute("UPDATE messages SET sent_at='2026-10-15T08:00:00.000000Z'")
        result = messages(self.con, {})
        self.assertEqual([message['text'] for message in result['messages']],
                         ['Build accessible checkout', 'Add social previews', 'Find quoted "search" syntax'])

    def test_http_privacy_static_assets_and_returned_social_metadata(self):
        server = ArchiveServer(('127.0.0.1', 0), self.database, 'https://example.com')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        with urlopen(origin + '/api/overview') as response:
            self.assertEqual(json.load(response)['stats']['messages'], 3)
        with urlopen(origin + '/') as response:
            body = response.read().decode()
            self.assertIn('https://example.com/share.png', body)
            self.assertNotIn('__PUBLIC_ORIGIN__', body)
            for key in ['og:title', 'og:description', 'og:type', 'og:url', 'og:image', 'og:image:alt', 'twitter:card', 'twitter:title', 'twitter:description', 'twitter:image', 'twitter:image:alt']:
                self.assertIn(key, body)
        for path in ['/api/overview', '/']:
            with self.assertRaises(HTTPError) as context:
                urlopen(Request(origin + path, headers={'Host': 'attacker.example'}))
            self.assertEqual(context.exception.code, 403)
        with self.assertRaises(HTTPError) as context:
            urlopen(Request(origin + '/api/messages', headers={'Sec-Fetch-Site': 'cross-site'}))
        self.assertEqual(context.exception.code, 403)
        with self.assertRaises(HTTPError):
            urlopen(origin + '/../../data/archive.sqlite3')

    def test_worktree_and_explicit_alias_resolution(self):
        repo = self.base / 'repo'
        (repo / '.git' / 'worktrees' / 'branch').mkdir(parents=True)
        worktree = self.base / 'worktree'
        worktree.mkdir()
        (worktree / '.git').write_text(f'gitdir: {repo}/.git/worktrees/branch')
        (repo / '.git' / 'worktrees' / 'branch' / 'commondir').write_text('../..')
        resolver = ProjectResolver(self.con, {'/old/project': str(repo)})
        self.assertEqual(resolver.resolve(str(worktree))[0], resolver.resolve(str(repo))[0])
        self.assertEqual(resolver.resolve('/old/project/src')[0], resolver.resolve(str(repo))[0])

    def test_clean_install_playbook_page_and_unavailable_download(self):
        server = ArchiveServer(('127.0.0.1', 0), self.database, 'https://example.com')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        with patch('ai_archive.playbook.PLAYBOOK', self.base / 'missing-playbook.json'):
            with urlopen(origin + '/playbook') as response:
                self.assertEqual(response.status, 200)
                self.assertIn('text/html', response.headers['Content-Type'])
                body = response.read().decode()
                self.assertIn('There is no draft saved in this copy yet.', body)
                self.assertIn('https://example.com/share.png', body)
                self.assertIn('name="robots" content="noindex,nofollow"', body)
                self.assertNotIn('href="/playbook.txt"', body)
            with self.assertRaises(HTTPError) as context:
                urlopen(origin + '/playbook.txt')
            self.assertEqual(context.exception.code, 404)
            self.assertEqual(json.load(context.exception), {'error': 'The playbook draft is not available yet.'})
            with urlopen(origin + '/api/overview') as response:
                self.assertEqual(json.load(response)['stats']['messages'], 3)


if __name__ == '__main__':
    unittest.main()
