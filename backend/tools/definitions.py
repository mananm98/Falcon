"""
OpenAI function-calling schemas for the 3 virtual shell tools,
and the system prompt that guides the agent.

These schemas tell OpenAI what tools exist and what params they accept.
They must match the signatures in shell.py exactly.
"""

# ---------------------------------------------------------------------------
# System prompt — injected as the first message on every agent loop iteration.
#
# Keep it short. This is sent with EVERY OpenAI call (including tool-call
# iterations), so every extra token multiplies across the loop.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a code exploration assistant. You have access to a repository's codebase \
through the tools provided. Your job is to answer questions about the code \
accurately and thoroughly.

## How to explore

1. Start with `list_files` to understand the repo structure.
2. Use `search_code` to find where specific patterns, functions, or classes are defined or used.
3. Use `read_file` to read the actual code. Use `start_line`/`end_line` for large files.

## Rules

- NEVER guess. Always verify by reading the code before answering.
- Reference specific file paths and line numbers in your answers (e.g., `src/auth.py:42`).
- If a file is too large, read it in sections rather than all at once.
- When searching, start broad and narrow down. If a search returns too many results, add a glob filter.
- You can call multiple tools in parallel when they are independent.
"""


# ---------------------------------------------------------------------------
# Tool definitions — one per tool in shell.py
#
# list_files(path)
# read_file(path, start_line?, end_line?)
# search_code(pattern, glob?)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files and directories in the repository. "
                "Pass a directory path to list its contents (like `ls`), "
                "or use glob patterns (*, **, ?) to search recursively (like `find`).\n\n"
                "Examples:\n"
                '  list_files(path="")              → list repo root\n'
                '  list_files(path="src/auth")      → list contents of src/auth/\n'
                '  list_files(path="**/*.py")       → find all Python files\n'
                '  list_files(path="src/**/*.test.ts") → find test files under src/'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory path to list, or glob pattern to search. "
                            "Use '' for repo root. "
                            "Use ** for recursive matching, * for single-level matching."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file, optionally a specific line range.\n\n"
                "Examples:\n"
                '  read_file(path="src/auth.py")                        → entire file\n'
                '  read_file(path="src/auth.py", end_line=20)           → first 20 lines\n'
                '  read_file(path="src/auth.py", start_line=-10)        → last 10 lines\n'
                '  read_file(path="src/auth.py", start_line=50, end_line=70) → lines 50-70'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": (
                            "Start line (1-indexed). "
                            "Negative values count from end: -10 means last 10 lines."
                        ),
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "End line (1-indexed, inclusive).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search for a regex pattern across all files in the repository. "
                "Returns matching lines with file paths and line numbers, "
                "formatted like ripgrep output (path:line:content).\n\n"
                "Examples:\n"
                '  search_code(pattern="def authenticate")               → find function def\n'
                '  search_code(pattern="import.*redis", glob="*.py")     → search Python files only\n'
                '  search_code(pattern="TODO|FIXME")                     → find all TODOs'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for in file contents.",
                    },
                    "glob": {
                        "type": "string",
                        "description": (
                            "Optional file filter. "
                            "Use '*.py' for Python files, 'test_*' for test files, etc."
                        ),
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]
