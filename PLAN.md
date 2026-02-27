# Falcon — Architecture Plan

Open-source alternative to DeepWiki / GitSummarize / Zread.ai.

## Features

1. **Repo Documentation** — Input a repo URL, generate structured docs
2. **Chat with Repo** — Agentic search (like Claude Code) to answer questions about any codebase

---

## Tech Stack

| Layer | Choice |
|-------|--------|
| Frontend | Next.js (App Router) |
| Backend | Python FastAPI |
| LLM | OpenAI GPT (function calling) |
| Database | PostgreSQL + pg_trgm |
| Repo Ingestion | `git clone --depth 1` (ephemeral — clone, index, delete) |
| Streaming | Server-Sent Events (SSE) |

---

## Architecture

```
┌─────────────────┐        ┌──────────────────────────┐        ┌──────────┐
│   Next.js App   │───────▶│    FastAPI Backend        │───────▶│  OpenAI  │
│   (Frontend)    │◀──SSE──│    (AGENT RUNTIME)        │◀───────│   API    │
└─────────────────┘        │                            │        └──────────┘
                           │  When OpenAI returns a     │
                           │  tool call like             │
                           │  read_file("src/auth.py"),  │
                           │  the BACKEND executes it    │
                           │  as a DB query and sends    │
                           │  the result back to OpenAI. │
                           │                            │
                           │  ┌────────────────┐        │
                           │  │  PostgreSQL     │        │
                           │  │  (repo files    │        │
                           │  │   indexed here) │        │
                           │  └────────────────┘        │
                           └──────────────────────────┘
```

No Redis, no queues, no vector DB. Repos are cloned, indexed into Postgres, and deleted.

---

## Core Idea: Virtual Shell via Database

Instead of keeping cloned repos on disk, we **ingest file contents into PostgreSQL** and mock shell commands through SQL queries. To the LLM agent, it feels like executing `ls`, `cat`, `rg` — but every call is a database query.

### Why?

- **No disk storage** — hundreds of repos, zero disk footprint after ingestion
- **Instant access** — no re-cloning, files are always queryable
- **Universal** — works with GitHub, BitBucket, GitLab, any git host

---

## Database Schema

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE repos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    ingested_at TIMESTAMP DEFAULT now(),
    status TEXT DEFAULT 'pending'  -- pending | ingesting | ready | error
);

CREATE TABLE files (
    id BIGSERIAL PRIMARY KEY,
    repo_id UUID NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path TEXT NOT NULL,             -- 'src/auth/login.py'
    name TEXT NOT NULL,             -- 'login.py'
    extension TEXT,                 -- '.py'
    parent_path TEXT NOT NULL,      -- 'src/auth' ('' for root)
    depth INTEGER NOT NULL,         -- 3 for 'src/auth/login.py'
    is_directory BOOLEAN DEFAULT FALSE,
    content TEXT,                   -- NULL for directories
    UNIQUE(repo_id, path)
);
```

### Every field has a purpose

| Field | Exists for tool | Query pattern |
|-------|----------------|---------------|
| `repo_id` | All tools | `WHERE repo_id = $1` (scopes every query) |
| `path` | read_file, list_files (glob), search_code | `WHERE path = $1`, glob matching |
| `name` | list_files (find mode) | `WHERE name LIKE '%.py'` |
| `extension` | search_code (--glob) | `WHERE extension = '.py'` (equality, fast) |
| `parent_path` | list_files (ls mode) | `WHERE parent_path = 'src/auth'` |
| `depth` | list_files (maxdepth) | `WHERE depth <= 2` |
| `is_directory` | list_files (ls), search_code | Sort dirs first, skip dirs in search |
| `content` | read_file, search_code | File content for reading and searching |

### Indexes (one per query pattern)

```sql
CREATE INDEX idx_dir_listing    ON files(repo_id, parent_path);           -- ls
CREATE INDEX idx_file_name      ON files(repo_id, name);                  -- find -name
CREATE INDEX idx_file_ext       ON files(repo_id, extension);             -- rg -g "*.py"
CREATE INDEX idx_content_search ON files USING gin(content gin_trgm_ops); -- rg (content)
CREATE INDEX idx_path_search    ON files USING gin(path gin_trgm_ops);    -- rg --files
```

---

## 3 Virtual Shell Tools

8 shell commands clubbed into 3 tools by capability:

### Tool 1: `list_files(path)` — "What files exist?"

**Clubs**: `ls`, `find`, `rg --files`

Single param `path`. Glob characters (`*`, `?`) switch behavior:

```
list_files("")                    → ls /           (repo root)
list_files("src/auth")            → ls src/auth/   (one directory)
list_files("**/*.py")             → find all .py   (recursive glob)
list_files("src/**/*.test.js")    → find test files under src/
```

- Directory mode: indexed query on `parent_path`
- Glob mode: fetch all paths (~3K strings), `fnmatch` in Python

### Tool 2: `read_file(path, start_line?, end_line?)` — "Show me content"

**Clubs**: `cat`, `head`, `tail`, `sed -n`

All four fetch content from one file and slice lines:

```
read_file("auth.py")                             → cat (all lines)
read_file("auth.py", end_line=20)                → head -n 20
read_file("auth.py", start_line=-10)             → tail -n 10
read_file("auth.py", start_line=50, end_line=70) → sed -n '50,70p'
```

Output includes line numbers so the LLM can reference them in follow-up calls.

### Tool 3: `search_code(pattern, glob?)` — "Where is this pattern?"

**Is**: `rg` (ripgrep)

Uses a **hybrid approach** — pg_trgm speed + Python regex compatibility:

1. **Extract literals** from regex: `"def\s+auth"` → `["def", "auth"]`
2. **pg_trgm LIKE** to narrow files: `WHERE content LIKE '%def%' AND content LIKE '%auth%'`
   (narrows 3,000 files → ~5 candidates, index-accelerated)
3. **Python `re.search()`** on candidates for precise line matching
   (full PCRE: `\w`, `\d`, `\s`, lookaheads — everything works)

**Why hybrid?** PostgreSQL uses POSIX regex (no `\w`, `\d`, `\s`). The LLM will naturally generate PCRE patterns. They'd break if sent directly to PG.

---

## Agent Loop (ReAct)

The LLM decides which tools to call. The backend executes them and feeds results back:

```
User: "How does authentication work?"
  │
  ▼
LLM thinks → calls list_files("")
  │           → backend runs SQL → returns directory listing
  ▼
LLM thinks → calls search_code("auth", "*.py")
  │           → backend runs hybrid search → returns matches
  ▼
LLM thinks → calls read_file("src/auth/login.py")
  │           → backend runs SQL → returns file content
  ▼
LLM answers → "The authentication system works by..."
               (streamed to client via SSE)
```

- **Always streaming** — text flows to client as generated, no blank screen
- **Tool calls accumulated** — OpenAI sends arguments in chunks, buffered until complete
- **15 iteration safety cap** — prevents runaway loops
- **Parallel tool calls** — if OpenAI returns 2 tools at once, both execute

---

## Ingestion Pipeline

```
URL → git clone --depth 1 → walk file tree → filter junk → batch INSERT → delete clone
```

1. Dedup check (skip if URL already indexed)
2. Shallow clone into Python tempdir
3. `os.walk()` with in-place directory filtering (prevents descent into `node_modules/` etc.)
4. Skip: `.git/`, `node_modules/`, binaries, lock files, images, files > 500KB
5. Read each file as UTF-8 (skip binary files that fail to decode)
6. Compute derived fields: `name`, `extension`, `parent_path`, `depth`
7. Batch insert via `asyncpg.copy_records_to_table()` (COPY protocol, very fast)
8. Delete clone (tempdir auto-cleanup)

After ingestion: **zero disk footprint**. All data lives in PostgreSQL.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST /repos` | Ingest a new repo `{url}` |
| `GET /repos` | List all ingested repos |
| `GET /repos/{id}` | Get repo details + file count |
| `DELETE /repos/{id}` | Delete repo and all files (CASCADE) |
| `POST /repos/{id}/chat` | Chat with repo (SSE stream) |
| `GET /health` | Health check |

---

## Project Structure

```
backend/
├── main.py                    # FastAPI app, lifespan, CORS
├── config.py                  # env-based settings (DATABASE_URL, etc.)
├── db.py                      # asyncpg pool + schema init
├── routers/
│   └── repos.py               # 5 endpoints
├── services/
│   ├── agent.py               # ReAct loop (async generator → SSE events)
│   └── ingestion.py           # clone → walk → index → cleanup
└── tools/
    ├── definitions.py          # OpenAI function-calling schemas + system prompt
    └── shell.py                # 3 virtual shell tools (list_files, read_file, search_code)
```

---

## Running Locally

```bash
# Prerequisites: PostgreSQL running on localhost:5432

# Create the database
createdb falcon

# Install dependencies
pip install -r requirements.txt

# Set OpenAI API key
export OPENAI_API_KEY=sk-...

# Start the server
uvicorn backend.main:app --reload

# Ingest a repo
curl -X POST http://localhost:8000/repos \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/expressjs/express"}'

# Chat with the repo
curl -N -X POST http://localhost:8000/repos/{repo_id}/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What does this project do?"}'
```

---

## TODO

- [ ] Frontend (Next.js) — repo input, doc viewer, chat UI
- [ ] Doc generation pipeline (Feature 1)
- [ ] Re-ingestion support (update existing repo)
- [ ] Private repo support (SSH keys / tokens)
- [ ] Rate limiting
