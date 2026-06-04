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

CREATE TABLE IF NOT EXISTS approval_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT 'normal',
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'expired', 'executed', 'failed')),
  source_session_id TEXT,
  source_message_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_status_id ON approval_requests(status, id);

CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'accepted', 'rejected', 'archived')),
  source_session_id TEXT,
  source_message_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_drafts_status_updated_at ON drafts(status, updated_at);

CREATE TABLE IF NOT EXISTS open_loops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'snoozed', 'resolved', 'archived')),
  importance REAL NOT NULL DEFAULT 0.5,
  confidence REAL NOT NULL DEFAULT 0.5,
  source_session_id TEXT,
  source_message_id INTEGER,
  suggested_next_step TEXT,
  due_at TEXT,
  last_discussed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_open_loops_status_updated_at ON open_loops(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_open_loops_source_session_id ON open_loops(source_session_id);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  priority REAL DEFAULT 0.5,
  confidence REAL DEFAULT 0.8,
  source_session_id TEXT,
  source_message_ids TEXT,
  updated_at TEXT,
  last_used_at TEXT,
  use_count INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived', 'forgotten')),
  sensitivity TEXT NOT NULL DEFAULT 'normal' CHECK(sensitivity IN ('normal', 'sensitive')),
  expires_at TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id_id ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS proactive_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER,
  proposed_text TEXT NOT NULL,
  user_response TEXT,
  outcome TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  session_id TEXT,
  task_id INTEGER,
  candidate_text TEXT,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  score REAL,
  metadata_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codex_threads (
  session_id TEXT PRIMARY KEY,
  codex_thread_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS autonomous_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'completed', 'cancelled', 'failed')),
  schedule_type TEXT NOT NULL CHECK(schedule_type IN ('once', 'interval')),
  next_run_at TEXT,
  interval_seconds INTEGER,
  timezone TEXT NOT NULL DEFAULT 'Asia/Tokyo',
  payload_json TEXT NOT NULL DEFAULT '{}',
  source TEXT,
  source_session_id TEXT,
  locked_until TEXT,
  lock_owner TEXT,
  last_run_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_autonomous_jobs_status_next_run_at
ON autonomous_jobs(status, next_run_at);

CREATE INDEX IF NOT EXISTS idx_autonomous_jobs_lock
ON autonomous_jobs(locked_until, lock_owner);

CREATE TABLE IF NOT EXISTS autonomous_job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('success', 'failure')),
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  FOREIGN KEY(job_id) REFERENCES autonomous_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomous_job_runs_job_id_id
ON autonomous_job_runs(job_id, id);

CREATE TABLE IF NOT EXISTS autonomous_notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'delivered', 'dismissed')),
  priority REAL NOT NULL DEFAULT 0.5,
  sources_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  delivered_at TEXT,
  FOREIGN KEY(job_id) REFERENCES autonomous_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomous_notifications_status_id
ON autonomous_notifications(status, id);
