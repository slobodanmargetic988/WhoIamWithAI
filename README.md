# Who I Am With AI

A local archive of the instructions you gave Codex and Claude. Browse your words by project, conversation, and time, across every discovered local profile and backup, with one shared identity.

## Use it

Start with the [static setup guide](https://myaiplaybook.weeyuga.com/) or give your local coding agent [the setup and playbook instructions](docs/AGENT_SETUP.md). The guide includes a copyable task covering collection, context review and both playbook versions.

Python 3.11 or later, with SQLite FTS5 support. There are no packages to install, accounts to connect, or remote services for collection and browsing.

```sh
git clone https://github.com/slobodanmargetic988/WhoIamWithAI.git
cd WhoIamWithAI
python3 start.py --collect --open
```

On Windows, use `py -3 start.py --collect --open` instead. Without Git, use GitHub's **Code → Download ZIP**, extract the folder, then run its launcher. Double-click **Start.bat** on Windows or **Start.command** on macOS; on Linux run `sh Start.sh`. The menu offers collection or browsing an existing archive. It checks Python/search support and preserves existing messages. Python must be installed first; the launchers do not install software or require admin access. Native Windows execution has not been verified on the current development machine.

Open [your archive](http://127.0.0.1:8765). Keep the launcher window open while browsing; press Ctrl+C there to stop. The database is `data/archive.sqlite3`. Run collection again and refresh the app to see new messages. A large first collection can take a while.

For an agent working in stages, run `python3 start.py --check`, then `python3 start.py --collect-only`, then start `python3 start.py --browse --open` in a retained process. Windows uses `py -3` instead of `python3`. The underlying `python3 -m ai_archive import` and `serve` commands remain available.

The interface provides project and thread navigation, chronological messages, full-text search, provider and inclusive UTC date filters, pagination, message copying, and file/line provenance. Timestamps display in your browser's local time. A separate **Needs review** view retains inputs whose human authorship cannot be established from the available records.

Each message card also has a collapsed **Previous AI reply** section above your text. It shows the original final response from the preceding work, with its timestamp and source locations. It does not generate a new summary. First messages, interrupted work, and missing final responses are identified explicitly.

The context reader requires Codex final-answer phases or completed-turn responses, or Claude end-turn/stop-sequence/result/stop-hook completion markers. A text-only assistant message immediately before your next input is insufficient on its own: it might be interim narration from interrupted work. Tool results, reasoning, and interim narration are excluded. Context belongs to a message **within its thread**, so a copied message in a fork can have different preceding replies. Context already extracted is retained if a later compacted file removes it.

## Import behavior

The importer scans the current user's home for recognized Codex and Claude profile/backup directories. It also scans Claude's application support directory and follows existing rollout locations referenced in Codex's read-only state databases. It does not read credentials, change the original history files, or transmit data. It covers active and archived Codex sessions; Claude Code transcripts, Cowork/Desktop transcripts and audit files; and unmatched Claude CLI history entries as review candidates.

Discovery deliberately skips dependency directories, build outputs, caches, media libraries, the Trash, and most of `Library`. It cannot recover deleted histories or discover another OS user's inaccessible files. You can add sources anywhere explicitly:

```sh
python3 -m ai_archive discover
python3 -m ai_archive import --source codex:/path/to/profile --source claude:/path/to/profile
python3 -m ai_archive import --only-explicit --source claude:/path/to/transcripts
python3 -m ai_archive --db /absolute/path/archive.sqlite3 import --source codex:/path/to/profile
python3 -m ai_archive stats
```

The same event appearing in multiple profiles, backups, or forks is stored once, with its source occurrences and thread memberships retained. The importer streams JSONL line by line, skips unchanged source files using size and modification time, and reparses changed files. A first import reads several GB of local history; subsequent imports are much faster. Use `--force` to reprocess unchanged files after a parser update. Malformed records and incomplete final lines are reported and retried; valid neighboring messages still import. A partial import is visibly identified in the app and in the summary. Import summaries and issues remain in the database.

Schema version 2 adds the separate final-reply archive and context links. The upgrade also corrects the original Codex identifier scheme: item IDs are scoped by thread because values such as `item-14` repeat across unrelated conversations. The importer repairs legacy IDs and rescans Codex sources to recover merged actions. Claude UUIDs remain global. Source files track their context-parser version, so an old archive is automatically rescanned once when this feature is added. Back up an existing database before upgrading the collector.

Native UUIDs/event IDs are the strongest duplicate evidence. Legacy records without IDs use a deterministic timestamp/text/occurrence fallback. Two truly separate old submissions with identical timestamps and text across altered source copies may remain ambiguous. Missing or invalid timestamps stay unknown; the importer does not substitute file dates. Equal timestamps use source order within a thread. The order of simultaneous events in different threads cannot be determined from these files.

## Human authorship

Modern Codex completed `UserMessage` events and older explicit `user_message` events are preferred over model-input projections. Model-input context, AGENTS instructions, repeated user projections, and inherited agent history are filtered. Unmatched model-input messages, programmatic exec sessions, agent prompts, and automation markers are review candidates.

Claude top-level user inputs are retained; tool results, sidechains/subagents, meta messages, compaction summaries, notifications, and generated command output are excluded. SDK and ambiguous scheduled/system input go to review. CLI history entries supplement missing transcripts in the review view and are matched against transcript text/time to reduce duplication. Binary attachment payloads are omitted; attachment descriptions/references and image-only actions survive.

These rules reflect the local formats inspected during implementation. They are evidence-based heuristics, not proof that a person physically typed every retained record. In particular, old CLI sessions sometimes lack explicit human/automation flags. The review view and source provenance support auditing and later corrections. Playbook analysis should use retained human messages and review available preceding AI replies for context.

## Project identity

Recorded working directories resolve to existing Git repository roots. Existing Git worktrees resolve through their common Git directory. Historical worktrees also group with their original project when an exact recorded Git remote identifies one existing checkout, or Claude Desktop metadata explicitly identifies the worktree's origin. Ambiguous clones and VM-only working directories remain separate. Unassigned inputs remain clearly labelled. Different directories are never merged just because their names match.

To group moved, deleted, or otherwise ambiguous directories, provide explicit aliases. The longest matching old path prefix wins; each value denotes the canonical project root:

```json
{
  "/old/location/project": "/current/location/project",
  "/archived/worktree/project": "/current/location/project"
}
```

```sh
python3 -m ai_archive import --aliases /absolute/path/aliases.json
```

Aliases supplied by you override inferred mappings. Project assignments are refreshed even for unchanged transcripts, so changing an alias does not require reparsing all messages.

## Data and future analysis

See [the database design](docs/DATABASE.md) and the executable [schema](ai_archive/schema.sql). A single SQLite file holds the archive; stable message IDs let later requirement/playbook records cite the original evidence.

The app can display a locally prepared [playbook draft](http://127.0.0.1:8765/playbook), with source quotations and links that open the exact human message with its preceding AI reply expanded. The **Show full conversation** action restores the surrounding thread. **Copy link** creates the same thread-specific link for any message.

Ask your agent to follow [AGENT_SETUP.md](docs/AGENT_SETUP.md) and [the playbook format](docs/PLAYBOOK_FORMAT.md), prepare a local draft JSON and run `python3 -m ai_archive.playbook --write-outputs` (Windows: `py -3 -m ai_archive.playbook --write-outputs`). This validates evidence against your archive and writes two editable versions with the same core rules:

- `data/playbook.md` and `.txt`: clean instructions, without quotations, citations, source IDs or research notes. This is the default download.
- `data/playbook-with-evidence.md` and `.txt`: the same instructions with source messages and context for review.

Both versions stay local. Source-linked preview data lives in `data/customer-facing-app-playbook.json`. The export command formats and validates an agent-prepared draft; it does not call AI or infer requirements by itself.

This repository includes no personal archive, researched playbook, source-message identifiers or AI credentials. A fresh installation starts empty. Import your own local histories, then use an AI assistant of your choice to review the archive and propose requirements with message citations. Automatic AI playbook generation and in-app editing are not implemented. The playbook renderer is included; the original owner's research script and generated content are excluded.

Read-only local endpoints:

- `/api/overview`, `/api/projects/{id}/threads`, `/api/sources`
- `/api/messages?project_id=...&thread_id=...&q=...&provider=claude&from=2026-01-01&to=2026-12-31`
- `/api/messages?origin=review` for uncertain inputs
- `/api/messages?message_id=...&thread_id=...` for an exact, thread-specific citation
- `/api/export` downloads human messages as JSONL with the same filters and provenance. Add `origin=review` when explicitly exporting uncertain inputs.
- `/playbook` previews the local draft; `/playbook-clean.txt` downloads clean instructions and `/playbook-with-evidence.txt` downloads the evidence version. `/playbook.txt` remains an alias for the evidence version.

The app binds to `127.0.0.1`, validates the request host, blocks cross-site API reads, serves no database/source files, and uses no remote assets or analytics. The database and generated reports are ignored by Git; new archive files have owner-only permissions. Source messages are intentionally retained verbatim and may contain information you previously included in prompts. This local server is not an authenticated public hosting solution.

When sharing the application, share the source repository rather than a copy of your working folder. Keep `data/`, exports, raw transcripts, credentials and private research local. The ignore rules exclude these common locations and file types; explicitly adding or force-adding private files can still bypass them. No AI provider credentials are needed to collect or browse local histories. Sending archive content to a separate AI service is a decision you make outside this application.

## Hosting the static guide

Upload only the repository-root **`index.html`** to the document root for `myaiplaybook.weeyuga.com`. It contains its own CSS, illustrations, favicon and small copy/download script. No installation, backend, database, account system or visitor upload is involved. The social preview image is a public branded image served from this GitHub repository, so it does not need a second file on the shared host. The guide has no analytics or third-party scripts.

The guide's canonical and Open Graph URLs are `https://myaiplaybook.weeyuga.com/`. Change those URLs if hosting it somewhere else. HTTPS, the returned page at that domain and social-platform caching can only be verified after upload. **Do not upload `data/`, the Python application, local exports or generated playbooks.** The app's `web/index.html` is its local archive interface; it is different from the root `index.html` hosting guide.

The source repository is public; each user's history is private. A chosen cloud-backed AI agent may process messages through its provider when reading local files. The static guide does not receive archive content, and local storage alone is not a promise of offline AI analysis.

## Social previews

The initial build includes branded `web/share.png` (1200 × 630), a favicon, a canonical URL, complete Open Graph metadata, and X large-image metadata, including image descriptions. The server renders the tags directly into returned HTML. Preview artwork contains no conversation data.

Local pages use the actual local URL. A future public deployment must set `PUBLIC_ORIGIN=https://your-real-domain.example` or pass `serve --public-origin https://your-real-domain.example` so every production page/image reference is absolute HTTPS. No production domain is invented. The root page and preview image are crawlable in the routing configuration; archive APIs are disallowed in robots rules. Public crawler access, production destination/image availability, and platform caching remain deployment-dependent. This build has not uploaded or published your archive.

## Verify

```sh
python3 -m unittest discover -s tests -v
node --check web/app.js
```

Tests exercise actual parsing, synthetic-message exclusion, duplicate copies and forks, repeat imports, malformed/truncated lines, unknown timestamps, attachment sanitization, project resolution, search/filter/pagination, HTTP privacy boundaries, and server-rendered preview metadata.
