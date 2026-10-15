"""Render a private, evidence-linked playbook draft without remote services."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

PLAYBOOK = Path(__file__).resolve().parent.parent / 'data' / 'customer-facing-app-playbook.json'


def evidence_url(evidence, origin=''):
    return origin.rstrip('/') + '/?' + urlencode({'message_id': evidence['message_id'], 'thread_id': evidence['thread_id']})


def source_date(source):
    return source['sent_at'][:10] if source.get('sent_at') else 'Date unknown'


def load_playbook(path=None):
    return json.loads(Path(PLAYBOOK if path is None else path).read_text(encoding='utf-8'))


def render_ecosystem_footer(local_note):
    """Simple outbound links keep the local app free of social widgets."""
    links = [
        ('Instagram', 'https://www.instagram.com/weeyuga?psln=emk2cHYwdW4zNmM4'),
        ('LinkedIn', 'https://www.linkedin.com/in/slobodan-margeti%C4%87-9a1229151/'),
        ('X', 'https://x.com/WeeyugaFounder'),
        ('Facebook', 'https://www.facebook.com/share/1GhcVRJ7vo/'),
    ]
    social_links = ''.join(
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
        f'aria-label="{label} (opens in a new tab)">{label}<span aria-hidden="true"> ↗</span></a>'
        for label, url in links
    )
    return f'''<footer class="ecosystem-footer" id="community"><div class="ecosystem-content">
    <div class="ecosystem-copy"><p class="ecosystem-label">MADE WITH WEEYUGA</p>
    <p class="ecosystem-title">Part of the <a href="https://weeyuga.com" target="_blank" rel="noopener noreferrer" aria-label="Weeyuga apps ecosystem (opens in a new tab)">Weeyuga apps ecosystem<span aria-hidden="true"> ↗</span></a>.</p>
    <p class="ecosystem-invitation">Like what I’m building? Follow me, or support the project if you can.</p></div>
    <nav class="ecosystem-links" aria-label="Follow Weeyuga and support the project">{social_links}
    <a class="ecosystem-support" href="https://github.com/sponsors/slobodanmargetic988" target="_blank" rel="noopener noreferrer" aria-label="Support on GitHub (opens in a new tab)">Support on GitHub<span aria-hidden="true"> ↗</span></a></nav>
    <p class="ecosystem-local">{html.escape(local_note)}</p></div></footer>'''


def render_empty_playbook(origin='http://127.0.0.1:8765'):
    """Explain the local, AI-assisted workflow without shipping anyone's draft."""
    title = 'Your app playbook'
    description = 'Build a personal app playbook from recurring requirements in your own messages and their AI reply context.'
    image = html.escape(origin.rstrip('/') + '/share.png', quote=True)
    destination = html.escape(origin.rstrip('/') + '/playbook', quote=True)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{title} · Who I Am With AI</title><meta name="description" content="{description}"><meta name="robots" content="noindex,nofollow">
    <link rel="canonical" href="{destination}"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/playbook.css">
    <meta property="og:title" content="{title}"><meta property="og:description" content="{description}"><meta property="og:type" content="website">
    <meta property="og:url" content="{destination}"><meta property="og:image" content="{image}"><meta property="og:image:alt" content="Who I Am With AI personal archive, connecting projects, conversations, and messages">
    <meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{title}"><meta name="twitter:description" content="{description}">
    <meta name="twitter:image" content="{image}"><meta name="twitter:image:alt" content="Who I Am With AI personal archive, connecting projects, conversations, and messages"></head>
    <body><header class="site-header"><a class="brand" href="/">who i am <span>with AI</span></a><a class="archive-link" href="/">← Browse your messages</a></header>
    <main><div class="intro"><p class="eyebrow">YOUR REPEATED REQUIREMENTS</p><p class="draft">Ready when you are</p>
    <h1>{title}</h1><p class="lead">Your playbook starts with your own conversations. There is no draft saved in this copy yet.</p>
    <div class="actions"><a class="download" href="/">Browse your messages →</a></div></div>
    <aside class="usage"><h2>From conversations to a useful starting point</h2><p>A playbook collects the requirements you repeat across projects, with links back to the messages that explain them.</p></aside>
    <section class="rule-section"><h2>Create your first draft</h2><article class="rule-card"><ol class="checks">
    <li>Collect your Codex and Claude messages into your local archive.</li>
    <li>Review the messages with their preceding AI replies so short follow-ups keep their context.</li>
    <li>Ask an AI assistant working with your archive to find recurring requirements, draft scoped rules, and cite the original messages.</li>
    <li>Have it prepare two versions: a clean playbook for everyday use, and the same playbook with evidence for checking the sources.</li>
    <li>Review and edit the draft before using it as instructions for a new app.</li></ol>
    <p class="qualification">The app collects and previews your history. Playbook analysis currently happens through an AI assistant; there is no built-in generation button.</p></article></section></main>
    {render_ecosystem_footer('Private and local · Your conversations and playbook stay on your computer.')}</body></html>'''


def plain_text(book, origin='http://127.0.0.1:8765', *, include_evidence=True):
    """The legacy default keeps evidence; the clean download opts out explicitly."""
    introduction = book['introduction'] if include_evidence else book.get('clean_introduction', 'Reusable requirements for the apps I build.')
    lines = [book['title'], f"{book['status']} | {book['date']}", '', introduction, '',
             'HOW TO APPLY THIS DRAFT', book['usage'], '']
    for group in book['sections']:
        lines.extend([group['title'].upper(), ''])
        for rule in group['rules']:
            heading = f"{rule['id']}. {rule['title']}"
            lines.extend([heading, f"Applies when: {rule['applies_to']}", rule['requirement']])
            for check in rule.get('checks', []):
                lines.append('- ' + check)
            if rule.get('qualification'):
                lines.append(rule['qualification'])
            if include_evidence:
                if rule.get('research_note'):
                    lines.append('Research note: ' + rule['research_note'])
                lines.append('Evidence:')
                for source in rule['evidence']:
                    lines.append(f"- {source['project_label']} | {source_date(source)} | {evidence_url(source, origin)}")
                    lines.append(f'  Your message: "{source["quote"]}"')
                    if source.get('context_note'):
                        lines.append('  Context: ' + source['context_note'])
                    if source.get('previous_reply_quote'):
                        lines.append(f'  Previous final AI reply: "{source["previous_reply_quote"]}"')
            lines.append('')
    if include_evidence:
        lines.extend(['DECISIONS FOR THE NEXT REVISION', *['- ' + decision for decision in book['decisions']], '',
                      'EVIDENCE NOTES', book['methodology'], ''])
    return '\n'.join(lines)


def markdown_text(book, origin='http://127.0.0.1:8765', *, include_evidence=False):
    """Two readable files built from the same rule bodies, never two AI drafts."""
    introduction = book['introduction'] if include_evidence else book.get('clean_introduction', 'Reusable requirements for the apps I build.')
    lines = [f"# {book['title']}", '', f"{book['status']} · {book['date']}", '', introduction, '',
             '## How to apply this playbook', '', book['usage'], '']
    for group in book['sections']:
        lines.extend([f"## {group['title']}", ''])
        for rule in group['rules']:
            lines.extend([f"### {rule['id']}. {rule['title']}", '', f"**Applies when:** {rule['applies_to']}", '', rule['requirement'], ''])
            lines.extend('- ' + check for check in rule.get('checks', []))
            if rule.get('checks'):
                lines.append('')
            if rule.get('qualification'):
                lines.extend([rule['qualification'], ''])
            if include_evidence:
                if rule.get('research_note'):
                    lines.extend(['Research note: ' + rule['research_note'], ''])
                lines.extend(['#### Evidence', ''])
                for source in rule['evidence']:
                    lines.extend([f"[{source['project_label']} · {source_date(source)}]({evidence_url(source, origin)})", '',
                                  *['> ' + html.escape(line, quote=False) for line in source['quote'].splitlines()], ''])
                    if source.get('context_note'):
                        lines.extend(['Context: ' + source['context_note'], ''])
                    if source.get('previous_reply_quote'):
                        lines.extend(['Previous final AI reply:', '',
                                      *['> ' + html.escape(line, quote=False) for line in source['previous_reply_quote'].splitlines()], ''])
    if include_evidence:
        lines.extend(['## Decisions for the next revision', '', *['- ' + item for item in book['decisions']], '',
                      '## Evidence notes', '', book['methodology'], ''])
    return '\n'.join(lines)


def validate_playbook(book, con):
    """Check shape and original citations without judging an AI's interpretation.

    The caller supplies a read-only connection. No transcript or credential file
    is opened, and missing context is never substituted with another thread.
    """
    def require(value, expected, label):
        if not isinstance(value, expected) or (expected in (str, list) and not value):
            raise ValueError(f'{label} must be a nonempty {expected.__name__}.')

    require(book, dict, 'Playbook')
    for name in ('title', 'status', 'date', 'introduction', 'usage', 'methodology'):
        require(book.get(name), str, name)
    if not isinstance(book.get('decisions'), list) or not all(isinstance(item, str) for item in book['decisions']):
        raise ValueError('decisions must be a list of strings (it may be empty).')
    require(book.get('snapshot'), dict, 'snapshot')
    for name in ('messages', 'projects', 'messages_with_previous_reply'):
        value = book['snapshot'].get(name)
        if type(value) is not int or value < 0:
            raise ValueError(f'snapshot.{name} must be a nonnegative integer.')
    require(book.get('sections'), list, 'sections')
    rules = 0
    quotations = 0
    sources = set()
    identifiers = set()
    for group in book['sections']:
        require(group, dict, 'Section')
        for name in ('id', 'title'):
            require(group.get(name), str, f'Section {name}')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', group['id']) or group['id'] in identifiers:
            raise ValueError('Section and rule IDs must be unique HTML identifiers.')
        identifiers.add(group['id'])
        require(group.get('rules'), list, 'rules')
        for rule in group['rules']:
            require(rule, dict, 'Rule')
            for name in ('id', 'title', 'applies_to', 'requirement'):
                require(rule.get(name), str, f'Rule {name}')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', rule['id']) or rule['id'] in identifiers:
                raise ValueError('Section and rule IDs must be unique HTML identifiers.')
            identifiers.add(rule['id'])
            if not isinstance(rule.get('checks', []), list) or not all(isinstance(item, str) for item in rule.get('checks', [])):
                raise ValueError('checks must be a list of strings.')
            for name in ('qualification', 'research_note'):
                if name in rule and not isinstance(rule[name], str):
                    raise ValueError(f'{name} must be a string.')
            require(rule.get('evidence'), list, 'evidence')
            rules += 1
            for source in rule['evidence']:
                require(source, dict, 'Evidence')
                for name in ('message_id', 'thread_id', 'project_label', 'product_key', 'provider', 'quote'):
                    require(source.get(name), str, f'Evidence {name}')
                if 'sent_at' not in source or (source['sent_at'] is not None and not isinstance(source['sent_at'], str)):
                    raise ValueError('Evidence sent_at must be the stored timestamp or null when unknown.')
                for name in ('message_id', 'thread_id'):
                    if not re.fullmatch(r'[0-9a-f]{64}', source[name]):
                        raise ValueError(f'Evidence {name} must be a stored archive ID.')
                row = con.execute('''SELECT m.text,m.provider,m.sent_at,m.origin FROM messages m
                    JOIN thread_messages tm ON tm.message_id=m.id
                    WHERE m.id=? AND tm.thread_id=?''', (source['message_id'], source['thread_id'])).fetchone()
                if row is None or row['origin'] != 'human':
                    raise ValueError('A source must be a retained human message in the cited thread.')
                if source['quote'] not in row['text']:
                    raise ValueError('A source quotation does not match its original human message verbatim.')
                if source['provider'] != row['provider'] or source['sent_at'] != row['sent_at']:
                    raise ValueError('Source provider and sent_at must match the original message.')
                for name in ('context_note', 'previous_reply_quote'):
                    if name in source and not isinstance(source[name], str):
                        raise ValueError(f'{name} must be a string.')
                if source.get('previous_reply_quote'):
                    reply = con.execute('''SELECT a.text FROM preferred_message_contexts c
                        JOIN agent_replies a ON a.id=c.reply_id
                        WHERE c.message_id=? AND c.thread_id=?''', (source['message_id'], source['thread_id'])).fetchone()
                    if reply is None or source['previous_reply_quote'] not in reply['text']:
                        raise ValueError('Previous AI quotation must match this message\'s captured preceding final reply.')
                sources.add(source['message_id'])
                quotations += 1
    return {'rules': rules, 'unique_source_messages': len(sources), 'quotations_validated': quotations}


def write_outputs(book, directory, origin='http://127.0.0.1:8765'):
    """Write private local files. Validation is performed by the CLI first."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    written = []
    for stem, evidence in [('playbook', False), ('playbook-with-evidence', True)]:
        for suffix, renderer in [('.md', markdown_text), ('.txt', plain_text)]:
            path = directory / (stem + suffix)
            # Restrict permissions before writing, including existing output files.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                os.chmod(path, 0o600)
                output.write(renderer(book, origin, include_evidence=evidence))
            written.append(path)
    return written


def main(argv=None):
    import argparse
    import sqlite3
    from .db import DEFAULT_DB, connect

    parser = argparse.ArgumentParser(description='Validate and export an AI-reviewed local playbook. This does not generate rules using AI.')
    parser.add_argument('--input', type=Path, default=PLAYBOOK, help='Private playbook JSON prepared by your AI assistant')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB, help='Local SQLite archive for citation checks (read-only)')
    parser.add_argument('--write-outputs', action='store_true', help='Write clean and evidence Markdown and TXT versions beside the input JSON')
    parser.add_argument('--origin', default='http://127.0.0.1:8765', help='Actual local app URL used in evidence links')
    args = parser.parse_args(argv)
    try:
        book = load_playbook(args.input)
        with connect(args.db, readonly=True) as con:
            result = validate_playbook(book, con)
        if args.write_outputs:
            result['outputs'] = [str(path) for path in write_outputs(book, args.input.parent, args.origin)]
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Playbook could not be exported: {error}\n')
    print(json.dumps(result, indent=2))
    return 0


def render_playbook(book, origin='http://127.0.0.1:8765'):
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    navigation = []
    number = 0
    for group in book['sections']:
        navigation.append(f'<a href="#{esc(group["id"])}">{esc(group["title"])}</a>')
        rules = []
        for rule in group['rules']:
            number += 1
            evidence = []
            product_count = len({s['product_key'] for s in rule['evidence']})
            for source in rule['evidence']:
                context = f'<p class="context-note">{esc(source["context_note"])}</p>' if source.get('context_note') else ''
                if source.get('previous_reply_quote'):
                    context += f'<p class="context-note">Previous final AI reply:</p><blockquote>{esc(source["previous_reply_quote"])}</blockquote>'
                evidence.append(f'''<li><div class="source-heading"><a href="{esc(evidence_url(source))}">
                    {esc(source['project_label'])} · {esc(source_date(source))}<span aria-hidden="true"> ↗</span></a>
                    <span>{esc(source['provider'].capitalize())}</span></div>
                    <blockquote>{esc(source['quote'])}</blockquote>{context}</li>''')
            checks = ''.join(f'<li>{esc(check)}</li>' for check in rule.get('checks', []))
            qualification = f'<p class="qualification">{esc(rule["qualification"])}</p>' if rule.get('qualification') else ''
            research = f'<p class="context-note"><strong>Research note:</strong> {esc(rule["research_note"])}</p>' if rule.get('research_note') else ''
            rules.append(f'''<article class="rule-card" id="{esc(rule['id'])}">
                <header><span class="rule-number">{number:02d}</span><div><p class="rule-scope">{esc(rule['applies_to'])}</p>
                <h3>{esc(rule['title'])}</h3></div></header><p class="requirement">{esc(rule['requirement'])}</p>
                <ul class="checks">{checks}</ul>{qualification}
                <details class="evidence"><summary>Source messages <span>{len(rule['evidence'])} citations · {product_count} products</span></summary>
                {research}<ol>{''.join(evidence)}</ol></details></article>''')
        cards.append(f'<section class="rule-section" id="{esc(group["id"])}"><h2>{esc(group["title"])}</h2>{"".join(rules)}</section>')
    decisions = ''.join(f'<li>{esc(decision)}</li>' for decision in book['decisions'])
    stats = book['snapshot']
    title = esc(book['title'])
    image = esc(origin.rstrip('/') + '/share.png')
    destination = esc(origin.rstrip('/') + '/playbook')
    description = 'A private first draft of recurring app requirements, with links to the original messages and their AI context.'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{title} · Who I Am With AI</title><meta name="description" content="{description}"><meta name="robots" content="noindex,nofollow">
    <link rel="canonical" href="{destination}"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/playbook.css">
    <meta property="og:title" content="{title}"><meta property="og:description" content="{description}"><meta property="og:type" content="website">
    <meta property="og:url" content="{destination}"><meta property="og:image" content="{image}"><meta property="og:image:alt" content="Who I Am With AI personal archive, connecting projects, conversations, and messages">
    <meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{title}"><meta name="twitter:description" content="{description}">
    <meta name="twitter:image" content="{image}"><meta name="twitter:image:alt" content="Who I Am With AI personal archive, connecting projects, conversations, and messages"></head>
    <body><header class="site-header"><a class="brand" href="/">who i am <span>with AI</span></a><a class="archive-link" href="/">← Browse your messages</a></header>
    <main><div class="intro"><p class="eyebrow">YOUR REPEATED REQUIREMENTS</p><p class="draft">{esc(book['status'])} · {esc(book['date'])}</p>
    <h1>{title}</h1><p class="lead">{esc(book['introduction'])}</p>
    <div class="actions"><a class="download" href="/playbook-clean.txt" download="playbook.txt">Download playbook (no evidence) ↓</a>
    <a href="/playbook-with-evidence.txt" download="playbook-with-evidence.txt">Download with evidence ↓</a>
    <span>{number} rules · linked to your own messages</span></div></div>
    <aside class="usage"><h2>How to apply this draft</h2><p>{esc(book['usage'])}</p></aside>
    <nav class="contents" aria-label="Playbook sections">{''.join(navigation)}<a href="#decisions">Decisions for you</a></nav>
    <div class="rules">{''.join(cards)}</div><section class="decisions" id="decisions"><h2>Decisions for the next revision</h2><ul>{decisions}</ul></section>
    <details class="methodology"><summary>About the evidence</summary><p>{esc(book['methodology'])}</p>
    <p>The archive snapshot contains {stats['messages']:,} human messages in {stats['projects']} project/workspace groups; {stats['messages_with_previous_reply']:,} have a captured preceding final reply.
    Uncertain or automated records are excluded from the draft.</p></details></main>
    {render_ecosystem_footer('Private and local · Open a source message to read its original AI reply context.')}</body></html>'''


if __name__ == '__main__':
    raise SystemExit(main())
