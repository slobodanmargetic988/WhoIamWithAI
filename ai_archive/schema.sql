PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);

CREATE TABLE IF NOT EXISTS identities(
  id INTEGER PRIMARY KEY CHECK(id = 1), name TEXT NOT NULL
);
INSERT OR IGNORE INTO identities VALUES(1, 'You');
CREATE TABLE IF NOT EXISTS profiles(
  id TEXT PRIMARY KEY, identity_id INTEGER NOT NULL DEFAULT 1 REFERENCES identities,
  provider TEXT NOT NULL CHECK(provider IN ('codex','claude')),
  name TEXT NOT NULL, root_path TEXT NOT NULL, UNIQUE(provider,root_path)
);
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, canonical_path TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS project_paths(
  path TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects,
  resolution TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS threads(
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, native_id TEXT NOT NULL,
  project_id TEXT NOT NULL REFERENCES projects, title TEXT NOT NULL,
  cwd TEXT, parent_native_id TEXT, is_agent_thread INTEGER NOT NULL DEFAULT 0,
  started_at TEXT, last_message_at TEXT, UNIQUE(provider,native_id)
);
CREATE INDEX IF NOT EXISTS threads_project ON threads(project_id);
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, identity_id INTEGER NOT NULL DEFAULT 1 REFERENCES identities,
  provider TEXT NOT NULL, native_id TEXT, primary_thread_id TEXT NOT NULL REFERENCES threads,
  text TEXT NOT NULL, content_json TEXT NOT NULL, sent_at TEXT, sent_at_raw TEXT,
  origin TEXT NOT NULL CHECK(origin IN ('human','review')),
  classification_reason TEXT NOT NULL, first_imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_time ON messages(sent_at,id);
CREATE INDEX IF NOT EXISTS messages_origin ON messages(origin,provider,sent_at);
CREATE TABLE IF NOT EXISTS thread_messages(
  thread_id TEXT NOT NULL REFERENCES threads, message_id TEXT NOT NULL REFERENCES messages,
  ordinal INTEGER, PRIMARY KEY(thread_id,message_id)
);
CREATE INDEX IF NOT EXISTS thread_messages_message ON thread_messages(message_id);
CREATE TABLE IF NOT EXISTS source_files(
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
  size_bytes INTEGER NOT NULL DEFAULT 0, mtime_ns INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT, imported_at TEXT, line_count INTEGER NOT NULL DEFAULT 0,
  message_count INTEGER NOT NULL DEFAULT 0, skipped_json TEXT NOT NULL DEFAULT '{}',
  malformed_count INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS file_profiles(
  file_id TEXT NOT NULL REFERENCES source_files, profile_id TEXT NOT NULL REFERENCES profiles,
  discovered_path TEXT NOT NULL, PRIMARY KEY(file_id,profile_id)
);
CREATE TABLE IF NOT EXISTS message_sources(
  message_id TEXT NOT NULL REFERENCES messages, file_id TEXT NOT NULL REFERENCES source_files,
  line INTEGER NOT NULL, record_sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(message_id,file_id,line)
);
CREATE INDEX IF NOT EXISTS message_sources_file ON message_sources(file_id);

-- Store the original completed response, separately from human messages.
-- Text versions are distinct so partial/copied transcripts cannot overwrite
-- the exact response that another source recorded.
CREATE TABLE IF NOT EXISTS agent_replies(
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, native_id TEXT,
  native_thread_id TEXT, text TEXT NOT NULL, sent_at TEXT, sent_at_raw TEXT,
  kind TEXT NOT NULL CHECK(kind='final'), selection_reason TEXT NOT NULL,
  first_imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_reply_sources(
  reply_id TEXT NOT NULL REFERENCES agent_replies, file_id TEXT NOT NULL REFERENCES source_files,
  line INTEGER NOT NULL, record_sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(reply_id,file_id,line,source_sha256)
);
CREATE INDEX IF NOT EXISTS agent_reply_sources_file ON agent_reply_sources(file_id);
CREATE TABLE IF NOT EXISTS message_contexts(
  message_id TEXT NOT NULL REFERENCES messages, thread_id TEXT NOT NULL REFERENCES threads,
  file_id TEXT NOT NULL REFERENCES source_files, source_sha256 TEXT NOT NULL,
  reply_id TEXT REFERENCES agent_replies, status TEXT NOT NULL,
  selection_reason TEXT NOT NULL, evidence_rank INTEGER NOT NULL DEFAULT 0,
  is_current INTEGER NOT NULL DEFAULT 1, observed_at TEXT NOT NULL,
  PRIMARY KEY(message_id,thread_id,file_id,source_sha256)
);
CREATE INDEX IF NOT EXISTS message_contexts_reply ON message_contexts(reply_id);
CREATE INDEX IF NOT EXISTS message_contexts_file ON message_contexts(file_id);
CREATE VIEW IF NOT EXISTS preferred_message_contexts AS
 SELECT message_id,thread_id,file_id,reply_id,status,selection_reason,is_current,source_sha256
 FROM (
   SELECT *,ROW_NUMBER() OVER (
     PARTITION BY message_id,thread_id
     ORDER BY (reply_id IS NOT NULL) DESC,evidence_rank DESC,is_current DESC,observed_at DESC,file_id,source_sha256
   ) AS preference
   FROM message_contexts
 ) WHERE preference=1;
CREATE TABLE IF NOT EXISTS import_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS import_issues(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES import_runs,
  file_id TEXT REFERENCES source_files, line INTEGER, kind TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  text, content='messages', content_rowid='rowid', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF text ON messages BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text);
  INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text);
END;
