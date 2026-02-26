"""
Agent loop — the ReAct engine that powers "Chat with Repo".

Flow:
  1. Build messages: system prompt + chat history + user question
  2. Call OpenAI (streaming)
  3. If OpenAI returns tool calls → execute via shell.py → append results → loop
  4. If OpenAI returns text → stream to client as text_delta events → done
  5. Safety: max 15 iterations

This is an async generator. The FastAPI route iterates over it
and converts each yielded dict into an SSE event.

Events yielded:
  {"type": "tool_start", "name": "search_code", "arguments": {...}}
  {"type": "tool_end",   "name": "search_code"}
  {"type": "text_delta", "content": "The auth module..."}
  {"type": "done"}
  {"type": "error",      "content": "..."}
"""

import json
from typing import AsyncGenerator

import asyncpg
from openai import AsyncOpenAI

from backend.tools.definitions import SYSTEM_PROMPT, TOOLS
from backend.tools.shell import execute_tool


MAX_ITERATIONS = 15

client = AsyncOpenAI()  # reads OPENAI_API_KEY from env


async def run_agent(
    conn: asyncpg.Connection,
    repo_id: str,
    question: str,
    history: list[dict] | None = None,
    model: str = "gpt-4o",
) -> AsyncGenerator[dict, None]:
    """
    Async generator that runs the agentic ReAct loop.

    Args:
        conn:      asyncpg connection (for tool execution)
        repo_id:   which repo the tools operate on
        question:  the user's question
        history:   prior messages [{"role": "user"|"assistant", "content": "..."}]
        model:     OpenAI model to use

    Yields:
        dicts with "type" key — see module docstring for event types.
    """

    # --- Build the initial messages array ---
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    # --- ReAct loop ---
    for iteration in range(MAX_ITERATIONS):

        # ---------------------------------------------------------------
        # Call OpenAI with streaming.
        #
        # We always stream because:
        #   - Tool call iterations: we accumulate chunks, then execute
        #   - Text response: we yield text_delta events as they arrive
        #     (no blank-screen wait for the user)
        # ---------------------------------------------------------------
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            stream=True,
        )

        # Accumulators for the streamed response
        tool_calls: dict[int, dict] = {}   # index → {id, name, arguments_str}
        text_content = ""

        # ---------------------------------------------------------------
        # Consume the stream chunk by chunk.
        #
        # Each chunk.choices[0].delta may contain:
        #   - .tool_calls  → partial tool call (name, argument fragments)
        #   - .content     → text fragment (the final answer)
        #   - neither      → role announcement or empty delta
        #
        # Tool call arguments arrive in pieces:
        #   chunk 1: {id: "call_abc", name: "search_code", arguments: ""}
        #   chunk 2: {arguments: '{"patt'}
        #   chunk 3: {arguments: 'ern": "auth"}'}
        # We concatenate them, then JSON.parse when the stream ends.
        # ---------------------------------------------------------------
        async for chunk in stream:
            choice = chunk.choices[0]
            delta = choice.delta

            # --- Accumulate tool call fragments ---
            if delta.tool_calls:
                for tc_chunk in delta.tool_calls:
                    idx = tc_chunk.index

                    if idx not in tool_calls:
                        # First chunk for this tool call — has id and name
                        tool_calls[idx] = {
                            "id": tc_chunk.id,
                            "name": tc_chunk.function.name,
                            "arguments_str": "",
                        }

                    # Append argument fragment
                    if tc_chunk.function.arguments:
                        tool_calls[idx]["arguments_str"] += tc_chunk.function.arguments

            # --- Stream text to client immediately ---
            if delta.content:
                text_content += delta.content
                yield {"type": "text_delta", "content": delta.content}

        # ---------------------------------------------------------------
        # Stream ended. Two possible outcomes:
        #
        # A) tool_calls is non-empty → execute tools, append results, loop
        # B) text_content is non-empty → final answer, we're done
        # ---------------------------------------------------------------

        if tool_calls:
            # --- Build the assistant message (OpenAI requires this format) ---
            assistant_tool_calls = []
            for idx in sorted(tool_calls.keys()):
                tc = tool_calls[idx]
                assistant_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments_str"],
                    },
                })

            messages.append({
                "role": "assistant",
                "tool_calls": assistant_tool_calls,
            })

            # --- Execute each tool call ---
            for idx in sorted(tool_calls.keys()):
                tc = tool_calls[idx]
                name = tc["name"]
                arguments_str = tc["arguments_str"]

                # Parse arguments
                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError:
                    arguments = {}

                # Notify frontend: tool execution starting
                yield {
                    "type": "tool_start",
                    "name": name,
                    "arguments": arguments,
                }

                # Execute via shell.py dispatcher
                result = await execute_tool(conn, repo_id, name, arguments)

                # Notify frontend: tool execution done
                yield {"type": "tool_end", "name": name}

                # Append tool result to messages (OpenAI requires tool_call_id)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

            # Loop — next iteration will send updated messages to OpenAI
            continue

        else:
            # --- Final text answer (already streamed via text_delta events) ---
            yield {"type": "done"}
            return

    # --- Safety: hit max iterations ---
    yield {
        "type": "text_delta",
        "content": (
            "\n\n---\n"
            "I've reached the maximum exploration depth. "
            "Here's my best answer based on what I've found so far."
        ),
    }
    yield {"type": "done"}
