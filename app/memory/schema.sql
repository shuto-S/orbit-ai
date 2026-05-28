CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  open_loops TEXT,
  decisions TEXT,
  follow_up_candidates TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'snoozed', 'cancelled')),
  priority REAL DEFAULT 0.5,
  due_at TEXT,
  source TEXT,
  source_session_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_date TEXT NOT NULL,
  summary TEXT NOT NULL,
  items_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  priority REAL DEFAULT 0.5,
  confidence REAL DEFAULT 0.8,
  last_used_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER,
  proposed_text TEXT NOT NULL,
  user_response TEXT,
  outcome TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codex_threads (
  session_id TEXT PRIMARY KEY,
  codex_thread_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
