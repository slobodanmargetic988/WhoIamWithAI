"""Final-response context survives upgrades, copies, forks, and repeat imports."""
import json
from pathlib import Path
import tempfile
import unittest

from ai_archive.db import connect, stable_id
from ai_archive.discovery import SourceRoot
from ai_archive.importer import import_archive
from ai_archive.server import messages


def user(ident, text, thread='conversation', second=0):
    return {'type': 'user', 'uuid': ident, 'sessionId': thread, 'cwd': '/example/project',
            'timestamp': f'2026-10-15T08:00:{second:02d}Z',
            'message': {'role': 'user', 'content': text}}


def assistant(ident, text, thread='conversation', second=1, stop='end_turn'):
    return {'type': 'assistant', 'uuid': 'event-' + ident, 'sessionId': thread,
            'timestamp': f'2026-10-15T08:00:{second:02d}Z',
            'message': {'id': ident, 'role': 'assistant', 'stop_reason': stop,
                        'content': [{'type': 'text', 'text': text}]}}


class ReplyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.database = self.base / 'archive.sqlite3'
        self.con = connect(self.database)
        self.addCleanup(lambda: self.con.close())

    def source(self, name, records):
        root = self.base / name
        path = root / 'projects' / 'conversation.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        return SourceRoot('claude', root), path

    def count(self, table):
        return self.con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_original_final_is_separate_and_returned_before_followup(self):
        final = '1. Design the schema.\n2. Build the importer.\n3. Test the app.'
        root, _ = self.source('.claude', [user('u1', 'Make a plan'), assistant('a1', final),
                                        user('u2', 'Implement steps 1 and 2', second=2)])
        result = import_archive(self.con, [root])
        self.assertEqual(result['messages'], 2)
        self.assertEqual(result['messages_with_previous_reply'], 1)
        self.assertEqual(self.count('agent_replies'), 1)
        response = messages(self.con, {})['messages']
        self.assertIsNone(response[0]['previous_reply'])
        self.assertEqual(response[1]['previous_reply']['text'], final)
        self.assertEqual(response[1]['previous_reply']['provenance'][0]['line'], 2)
        self.assertEqual(response[1]['text'], 'Implement steps 1 and 2')
        self.assertEqual(response[1]['previous_reply_status'], 'available')
        self.assertEqual(self.con.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_backups_share_reply_identity_and_keep_both_source_locations(self):
        records = [user('u1', 'Plan'), assistant('a1', 'Original final plan'), user('u2', 'Do it', second=2)]
        first, _ = self.source('.claude', records)
        copy, _ = self.source('backup', records)
        import_archive(self.con, [first, copy])
        self.assertEqual(self.count('agent_replies'), 1)
        self.assertEqual(self.count('agent_reply_sources'), 2)
        result = messages(self.con, {})['messages'][1]
        self.assertEqual(len(result['previous_reply']['provenance']), 2)
        again = import_archive(self.con, [first, copy])
        self.assertEqual(again['files_unchanged'], 2)
        self.assertEqual(self.count('agent_replies'), 1)

    def test_copied_user_event_gets_context_from_its_selected_thread(self):
        first, _ = self.source('first', [user('p1', 'Plan A', thread='parent'),
                                          assistant('a1', 'The plan for A', thread='parent'),
                                          user('shared', 'Implement it', thread='parent', second=2)])
        fork, _ = self.source('fork', [user('p2', 'Plan B', thread='fork'),
                                        assistant('a2', 'The plan for B', thread='fork'),
                                        user('shared', 'Implement it', thread='fork', second=2)])
        import_archive(self.con, [first, fork])
        for native, expected in [('parent', 'The plan for A'), ('fork', 'The plan for B')]:
            thread_id = self.con.execute('SELECT id FROM threads WHERE native_id=?', (native,)).fetchone()[0]
            data = messages(self.con, {'thread_id': [thread_id], 'q': ['Implement']})
            self.assertEqual(data['messages'][0]['previous_reply']['text'], expected)

    def test_context_is_retained_when_compaction_removes_the_old_final(self):
        root, path = self.source('claude', [user('u1', 'Plan'), assistant('a1', 'The original plan'),
                                           user('u2', 'Implement', second=2)])
        import_archive(self.con, [root])
        path.write_text(json.dumps(user('u2', 'Implement', second=2)) + '\n')
        import_archive(self.con, [root])
        result = messages(self.con, {'q': ['Implement']})['messages'][0]
        self.assertEqual(result['previous_reply']['text'], 'The original plan')
        self.assertEqual(result['previous_reply']['provenance'][0]['is_current'], 0)

    def test_upgrade_keeps_existing_messages_and_rescans_unchanged_files(self):
        root, _ = self.source('claude', [user('u1', 'Plan'), assistant('a1', 'Original plan'),
                                        user('u2', 'Implement', second=2)])
        import_archive(self.con, [root])
        ids = [row[0] for row in self.con.execute('SELECT id FROM messages ORDER BY id')]
        # Reproduce the v1 database shape, retaining its existing human archive.
        self.con.executescript('''DROP VIEW preferred_message_contexts;
            DROP TABLE message_contexts; DROP TABLE agent_reply_sources; DROP TABLE agent_replies;
            ALTER TABLE source_files DROP COLUMN context_version;
            DELETE FROM schema_version WHERE version=2;''')
        self.con.commit()
        self.con.close()
        self.con = connect(self.database)
        self.assertEqual(self.con.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 2)
        self.assertEqual(self.con.execute('SELECT context_version FROM source_files').fetchone()[0], 0)
        result = import_archive(self.con, [root])
        self.assertEqual(result['files_imported'], 1)
        self.assertEqual(result['messages_with_previous_reply'], 1)
        self.assertEqual([r[0] for r in self.con.execute('SELECT id FROM messages ORDER BY id')], ids)

    def test_incomplete_work_does_not_attach_an_older_final(self):
        root, _ = self.source('claude', [user('u1', 'First plan'), assistant('a1', 'First final'),
            user('u2', 'Another task', second=2), assistant('a2', 'I am still working', second=3, stop='tool_use'),
            user('u3', 'Actually stop', second=4)])
        import_archive(self.con, [root])
        result = messages(self.con, {'q': ['Actually']})['messages'][0]
        self.assertIsNone(result['previous_reply'])

    def test_existing_global_codex_identity_is_repaired_and_unchanged_source_rescanned(self):
        from test_importer import codex_session, codex_message
        root = self.base / '.codex'
        path = root / 'sessions' / 'conversation.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('\n'.join(json.dumps(r) for r in [codex_session(), codex_message(native='item-14')]) + '\n')
        source = SourceRoot('codex', root)
        import_archive(self.con, [source])
        new_id = self.con.execute('SELECT id FROM messages').fetchone()[0]
        old_id = stable_id('message', 'codex', 'item-14')
        self.con.commit()
        self.con.execute('PRAGMA foreign_keys=OFF')
        for table, column in [('messages', 'id'), ('thread_messages', 'message_id'),
                              ('message_sources', 'message_id'), ('message_contexts', 'message_id')]:
            self.con.execute(f'UPDATE {table} SET {column}=? WHERE {column}=?', (old_id, new_id))
        self.con.commit()
        self.con.execute('PRAGMA foreign_keys=ON')
        result = import_archive(self.con, [source])
        self.assertEqual(result['codex_identities_repaired'], 1)
        self.assertEqual(result['files_imported'], 1)
        self.assertEqual(self.count('messages'), 1)
        self.assertEqual(self.con.execute('SELECT id FROM messages').fetchone()[0], new_id)
        self.assertEqual(self.con.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_shifted_copy_cannot_link_a_reply_after_canonical_user_time(self):
        first, _ = self.source('a-original', [user('u2', 'Implement it', second=0)])
        second, _ = self.source('b-copy', [user('u1', 'Plan', second=0), assistant('a1', 'The plan', second=1),
                                          user('u2', 'Implement it', second=2)])
        import_archive(self.con, [first, second])
        result = messages(self.con, {'q': ['Implement']})['messages'][0]
        self.assertIsNone(result['previous_reply'])


if __name__ == '__main__':
    unittest.main()
