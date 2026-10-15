"""Render a private, evidence-linked playbook draft without remote services."""
from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlencode

PLAYBOOK = Path(__file__).resolve().parent.parent / 'data' / 'customer-facing-app-playbook.json'


def evidence_url(evidence, origin=''):
    return origin.rstrip('/') + '/?' + urlencode({'message_id': evidence['message_id'], 'thread_id': evidence['thread_id']})


def load_playbook(path=None):
    return json.loads(Path(PLAYBOOK if path is None else path).read_text())


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
    <li>Review and edit the draft before using it as instructions for a new app.</li></ol>
    <p class="qualification">The app collects and previews your history. Playbook analysis currently happens through an AI assistant; there is no built-in generation button.</p></article></section></main>
    <footer>Private and local · Your conversations and playbook stay on your computer.</footer></body></html>'''


def plain_text(book, origin='http://127.0.0.1:8765'):
    lines = [book['title'], f"{book['status']} | {book['date']}", '', book['introduction'], '',
             'HOW TO APPLY THIS DRAFT', book['usage'], '']
    for group in book['sections']:
        lines.extend([group['title'].upper(), ''])
        for rule in group['rules']:
            lines.extend([f"{rule['id']}. {rule['title']}", f"Applies when: {rule['applies_to']}", rule['requirement']])
            for check in rule.get('checks', []):
                lines.append('- ' + check)
            if rule.get('qualification'):
                lines.append('Scope note: ' + rule['qualification'])
            lines.append('Evidence:')
            for source in rule['evidence']:
                lines.append(f"- {source['project_label']} | {source['sent_at'][:10]} | {evidence_url(source, origin)}")
                lines.append(f'  Your message: "{source["quote"]}"')
                if source.get('context_note'):
                    lines.append('  Context: ' + source['context_note'])
            lines.append('')
    lines.extend(['DECISIONS FOR THE NEXT REVISION', *['- ' + decision for decision in book['decisions']], '',
                  'EVIDENCE NOTES', book['methodology'], ''])
    return '\n'.join(lines)


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
                evidence.append(f'''<li><div class="source-heading"><a href="{esc(evidence_url(source))}">
                    {esc(source['project_label'])} · {esc(source['sent_at'][:10])}<span aria-hidden="true"> ↗</span></a>
                    <span>{esc(source['provider'].capitalize())}</span></div>
                    <blockquote>{esc(source['quote'])}</blockquote>{context}</li>''')
            checks = ''.join(f'<li>{esc(check)}</li>' for check in rule.get('checks', []))
            qualification = f'<p class="qualification">{esc(rule["qualification"])}</p>' if rule.get('qualification') else ''
            rules.append(f'''<article class="rule-card" id="{esc(rule['id'])}">
                <header><span class="rule-number">{number:02d}</span><div><p class="rule-scope">{esc(rule['applies_to'])}</p>
                <h3>{esc(rule['title'])}</h3></div></header><p class="requirement">{esc(rule['requirement'])}</p>
                <ul class="checks">{checks}</ul>{qualification}
                <details class="evidence"><summary>Source messages <span>{len(rule['evidence'])} citations · {product_count} products</span></summary>
                <ol>{''.join(evidence)}</ol></details></article>''')
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
    <div class="actions"><a class="download" href="/playbook.txt" download="customer-facing-app-playbook.txt">Download agent instructions ↓</a>
    <span>{number} rules · linked to your own messages</span></div></div>
    <aside class="usage"><h2>How to apply this draft</h2><p>{esc(book['usage'])}</p></aside>
    <nav class="contents" aria-label="Playbook sections">{''.join(navigation)}<a href="#decisions">Decisions for you</a></nav>
    <div class="rules">{''.join(cards)}</div><section class="decisions" id="decisions"><h2>Decisions for the next revision</h2><ul>{decisions}</ul></section>
    <details class="methodology"><summary>About the evidence</summary><p>{esc(book['methodology'])}</p>
    <p>The archive snapshot contains {stats['messages']:,} human messages in {stats['projects']} project/workspace groups; {stats['messages_with_previous_reply']:,} have a captured preceding final reply.
    Uncertain or automated records are excluded from the draft.</p></details></main>
    <footer>Private and local · Open a source message to read its original AI reply context.</footer></body></html>'''
