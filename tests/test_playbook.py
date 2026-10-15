import unittest

from ai_archive.playbook import evidence_url, plain_text, render_empty_playbook, render_playbook


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
                     'evidence': [source], 'qualification': 'Scope is product-specific.'}]}],
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
