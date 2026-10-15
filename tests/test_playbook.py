from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from ai_archive.db import connect
from ai_archive.discovery import SourceRoot
from ai_archive.importer import import_archive
from ai_archive.playbook import (evidence_url, main, markdown_text, plain_text,
                                 render_empty_playbook, render_playbook, validate_playbook)


class PlaybookTests(unittest.TestCase):
    def book(self):
        source = {'message_id': 'a' * 64, 'thread_id': 'b' * 64, 'project_label': 'Example app',
                  'product_key': 'example', 'provider': 'codex', 'sent_at': '2026-10-15T08:00:00Z',
                  'quote': 'make it work <script>alert(1)</script>', 'context_note': 'Adopts the previous final plan.'}
        return {'title': 'Customer facing app playbook', 'status': 'Draft 1 for review', 'date': '15 October 2026',
                'introduction': 'A draft of recurring requirements.', 'usage': 'Apply the relevant modules.',
                'sections': [{'id': 'customer-experience', 'title': 'Customer experience', 'rules': [
                    {'id': 'CF-01', 'title': 'Working customer flows', 'applies_to': 'Apps with accounts',
                     'requirement': 'Test a real user journey.', 'checks': ['Verify the result.'],
                     'evidence': [source], 'qualification': 'Scope is product-specific.',
                     'research_note': 'The repeated requests refer to three related products.'}]}],
                'decisions': ['Choose the access model.'], 'methodology': 'Human evidence with preceding AI context.',
                'snapshot': {'messages': 100, 'projects': 3, 'messages_with_previous_reply': 75}}

    def test_source_links_keep_the_thread_specific_context(self):
        book = self.book()
        source = book['sections'][0]['rules'][0]['evidence'][0]
        url = evidence_url(source, 'http://127.0.0.1:8765')
        self.assertIn('message_id=' + 'a' * 64, url)
        self.assertIn('thread_id=' + 'b' * 64, url)
        rendered = render_playbook(book)
        self.assertIn('message_id=' + 'a' * 64 + '&amp;thread_id=' + 'b' * 64, rendered)

    def test_private_original_evidence_is_escaped_not_executed(self):
        rendered = render_playbook(self.book())
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', rendered)
        self.assertIn('name="robots" content="noindex,nofollow"', rendered)
        self.assertIn('og:image:alt', rendered)
        self.assertIn('twitter:card', rendered)

    def test_portable_draft_keeps_each_rule_and_original_citation(self):
        text = plain_text(self.book())
        self.assertIn('Draft 1 for review', text)
        self.assertIn('CF-01. Working customer flows', text)
        self.assertIn('Your message: "make it work <script>alert(1)</script>"', text)
        self.assertIn('Context: Adopts the previous final plan.', text)
        self.assertIn('thread_id=' + 'b' * 64, text)

    def test_clean_edition_keeps_the_same_actionable_rules_without_evidence(self):
        book = self.book()
        for renderer in (plain_text, markdown_text):
            clean = renderer(book, include_evidence=False)
            evidence = renderer(book, include_evidence=True)
            for rule_text in ('Working customer flows', 'Apps with accounts', 'Test a real user journey.',
                              'Verify the result.', 'Scope is product-specific.', 'CF-01'):
                self.assertIn(rule_text, clean)
                self.assertIn(rule_text, evidence)
            for private in ('<script>', 'Adopts the previous final plan.', 'a' * 64, 'b' * 64,
                            'Evidence:', '#### Evidence', 'EVIDENCE NOTES', 'Evidence notes',
                            'Human evidence with preceding AI context.', 'Choose the access model.',
                            'The repeated requests refer to three related products.'):
                self.assertNotIn(private, clean)
            self.assertIn('message_id=' + 'a' * 64, evidence)
            self.assertIn('Research note: The repeated requests refer to three related products.', evidence)
        rendered = render_playbook(book)
        self.assertIn('The repeated requests refer to three related products.', rendered)
        self.assertIn('href="/playbook-clean.txt"', rendered)
        self.assertIn('Download playbook (no evidence)', rendered)
        self.assertIn('href="/playbook-with-evidence.txt"', rendered)

    def local_evidence(self, base):
        database = base / 'archive.sqlite3'
        root = base / '.claude'
        source = root / 'projects' / 'test.jsonl'
        source.parent.mkdir(parents=True)
        records = [
            {'type': 'user', 'uuid': 'first', 'sessionId': 'example', 'cwd': '/example/project',
             'timestamp': '2026-10-15T08:00:00Z', 'message': {'role': 'user', 'content': 'Make a plan.'}},
            {'type': 'assistant', 'uuid': 'reply-event', 'sessionId': 'example', 'timestamp': '2026-10-15T08:01:00Z',
             'message': {'id': 'reply', 'role': 'assistant', 'stop_reason': 'end_turn',
                         'content': [{'type': 'text', 'text': 'I will test the account registration journey.'}]}},
            {'type': 'user', 'uuid': 'adoption', 'sessionId': 'example', 'cwd': '/example/project',
             'timestamp': '2026-10-15T08:02:00Z', 'message': {'role': 'user', 'content': 'Please implement that plan.'}},
        ]
        source.write_text(''.join(json.dumps(record) + '\n' for record in records))
        with connect(database) as con:
            import_archive(con, [SourceRoot('claude', root)])
            row = con.execute('SELECT * FROM messages WHERE text=?', ('Please implement that plan.',)).fetchone()
            book = self.book()
            item = book['sections'][0]['rules'][0]['evidence'][0]
            item.update(message_id=row['id'], thread_id=row['primary_thread_id'], provider=row['provider'],
                        sent_at=row['sent_at'], quote='implement that plan.',
                        previous_reply_quote='test the account registration journey.')
        return database, book

    def test_citations_are_validated_against_human_text_and_the_precise_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            database, book = self.local_evidence(Path(directory))
            with connect(database, readonly=True) as con:
                self.assertEqual(validate_playbook(book, con),
                                 {'rules': 1, 'unique_source_messages': 1, 'quotations_validated': 1})
                for field, value, reason in [('quote', 'an invented quote', 'verbatim'),
                                             ('thread_id', 'f' * 64, 'cited thread'),
                                             ('previous_reply_quote', 'an invented AI quote', 'preceding final reply'),
                                             ('provider', 'codex', 'provider and sent_at')]:
                    altered = deepcopy(book)
                    altered['sections'][0]['rules'][0]['evidence'][0][field] = value
                    with self.subTest(field=field), self.assertRaisesRegex(ValueError, reason):
                        validate_playbook(altered, con)
            with connect(database) as con:
                con.execute("UPDATE messages SET origin='review'")
            with connect(database, readonly=True) as con:
                with self.assertRaisesRegex(ValueError, 'retained human message'):
                    validate_playbook(book, con)

    def test_export_command_writes_private_paired_editions_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            database, book = self.local_evidence(base)
            draft = base / 'draft.json'
            draft.write_text(json.dumps(book))
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(['--db', str(database), '--input', str(draft), '--write-outputs',
                                       '--origin', 'http://127.0.0.1:8766']), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(len(result['outputs']), 4)
            for suffix in ('.md', '.txt'):
                clean = base / ('playbook' + suffix)
                evidence = base / ('playbook-with-evidence' + suffix)
                self.assertIn('Test a real user journey.', clean.read_text())
                self.assertNotIn('message_id=', clean.read_text())
                self.assertIn('http://127.0.0.1:8766/?message_id=', evidence.read_text())
                self.assertIn('test the account registration journey.', evidence.read_text())
                if os.name != 'nt':
                    self.assertEqual(clean.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(evidence.stat().st_mode & 0o777, 0o600)
            book['sections'][0]['rules'][0]['evidence'][0]['quote'] = 'fabricated'
            draft.write_text(json.dumps(book))
            before = (base / 'playbook-with-evidence.md').read_text()
            with redirect_stdout(StringIO()), self.assertRaises(SystemExit):
                main(['--db', str(database), '--input', str(draft), '--write-outputs'])
            self.assertEqual((base / 'playbook-with-evidence.md').read_text(), before)

    def test_empty_playbook_explains_the_actual_workflow_without_seed_data(self):
        rendered = render_empty_playbook('https://example.com')
        self.assertIn('There is no draft saved in this copy yet.', rendered)
        self.assertIn('Ask an AI assistant working with your archive', rendered)
        self.assertIn('there is no built-in generation button', rendered)
        self.assertIn('href="/">Browse your messages', rendered)
        self.assertNotIn('href="/playbook.txt"', rendered)
        self.assertNotIn('CF-01', rendered)
        self.assertIn('name="robots" content="noindex,nofollow"', rendered)
        self.assertIn('https://example.com/playbook', rendered)
        self.assertIn('https://example.com/share.png', rendered)
        for key in ['og:title', 'og:description', 'og:type', 'og:url', 'og:image', 'og:image:alt',
                    'twitter:card', 'twitter:title', 'twitter:description', 'twitter:image', 'twitter:image:alt']:
            self.assertIn(key, rendered)


if __name__ == '__main__':
    unittest.main()
