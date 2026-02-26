"""
Virtual shell tools backed by PostgreSQL.

3 tools that replicate 8 shell commands:

  list_files   →  ls, find, rg --files    ("What files exist?")
  read_file    →  cat, head, tail, sed -n  ("Show me file content")
  search_code  →  rg                       ("Where is this pattern?")

The LLM agent calls these as if running shell commands.
Under the hood, every call is a database query against ingested repo files.
"""

import re
import fnmatch
import asyncpg
from typing import Optional


# Output caps — prevent flooding the LLM's context window
MAX_LIST_RESULTS = 200
MAX_FILE_LINES = 500
MAX_SEARCH_MATCHES = 50


# ---------------------------------------------------------------------------
# Tool 1: list_files
#
# Clubs: ls, find, rg --files
#
# Single param `path` — glob characters switch the behavior:
#
#   list_files("")                  → ls /           (repo root)
#   list_files("src/auth")          → ls src/auth/   (one directory level)
#   list_files("**/*.py")           → find -name "*.py"  (recursive glob)
#   list_files("src/**/*.test.js")  → find src/ -name "*.test.js"
#
# Directory mode: indexed query on parent_path
# Glob mode:      fetch all paths (~3K strings), fnmatch in Python
# ---------------------------------------------------------------------------
async def list_files(
    conn: asyncpg.Connection,
    repo_id: str,
    path: str = "",
) -> str:

    path = path.strip("/")
    if path == ".":
        path = ""

    is_glob = "*" in path or "?" in path

    if is_glob:
        # ----------------------------------------------------------
        # Glob mode: fetch all paths for this repo, filter in Python.
        #
        # ~3K path strings ≈ 100KB — trivial to transfer and filter.
        # fnmatch handles **, *, ? natively.
        #
        # Query:  SELECT path, is_directory FROM files
        #         WHERE repo_id = $1
        #         ORDER BY path
        #
        # Index:  idx_dir_listing(repo_id, parent_path) — repo_id prefix
        # ----------------------------------------------------------
        rows = await conn.fetch(
            "SELECT path, is_directory FROM files WHERE repo_id = $1 ORDER BY path",
            repo_id,
        )

        matched = [
            row for row in rows
            if fnmatch.fnmatch(row["path"], path)
        ]

        if not matched:
            return f"No files matching: {path}"

        lines = []
        for row in matched[:MAX_LIST_RESULTS]:
            lines.append(row["path"] + "/" if row["is_directory"] else row["path"])

        if len(matched) > MAX_LIST_RESULTS:
            lines.append(f"\n... {len(matched) - MAX_LIST_RESULTS} more results. Narrow your glob.")

        return "\n".join(lines)

    else:
        # ----------------------------------------------------------
        # Directory mode: list one level, like `ls`.
        #
        # Query:  SELECT name, is_directory FROM files
        #         WHERE repo_id = $1 AND parent_path = $2
        #         ORDER BY is_directory DESC, name
        #
        # Index:  idx_dir_listing(repo_id, parent_path) — exact match
        # ----------------------------------------------------------
        rows = await conn.fetch(
            """
            SELECT name, is_directory
            FROM files
            WHERE repo_id = $1 AND parent_path = $2
            ORDER BY is_directory DESC, name
            """,
            repo_id,
            path,
        )

        if not rows:
            return f"ls: cannot access '{path or '.'}': No such file or directory"

        lines = []
        for row in rows:
            lines.append(row["name"] + "/" if row["is_directory"] else row["name"])

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: read_file
#
# Clubs: cat, head, tail, sed -n
#
# All four fetch content from one file and slice lines.
# start_line and end_line control the slice:
#
#   read_file("auth.py")                            → cat   (all lines)
#   read_file("auth.py", end_line=20)               → head  (first 20)
#   read_file("auth.py", start_line=-10)            → tail  (last 10)
#   read_file("auth.py", start_line=50, end_line=70) → sed  (lines 50–70)
#
# Query:  SELECT content, is_directory FROM files
#         WHERE repo_id = $1 AND path = $2
#
# Index:  UNIQUE(repo_id, path)
#
# Output includes line numbers so the LLM can reference them:
#   42 | def authenticate(user, password):
#   43 |     if not user:
# ---------------------------------------------------------------------------
async def read_file(
    conn: asyncpg.Connection,
    repo_id: str,
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:

    path = path.strip("/").lstrip("./")

    row = await conn.fetchrow(
        "SELECT content, is_directory FROM files WHERE repo_id = $1 AND path = $2",
        repo_id,
        path,
    )

    if not row:
        return f"Error: {path}: No such file or directory"
    if row["is_directory"]:
        return f"Error: {path}: Is a directory"

    lines = row["content"].split("\n")
    total = len(lines)

    # --- Determine the slice ---

    if start_line is not None and start_line < 0:
        # tail mode: start_line=-10 → last 10 lines
        selected = lines[start_line:]
        first_num = total + start_line + 1
    else:
        # cat / head / sed mode
        s = (start_line or 1) - 1   # 1-indexed → 0-indexed
        e = end_line or total
        selected = lines[s:e]
        first_num = s + 1

    # --- Truncate if too long (cat on a huge file) ---

    truncated = False
    if len(selected) > MAX_FILE_LINES:
        selected = selected[:MAX_FILE_LINES]
        truncated = True

    # --- Format with line numbers ---

    width = len(str(first_num + len(selected) - 1))
    output = []
    for i, line in enumerate(selected):
        num = first_num + i
        output.append(f"{num:>{width}} | {line}")

    result = "\n".join(output)

    if truncated:
        result += (
            f"\n\n... truncated ({total} total lines). "
            f"Use start_line/end_line to read specific sections."
        )

    return result


# ---------------------------------------------------------------------------
# Tool 3: search_code
#
# Is: rg (ripgrep)
# The only tool that searches ACROSS files.
#
# Hybrid approach — pg_trgm speed + Python regex compatibility:
#
#   Step 1: Extract literal substrings from the regex
#           "def\s+authenticate" → ["def", "authenticate"]
#
#   Step 2: pg_trgm pre-filter with LIKE (index-accelerated)
#           WHERE content LIKE '%def%' AND content LIKE '%authenticate%'
#           Narrows 3,000 files → maybe 5 candidates.
#
#   Step 3: Python re.search() on candidates for precise line matching
#           Full PCRE: \w, \d, \s, lookaheads — everything works.
#
# Why not PostgreSQL regex directly?
#   PG uses POSIX regex — no \w, \d, \s support.
#   The LLM will naturally generate PCRE patterns. They'd break in PG.
#
# Output mimics ripgrep:
#   src/auth/login.py:10:def authenticate(user, password):
#   src/auth/login.py:25:    if not authenticate(user, pwd):
# ---------------------------------------------------------------------------


def _extract_literals(pattern: str) -> list[str]:
    """
    Pull literal substrings (3+ chars) from a regex for pg_trgm pre-filtering.
    Trigram indexes need at least 3 characters to be useful.

    "def\\s+authenticate"   → ["def", "authenticate"]
    "import\\s+(\\w+)"      → ["import"]
    "\\d+\\.\\d+"           → []  (no literals → falls back to full scan)
    """
    return re.findall(r"[a-zA-Z0-9_]{3,}", pattern)


async def search_code(
    conn: asyncpg.Connection,
    repo_id: str,
    pattern: str,
    glob: Optional[str] = None,
) -> str:

    # Validate regex before hitting the DB
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"Invalid regex: {e}"

    # --- Step 1: Extract literals for pg_trgm pre-filtering ---
    literals = _extract_literals(pattern)

    # --- Step 2: Build query with LIKE filters ---
    #
    # Each LIKE '%literal%' uses:
    #   idx_content_search GIN(content gin_trgm_ops)
    #
    # If no literals extracted (pure regex like \d+), we scan all files.
    # With repo_id scoping, that's still only ~3K files.
    #
    conditions = ["repo_id = $1", "is_directory = false"]
    params: list = [repo_id]
    idx = 2

    for lit in literals:
        conditions.append(f"content LIKE ${idx}")
        params.append(f"%{lit}%")
        idx += 1

    # --glob filter
    if glob:
        ext_match = re.match(r"^\*(\.\w+)$", glob)
        if ext_match:
            # "*.py" → extension = '.py' (equality, idx_file_ext)
            conditions.append(f"extension = ${idx}")
            params.append(ext_match.group(1))
        else:
            # "test_*" → name LIKE 'test_%'
            like_glob = glob.replace("*", "%").replace("?", "_")
            conditions.append(f"name LIKE ${idx}")
            params.append(like_glob)
        idx += 1

    query = f"""
        SELECT path, content FROM files
        WHERE {' AND '.join(conditions)}
        ORDER BY path
    """
    rows = await conn.fetch(query, *params)

    if not rows:
        return f"No matches found for pattern: {pattern}"

    # --- Step 3: Python regex for precise line-level matching ---
    #
    # pg_trgm found candidate files.
    # Now apply the real regex line-by-line to get:
    #   - exact matching lines
    #   - line numbers
    #   - ripgrep-style output
    #
    output = []
    match_count = 0

    for row in rows:
        file_lines = row["content"].split("\n")
        for line_num, line in enumerate(file_lines, 1):
            if compiled.search(line):
                output.append(f"{row['path']}:{line_num}:{line}")
                match_count += 1
                if match_count >= MAX_SEARCH_MATCHES:
                    output.append(
                        f"\n... truncated at {MAX_SEARCH_MATCHES} matches. "
                        f"Narrow with glob or a more specific pattern."
                    )
                    return "\n".join(output)

    if not output:
        # pg_trgm found files with the literal substrings,
        # but the full regex didn't match any individual line.
        return f"No matches found for pattern: {pattern}"

    return "\n".join(output)


# ---------------------------------------------------------------------------
# Dispatcher — routes tool calls from the agent loop
# ---------------------------------------------------------------------------

TOOL_MAP = {
    "list_files": list_files,
    "read_file": read_file,
    "search_code": search_code,
}


async def execute_tool(
    conn: asyncpg.Connection,
    repo_id: str,
    tool_name: str,
    arguments: dict,
) -> str:
    """
    Called by the agent loop when OpenAI returns a tool call.
    Injects conn + repo_id, forwards the rest as kwargs.
    """
    func = TOOL_MAP.get(tool_name)
    if not func:
        return f"Unknown tool: {tool_name}"

    return await func(conn, repo_id, **arguments)
