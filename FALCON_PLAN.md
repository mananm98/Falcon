# Falcon: Open-Source DeepWiki Alternative — Implementation Plan

## Context

Build an open-source alternative to DeepWiki that generates interactive wiki documentation for any public GitHub repository. Unlike existing alternatives (deepwiki-open, OpenDeepWiki) that use RAG/embeddings, Falcon uses **OpenAI Codex CLI** as an actual agent that reads code and writes documentation — producing higher quality, semantically aware docs.

**Key decisions:**
- Agent: OpenAI Codex CLI (`codex exec` non-interactive mode)
- Sandbox: Daytona (production), local tmpdir (dev)
- Backend: Python + FastAPI
- Storage: Filesystem (markdown) + SQLite (metadata)
- Scope: Public GitHub repos only (MVP)

---

## 1. Wiki Structure: Single Structure, Dual Purpose

**One set of markdown files + one `manifest.json`** — no separate human/agent formats.

- Each markdown page has **YAML frontmatter** with metadata (source files, dependencies, key exports, module type)
- **`manifest.json`** at wiki root acts as the agent's navigation index: maps topics → files, tracks module relationships, provides a source file → wiki page lookup
- Agents read `manifest.json` first to find relevant pages, then read those pages for context
- Humans browse the same markdown rendered as HTML

### Wiki Directory Layout
```
wiki_storage/{owner}/{repo}/{wiki_id}/
  manifest.json          # machine-readable index + dependency graph
  _overview.md           # landing page
  architecture/
    _index.md            # section overview
    system-design.md
    data-flow.md
  modules/
    _index.md
    auth.md
    api-layer.md
    database.md
  guides/
    _index.md
    getting-started.md
    configuration.md
  api-reference/
    _index.md
    endpoints.md
    models.md
  AGENTS.md              # instructions for Q&A agent sessions
```

### Frontmatter Schema (per markdown page)
```yaml
---
title: "Authentication Module"
slug: "modules/auth"
section: "modules"
order: 1
source_files: ["src/auth/login.py", "src/auth/oauth.py"]
source_dirs: ["src/auth/"]
depends_on: ["modules/database"]
depended_by: ["guides/getting-started"]
key_exports: ["LoginHandler", "OAuthProvider", "auth_middleware"]
module_type: "library"  # library | service | config | test | docs | script
languages: ["python"]
complexity: "medium"    # low | medium | high
generated_at: "2026-02-15T10:30:00Z"
---
```

### manifest.json Schema

```json
{
  "version": "1.0",
  "repository": {
    "owner": "fastapi",
    "name": "fastapi",
    "url": "https://github.com/fastapi/fastapi",
    "default_branch": "main",
    "commit_sha": "abc123def",
    "languages": {"Python": 85.2, "Shell": 10.1, "Dockerfile": 4.7},
    "description": "FastAPI framework, high performance..."
  },
  "generated_at": "2026-02-15T10:30:00Z",
  "falcon_version": "0.1.0",

  "sections": [
    {
      "id": "architecture",
      "title": "Architecture",
      "order": 1,
      "description": "System design and high-level architecture"
    }
  ],

  "pages": [
    {
      "slug": "modules/auth",
      "title": "Authentication Module",
      "section": "modules",
      "order": 1,
      "file_path": "modules/auth.md",
      "summary": "Covers login, OAuth, and auth middleware",
      "source_files": ["src/auth/login.py", "src/auth/oauth.py"],
      "source_dirs": ["src/auth/"],
      "key_exports": ["LoginHandler", "OAuthProvider"],
      "module_type": "library",
      "depends_on": ["modules/database"],
      "depended_by": ["guides/getting-started"]
    }
  ],

  "source_index": {
    "src/auth/login.py": ["modules/auth"],
    "src/auth/oauth.py": ["modules/auth"],
    "src/models/user.py": ["modules/database", "modules/auth"],
    "src/main.py": ["architecture/system-design", "modules/api-layer"]
  },

  "graph": {
    "nodes": [
      {"id": "modules/auth", "label": "Auth", "type": "library"},
      {"id": "modules/database", "label": "Database", "type": "library"}
    ],
    "edges": [
      {"from": "modules/auth", "to": "modules/database", "type": "depends_on"}
    ]
  },

  "stats": {
    "total_pages": 12,
    "total_source_files_covered": 47,
    "total_source_files_in_repo": 63,
    "coverage_percent": 74.6
  }
}
```

The `source_index` is the critical lookup for Q&A: given a source file path, find which wiki pages cover it. The `graph` powers the frontend's interactive dependency visualization.

---

## 2. System Architecture

```
                              +-----------------+
                              |    Frontend     |
                              |   (Next.js)     |
                              +-------+---------+
                                      |
                                 HTTP / SSE
                                      |
                        +-------------v--------------+
                        |      FastAPI Backend        |
                        |                             |
                        |  Wiki API    Q&A API        |
                        |      |          |           |
                        |  Job Orchestrator           |
                        +------+----------+-----------+
                               |          |
                    +----------+    +-----+--------+
                    |               |              |
              +-----v-----+  +-----v------+  +----v-------+
              | SQLite DB  |  |Wiki Storage|  |  Sandbox   |
              | (metadata) |  |(filesystem)|  | (Daytona)  |
              +------------+  +------------+  +----+-------+
                                                   |
                                              +----v-------+
                                              | Codex CLI  |
                                              +------------+
```

### Data Flow: GitHub URL → Wiki Served

**Phase 0 — Request Intake:**
1. Frontend POSTs `{ github_url }` to `/api/wikis`
2. Backend validates URL, checks SQLite for existing wiki at same commit SHA
3. If fresh cache exists → return immediately
4. Otherwise → create wiki record (status: `queued`), enqueue job, return `wiki_id`

**Phase 1 — Repository Acquisition:**
1. Job worker creates sandbox (Daytona or local tmpdir)
2. `git clone --depth=1` into sandbox
3. Fetch repo metadata via GitHub API
4. Status: `cloning` → `analyzing`

**Phase 2 — Repo Analysis (Codex Pass 1: "Understand"):**
1. Write analysis `AGENTS.md` into sandbox
2. `codex exec --json --full-auto` with analysis prompt
3. Codex reads codebase, outputs structured JSON: modules, boundaries, dependencies, recommended wiki structure
4. Store analysis plan in SQLite
5. Status: `analyzing` → `generating`

**Phase 3 — Wiki Generation (Codex Pass 2..N: "Write"):**
1. For each planned page, invoke Codex with focused prompt + relevant source files + analysis context
2. Generate in dependency waves:
   - Wave 1: `_overview.md` + `architecture/*` (provides context for everything)
   - Wave 2: `modules/*` (parallelized, up to 3 concurrent Codex calls)
   - Wave 3: `guides/*` + `api-reference/*`
3. Each page completion updates progress in SQLite + broadcasts SSE event
4. Status: `generating (5/12 pages)`

**Phase 4 — Manifest Generation (Codex Final Pass: "Index"):**
1. Codex reads all generated markdown, produces `manifest.json` with full index + source mapping + dependency graph
2. Also generates Q&A `AGENTS.md`

**Phase 5 — Storage & Completion:**
1. Copy files from sandbox to persistent storage: `wiki_storage/{owner}/{repo}/{wiki_id}/`
2. Populate SQLite `wiki_pages` + `source_file_index` tables
3. Destroy sandbox
4. Status: `completed`, SSE completion event

---

## 3. Codex CLI Integration

### Invocation Wrapper

Call Codex via `asyncio.create_subprocess_exec`, parse JSON Lines stream in real-time for progress tracking.

```python
async def run_codex(
    working_dir: str,
    prompt: str,
    output_schema_path: str | None = None,
    timeout_seconds: int = 300,
) -> CodexResult:
    cmd = [
        "codex", "exec",
        "--json",
        "--full-auto",
        "--sandbox", "workspace-write",
    ]
    if output_schema_path:
        cmd.extend(["--output-schema", output_schema_path])
    cmd.append(prompt)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=working_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CODEX_API_KEY": settings.codex_api_key},
    )

    events = []
    async for line in process.stdout:
        event = json.loads(line)
        events.append(event)
        await broadcast_progress(event)

    await process.wait()
    return CodexResult(
        exit_code=process.returncode,
        events=events,
        output=extract_final_message(events),
    )
```

### AGENTS.md Templates

**Analysis AGENTS.md (Phase 2):**
```markdown
# Falcon Repository Analyzer

You are analyzing a GitHub repository to plan wiki documentation.

## Your Task
Analyze the codebase structure and produce a documentation plan.

## Rules
- Read directory structure first, then key files (README, package.json, setup.py, etc.)
- Identify logical modules/packages and their boundaries
- Map dependencies between modules
- Identify entry points, configuration, and public APIs
- Do NOT write documentation yet — only produce the analysis

## Output Format
Produce a JSON object with:
- `modules`: array of {name, directory, purpose, key_files, depends_on}
- `sections`: recommended wiki sections
- `pages`: recommended pages per section with source file mappings
- `entry_points`: main entry files
- `config_files`: configuration files found
```

**Writing AGENTS.md (Phase 3):**
```markdown
# Falcon Wiki Writer

You are writing a wiki documentation page for a GitHub repository.

## Rules
- Write clear, technical documentation aimed at developers
- Include code examples from the actual source (quote real code, do not invent)
- Use Mermaid diagrams for architecture and data flow where helpful
- Begin the file with YAML frontmatter matching the provided schema
- Explain WHY things are designed the way they are, not just WHAT they do
- Reference other wiki pages by slug when relevant
- Keep pages focused: one module or concept per page
- Target 500-1500 words per page
```

**Q&A AGENTS.md:**
```markdown
# Falcon Q&A Agent

You are answering questions about a GitHub repository.
You have access to pre-generated wiki documentation and the original source code.

## Rules
1. ALWAYS check the wiki pages provided in context FIRST
2. If the wiki pages answer the question, use that information and cite the wiki page
3. If you need more detail, read the relevant source files directly
4. Always cite your sources: wiki page slugs and/or source file paths
5. Use code snippets from the actual source when explaining implementation details
6. If you genuinely cannot find the answer, say so honestly
```

### Parallel Generation

Pages generated in waves with `asyncio.Semaphore(3)` for max 3 concurrent Codex calls within a wave.

---

## 4. API Design (Frontend Contracts)

### Wiki Generation & Retrieval
| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/wikis` | Submit GitHub URL, returns `wiki_id` |
| `GET` | `/api/wikis/{wiki_id}` | Get wiki metadata + status |
| `GET` | `/api/wikis/{wiki_id}/manifest` | Get full manifest.json |
| `GET` | `/api/wikis/{wiki_id}/pages` | List all pages (slug, title, section) |
| `GET` | `/api/wikis/{wiki_id}/pages/{slug:path}` | Get single page content + frontmatter |
| `DELETE` | `/api/wikis/{wiki_id}` | Delete wiki |
| `GET` | `/api/wikis?owner={o}&repo={r}` | Find wikis for a repo |

### Progress & Events
| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/wikis/{wiki_id}/status` | Current status + progress (completed/total pages) |
| `GET` | `/api/wikis/{wiki_id}/events` | **SSE stream** — status changes, page completions, errors |

### Q&A Chat
| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/wikis/{wiki_id}/chat` | Send message, get SSE streamed response |
| `GET` | `/api/wikis/{wiki_id}/chat/{conversation_id}` | Get conversation history |

### Health
| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/health` | Health check + active jobs count |

---

## 5. SQLite Schema

```sql
-- Core wiki metadata
CREATE TABLE wikis (
    id              TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    repo            TEXT NOT NULL,
    github_url      TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT 'main',
    commit_sha      TEXT,
    repo_description TEXT,
    repo_languages  TEXT,              -- JSON: {"Python": 85.2, ...}
    status          TEXT NOT NULL DEFAULT 'queued',
    -- statuses: queued, cloning, analyzing, generating, indexing, completed, failed
    analysis_plan   TEXT,              -- JSON: Phase 2 output
    total_pages     INTEGER DEFAULT 0,
    completed_pages INTEGER DEFAULT 0,
    error_message   TEXT,
    storage_path    TEXT,              -- relative to WIKI_STORAGE_ROOT
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner, repo, branch, commit_sha)
);

CREATE INDEX idx_wikis_owner_repo ON wikis(owner, repo);
CREATE INDEX idx_wikis_status ON wikis(status);

-- Wiki pages (denormalized from filesystem for fast lookups)
CREATE TABLE wiki_pages (
    id              TEXT PRIMARY KEY,
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL,
    title           TEXT NOT NULL,
    section         TEXT NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    source_files    TEXT,              -- JSON array
    source_dirs     TEXT,              -- JSON array
    key_exports     TEXT,              -- JSON array
    module_type     TEXT,
    summary         TEXT,
    file_path       TEXT NOT NULL,
    content_hash    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(wiki_id, slug)
);

CREATE INDEX idx_pages_wiki_id ON wiki_pages(wiki_id);

-- Source file → wiki page mapping (for Q&A lookups)
CREATE TABLE source_file_index (
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    source_path     TEXT NOT NULL,
    page_slug       TEXT NOT NULL,
    PRIMARY KEY (wiki_id, source_path, page_slug)
);

CREATE INDEX idx_source_wiki ON source_file_index(wiki_id, source_path);

-- Q&A conversations
CREATE TABLE conversations (
    id              TEXT PRIMARY KEY,
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Chat messages
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,     -- 'user' or 'assistant'
    content         TEXT NOT NULL,
    context_pages   TEXT,              -- JSON: slugs used as context
    model_used      TEXT,
    tokens_used     INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_messages_conv ON messages(conversation_id);

-- Job queue (SQLite-backed)
CREATE TABLE jobs (
    id              TEXT PRIMARY KEY,
    job_type        TEXT NOT NULL,     -- 'wiki_generation', 'wiki_update'
    wiki_id         TEXT NOT NULL REFERENCES wikis(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'queued',
    -- statuses: queued, running, completed, failed, cancelled
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

CREATE INDEX idx_jobs_status ON jobs(status, priority DESC);
```

---

## 6. Job Queue System

SQLite-backed async job queue (no Redis/Celery for MVP):

- **`JobOrchestrator`** runs as FastAPI background task on startup
- Polls `jobs` table, atomically claims next `queued` job
- `asyncio.Semaphore` controls max concurrent wiki generations (default: 2)
- **Crash recovery**: on startup, reset any `running` jobs back to `queued`
- **Retry**: 3 attempts per job with backoff
- **Partial success**: individual page failures don't fail the whole job — manifest reflects coverage gaps
- **`EventBus`**: in-memory asyncio pub/sub broadcasts real-time events to SSE subscribers

---

## 7. Q&A System

**No embeddings/RAG.** Uses manifest-based context selection + Codex:

### Flow
1. **Context Selection** (fast, no LLM): keyword/term matching against manifest page titles, summaries, key_exports, source file names. Score and rank, select top 3-5 pages.
2. **Context Assembly**: read selected markdown pages, concatenate as context block (trimmed to token budget)
3. **Codex Invocation**: `codex exec` in sandbox (which has cloned repo) with wiki context + conversation history + question
4. **Response**: streamed via SSE with source attribution

### Context Selection Algorithm
```python
def select_context_pages(manifest, question, max_pages=5):
    question_terms = tokenize_and_stem(question)
    scored = []
    for page in manifest["pages"]:
        score = 0.0
        score += 3.0 * jaccard_similarity(question_terms, tokenize(page["title"]))
        score += 2.0 * jaccard_similarity(question_terms, tokenize(page["summary"]))
        for export in page.get("key_exports", []):
            if export.lower() in question.lower():
                score += 5.0
        for f in page.get("source_files", []):
            filename = f.split("/")[-1].replace("_", " ").replace(".py", "")
            if any(term in filename.lower() for term in question_terms):
                score += 2.0
        scored.append((page["slug"], score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [slug for slug, score in scored[:max_pages] if score > 0]
```

### Sandbox Reuse for Q&A
- **Daytona**: keep workspace stopped, resume on Q&A request, auto-stop after 15 min idle
- **Fallback**: re-clone to tmpdir, cache for 30 min

---

## 8. Sandbox Manager

Abstract over Daytona (prod) and local tmpdir (dev):

```python
class SandboxManager:
    async def create_sandbox(self, github_url: str, branch: str = "main") -> Sandbox:
        if settings.use_daytona:
            return await self._create_daytona_sandbox(github_url, branch)
        else:
            return await self._create_local_sandbox(github_url, branch)
```

- **Daytona**: Python SDK, sets `CODEX_API_KEY` env var, 30 min auto-stop
- **Local**: `tempfile.mkdtemp()` + `git clone --depth=1`

---

## 9. Project File Layout

```
falcon/
  backend/
    app/
      __init__.py
      main.py              # FastAPI app, lifespan, middleware
      config.py            # pydantic-settings
      database.py          # SQLite connection + migrations
      models.py            # Pydantic request/response schemas
      routers/
        wikis.py           # /api/wikis endpoints
        chat.py            # /api/wikis/{id}/chat endpoints
        health.py          # /api/health
      services/
        wiki_service.py    # Create/get/delete wiki business logic
        chat_service.py    # Context selection + Codex Q&A
        github_service.py  # GitHub API client (metadata, validation)
      pipeline/
        orchestrator.py    # WikiGenerationPipeline (phases 1-5)
        analyzer.py        # Phase 2: repo analysis via Codex
        writer.py          # Phase 3: page generation via Codex
        indexer.py         # Phase 4: manifest generation
        prompts.py         # All prompt templates
        agents_md.py       # AGENTS.md content generators
      sandbox/
        manager.py         # SandboxManager (Daytona + local)
        codex.py           # Codex CLI wrapper
      queue/
        job_queue.py       # JobOrchestrator
        event_bus.py       # In-memory pub/sub for SSE
      db/
        migrations/
          001_initial.sql
    schemas/               # JSON schemas for Codex --output-schema
      analysis_plan.json
      wiki_page.json
      manifest.json
    agents/                # AGENTS.md templates
      analysis.md
      writing.md
      qa.md
    tests/
    pyproject.toml
  wiki_storage/            # Generated wikis (gitignored)
```

---

## 10. Implementation Order

1. **Project scaffolding** — FastAPI app, config, SQLite setup, migrations
2. **Sandbox manager** — local tmpdir first, Daytona later
3. **Codex CLI wrapper** — `run_codex()` function with JSON Lines parsing
4. **Pipeline Phase 1-2** — clone + analysis (get structured repo understanding)
5. **Pipeline Phase 3** — page generation with AGENTS.md templates
6. **Pipeline Phase 4-5** — manifest generation + storage
7. **Job queue** — async orchestration with progress tracking
8. **Wiki API endpoints** — CRUD + SSE progress
9. **Q&A system** — context selection + chat endpoints
10. **Daytona integration** — swap local tmpdir for Daytona sandboxes

---

## Verification Plan

1. **Unit tests**: Codex wrapper (mock subprocess), context selection algorithm, manifest parsing
2. **Integration test**: Submit a small public repo (e.g., `kelseyhightower/nocode`), verify wiki generates end-to-end
3. **API test**: Hit all endpoints via httpx/pytest, verify SSE streaming works
4. **Q&A test**: Generate wiki for a known repo, ask questions, verify answers cite correct sources
5. **Manual test**: Run full backend, submit a medium-sized repo via curl, monitor SSE progress, browse generated wiki files
