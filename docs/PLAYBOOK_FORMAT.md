# Local playbook format

An AI assistant researches the current user's archive and prepares `data/customer-facing-app-playbook.json`. The collector and exporter do not generate rules with AI. The JSON and all generated files stay private and ignored by Git.

The renderer uses one structured set of rules to produce the clean and evidence editions. Never write two independent AI drafts: their actual requirements and stable rule IDs should match. Keep research commentary in `methodology`, `decisions`, optional rule `research_note` and each source's `context_note`, since those are omitted from the clean edition. `qualification` is only a substantive scope/exception that a future agent needs and appears in both versions. `introduction` may discuss the research; optional `clean_introduction` must be standalone and source-free. `usage` appears in both editions and must also stand alone.

This deliberately synthetic example illustrates the shape. Its source identifiers must be replaced with real archive IDs; the validator will reject it as evidence:

```json
{
  "title": "My customer-facing app playbook",
  "status": "Draft 1 for review",
  "date": "2026-01-01",
  "introduction": "A draft of requirements repeated in my project conversations.",
  "clean_introduction": "Reusable requirements for the apps I build.",
  "usage": "Apply each rule when its stated scope fits the product. Resolve product-specific choices before implementation.",
  "sections": [
    {
      "id": "customer-experience",
      "title": "Customer experience",
      "rules": [
        {
          "id": "CX-01",
          "title": "Make the main journey work on mobile",
          "applies_to": "Apps used in a mobile browser",
          "requirement": "A customer must be able to complete the main task on a narrow screen.",
          "checks": ["Verify navigation, input, completion and the resulting state in a mobile browser."],
          "qualification": "Choose layouts that fit the approved design of this product.",
          "research_note": "The cited follow-up refers to checkout because that journey is identified in the preceding final reply.",
          "evidence": [
            {
              "message_id": "replace-with-the-real-64-character-message-id",
              "thread_id": "replace-with-the-real-64-character-thread-id",
              "project_label": "Example project",
              "product_key": "example-product",
              "provider": "claude",
              "sent_at": "2026-01-01T12:00:00.000000Z",
              "quote": "Please make this work on mobile too.",
              "context_note": "The preceding final reply identifies the checkout journey as the task under discussion.",
              "previous_reply_quote": "The checkout journey is ready for review."
            }
          ]
        }
      ]
    }
  ],
  "decisions": ["Decide which languages this product needs."],
  "methodology": "Describe the actual coverage, recurrence assessment, exceptions and context limitations without claiming a keyword scan was a full reading of every message.",
  "snapshot": {"messages": 100, "projects": 3, "messages_with_previous_reply": 75}
}
```

- Section and rule IDs must be unique identifiers using letters, numbers, hyphens or underscores, beginning with a letter.
- Every rule must have at least one verified source. Prefer evidence from distinct actual products when claiming recurrence. A broad user mandate can be stated explicitly without inflating its recurrence count.
- `quote` must be a nonempty, exact, contiguous excerpt of the original retained human message. Do not insert ellipses or redaction markers inside an asserted verbatim excerpt. Choose a shorter safe excerpt instead. The full human text remains available at its local citation link.
- `message_id` and `thread_id` are the stored 64-character lowercase hexadecimal IDs. The pair must exist in `thread_messages`, and the message must have `origin='human'`. A fork can have different context: use the precise thread that supports the interpretation.
- Copy `provider` and `sent_at` exactly from the API/database. Use `null` for unknown `sent_at`; never invent a time or use a file modification date.
- `product_key` is your reviewed semantic identity for the actual product, so differently named directories/worktrees for the same product do not inflate the displayed product count. `project_label` is a readable label. These counts are selected supporting examples, not an exhaustive statistical frequency measurement.
- `context_note` and `previous_reply_quote` are optional. The note is the assistant's interpretation and must be identified as such. A reply quotation, if supplied, must be an exact excerpt from `preferred_message_contexts` → `agent_replies` for this same message/thread pair. Do not quote another assistant message, substitute interim narration or turn an AI suggestion into a user request. The local evidence link opens the full captured context when available.
- `checks` and `decisions` are lists of strings; either may be empty. `qualification` and `research_note` are optional strings. Keep substantive scope/exceptions in `qualification` (both editions), and interpretation/history/recurrence caveats in `research_note` (evidence edition only). Snapshot counts are nonnegative integers from the actual human archive, not example values copied from these instructions.

Validate without writing files:

```sh
python3 -m ai_archive.playbook
```

Validate and export the two versions (use `py -3` instead of `python3` on Windows):

```sh
python3 -m ai_archive.playbook --write-outputs
```

This writes `data/playbook.md`, `data/playbook.txt`, `data/playbook-with-evidence.md` and `data/playbook-with-evidence.txt`, with owner-only permissions where the operating system supports them. The validation database is opened read-only. It checks evidence integrity, not the semantic validity of inferred preferences or the presence of secrets; the assistant still has to review both.

The default clean edition contains stable rule IDs, rule titles, applicable scope, requirements, checks and substantive qualifications. It deliberately omits original message/thread identifiers, quotations, citations, research notes, unresolved decisions and snapshot statistics. Both editions share the same rule IDs and bodies, so editing the private JSON and exporting again keeps them aligned.

For an existing hand-edited playbook, preserve the user's changes and approved decisions. Do not overwrite them with newly inferred preferences just because a fresh analysis is available. Keep draft and approval status honest.
