-- Core wiki metadata
CREATE TABLE IF NOT EXISTS wikis (
    id              TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    repo            TEXT NOT NULL,
    github_url      TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT 'main',
    commit_sha      TEXT,
    repo_description TEXT,
    repo_languages  TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    analysis_plan   TEXT,
    total_pages     INTEGER DEFAULT 0,
    completed_pages INTEGER DEFAULT 0,
    error_message   TEXT,
    storage_path    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner, repo, branch, commit_sha)
);

CREATE INDEX IF NOT EXISTS idx_wikis_owner_repo ON wikis(owner, repo);
CREATE INDEX IF NOT EXISTS idx_wikis_status ON wikis(status);

-- Wiki pages (denormalized from filesystem)
CREATE TABLE IF NOT EXISTS wiki_pages (
    id              TEXT PRIMARY KEY,
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL,
    title           TEXT NOT NULL,
    section         TEXT NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    source_files    TEXT,
    source_dirs     TEXT,
    key_exports     TEXT,
    module_type     TEXT,
    summary         TEXT,
    file_path       TEXT NOT NULL,
    content_hash    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(wiki_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_pages_wiki_id ON wiki_pages(wiki_id);

-- Source file to wiki page mapping
CREATE TABLE IF NOT EXISTS source_file_index (
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    source_path     TEXT NOT NULL,
    page_slug       TEXT NOT NULL,
    PRIMARY KEY (wiki_id, source_path, page_slug)
);

CREATE INDEX IF NOT EXISTS idx_source_wiki ON source_file_index(wiki_id, source_path);

-- Q&A conversations
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Chat messages
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    context_pages   TEXT,
    model_used      TEXT,
    tokens_used     INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

-- Job queue
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    job_type        TEXT NOT NULL,
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'queued',
    priority        INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    error_message   TEXT,
    worker_id       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT,
    UNIQUE(wiki_id, job_type)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority DESC);
