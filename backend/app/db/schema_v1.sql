-- pgassistant schema v1（对应需求文档 §5 / §5.3）
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS resources (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  slug          TEXT NOT NULL UNIQUE,
  title         TEXT NOT NULL,
  summary       TEXT NOT NULL DEFAULT '',
  body_md       TEXT NOT NULL,
  body_tokens   TEXT NOT NULL DEFAULT '',
  stack         TEXT NOT NULL,
  category      TEXT NOT NULL,
  level         TEXT NOT NULL CHECK (level IN ('beginner','intermediate','advanced')),
  type          TEXT NOT NULL CHECK (type IN
                  ('article','video','course','book','docs','exercise','summary','overview')),
  duration_min  INTEGER,
  url           TEXT,
  cover_path    TEXT,
  source        TEXT NOT NULL DEFAULT 'repo',
  file_path     TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  author        TEXT NOT NULL DEFAULT '',
  license       TEXT NOT NULL DEFAULT '',
  source_url    TEXT,
  runnable      TEXT,
  lang          TEXT NOT NULL DEFAULT 'zh',
  is_deleted    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resources_stack   ON resources(stack, category);
CREATE INDEX IF NOT EXISTS idx_resources_level   ON resources(level);
CREATE INDEX IF NOT EXISTS idx_resources_type    ON resources(type);
CREATE INDEX IF NOT EXISTS idx_resources_deleted ON resources(is_deleted);

CREATE TABLE IF NOT EXISTS resource_tags (
  resource_id INTEGER NOT NULL,
  tag         TEXT NOT NULL,
  UNIQUE (resource_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON resource_tags(tag);

-- 独立 FTS5 表：rowid 对齐 resources.id，内容为 jieba 切好的空格串
CREATE VIRTUAL TABLE IF NOT EXISTS resources_fts USING fts5(
  title_tokens,
  summary_tokens,
  body_tokens,
  tags_tokens,
  tokenize = "unicode61 remove_diacritics 0"
);

CREATE TABLE IF NOT EXISTS resource_aliases (
  old_slug   TEXT PRIMARY KEY,
  new_slug   TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
  resource_id INTEGER PRIMARY KEY,
  model       TEXT NOT NULL,
  dim         INTEGER NOT NULL,
  vec         BLOB NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT NOT NULL,
  goal          TEXT NOT NULL DEFAULT '',
  start_level   TEXT NOT NULL DEFAULT 'beginner',
  weekly_hours  INTEGER NOT NULL DEFAULT 10,
  horizon_weeks INTEGER NOT NULL DEFAULT 12,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  origin        TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('agent','manual','template')),
  version       INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_stages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id     INTEGER NOT NULL REFERENCES plans(id),
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  weeks       INTEGER NOT NULL DEFAULT 1,
  order_index INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_stages_plan ON plan_stages(plan_id, order_index);

CREATE TABLE IF NOT EXISTS plan_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_id      INTEGER NOT NULL REFERENCES plan_stages(id),
  title         TEXT NOT NULL,
  resource_slug TEXT,
  note          TEXT NOT NULL DEFAULT '',
  est_minutes   INTEGER NOT NULL DEFAULT 30,
  order_index   INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','done','skipped')),
  depends_on    TEXT,
  completed_at  TEXT,
  version       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_items_stage ON plan_items(stage_id, order_index);
CREATE INDEX IF NOT EXISTS idx_items_slug  ON plan_items(resource_slug);

CREATE TABLE IF NOT EXISTS chat_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  role       TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
  content    TEXT NOT NULL,
  intent     TEXT,
  model      TEXT,
  tokens_in  INTEGER,
  tokens_out INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_files (
  path       TEXT PRIMARY KEY,
  mtime      INTEGER NOT NULL,
  size       INTEGER NOT NULL,
  hash       TEXT NOT NULL,
  resource_slug TEXT
);

CREATE TABLE IF NOT EXISTS index_state (
  id               INTEGER PRIMARY KEY CHECK (id = 1),
  content_version  TEXT NOT NULL,
  last_indexed_at  TEXT NOT NULL,
  file_count       INTEGER NOT NULL,
  model_fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS search_misses (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  query      TEXT NOT NULL,
  filters    TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS link_check_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_id INTEGER NOT NULL,
  url         TEXT NOT NULL,
  status_code INTEGER,
  checked_at  TEXT NOT NULL,
  ok          INTEGER NOT NULL DEFAULT 0
);
