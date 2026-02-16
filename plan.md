# Background Coding Agent — Implementation Plan

## Product Overview

A background coding agent (inspired by Ramp's "Inspect") that autonomously writes and verifies code with the rigor of a professional engineer. It closes the loop on verifying its work by having all the context and tools needed to prove it — running tests, reviewing telemetry, querying feature flags, and visually verifying frontend changes.

---

## Key Features Identified

### 1. Sandboxed Execution Environment
- Isolated VMs per session (Ramp uses Modal)
- Pre-configured dev stacks (DB, build tools, etc.)
- Repo images rebuilt every 30 min; snapshots frozen/restored for fast startup
- Warm sandbox pools — spun up when users start typing
- Block writes until branch sync completes; allow reads immediately

### 2. Agent Framework
- Server-first architecture with plugin system (Ramp uses OpenCode)
- Plugin system for custom integrations (MCPs)
- Supports multiple frontier LLM models
- Agents can spawn child sessions for parallel work

### 3. API & Session Infrastructure
- Each session gets its own isolated state + SQLite DB (Ramp uses Cloudflare Durable Objects)
- WebSocket streaming via Cloudflare Agents SDK
- Hibernation API for long-lived connections without compute cost
- Queued follow-up prompts (async contributions)
- Mid-execution stop, snapshot restore for resumption

### 4. Git & Auth
- GitHub App for repo cloning without user context
- User tokens for PR creation on behalf of users
- Dynamic git config (user.name/email per commit)
- Webhooks for branch/PR/merge/close events

### 5. Clients
- **Web Client**: Hosted VS Code (code-server), streamed desktop view, before/after screenshots, usage dashboard
- **Slack Client**: Classifier model picks repo from message/channel context, real-time Block Kit updates
- **Chrome Extension**: Sidebar chat, screenshot + DOM tree extraction, React internals integration, MDM-distributed
- **PR Interface**: Discuss/collaborate on proposed changes, session sharing

### 6. Multiplayer
- Concurrent users in a single session
- State sync across all connected clients
- Attribution per prompt — each code change tied to the user who initiated it

### 7. Verification & QA
- Runs test suites
- Screenshots and visual verification via computer vision
- Queries feature flags (LaunchDarkly)
- Reviews telemetry (Sentry, Datadog)

---

## Implementation Plan

### Phase 1: Core Infrastructure

#### 1.1 Sandbox Environment
- **Choose a VM provider**: Modal, Firecracker (like Fly.io), or Docker-based isolation
- Build a **sandbox image pipeline**:
  - Cron job rebuilds repo images every 30 min (clone repo, install deps, build)
  - Store images as snapshots (e.g., Modal snapshots, Docker layers, or VM disk images)
  - Implement snapshot freeze/restore for fast session startup
- **Warm pool manager**:
  - Service that maintains N pre-warmed sandboxes per high-traffic repo
  - Trigger warm-up on "user started typing" events from clients
- **File system sync**:
  - On session start, sync target branch into sandbox
  - Allow reads immediately, block writes until sync completes (use a lock/flag)
- Pre-install dev stack in images: your DB (Postgres), build tools, test runners

#### 1.2 Agent Framework
- Integrate **OpenCode** (open-source, server-first architecture, typed SDK, plugin system)
  - Alternatively: build on top of Claude Code SDK, Aider, or a custom agent loop
- Build **plugin/MCP layer**:
  - Plugin for running tests and returning results to the agent
  - Plugin for querying feature flags (your equivalent of LaunchDarkly)
  - Plugin for querying monitoring/telemetry (Sentry/Datadog APIs)
  - Plugin for taking screenshots and running visual verification
- **Model routing**: Support multiple LLM providers (Anthropic, OpenAI, etc.) via a unified interface
- **Child session spawning**: Agent can request new parallel sessions for research or multi-repo tasks

#### 1.3 API Layer
- **Session management service**:
  - Use Cloudflare Durable Objects or an equivalent (each session = isolated state + SQLite DB)
  - Alternatively: a stateful service with per-session state in Redis/SQLite
- **WebSocket streaming**: Real-time agent output streamed to all connected clients
- **Hibernation**: For idle sessions, persist state to disk, free compute, restore on reconnect
- **Prompt queue**: Allow users to queue follow-up prompts while agent is working (not injected mid-execution)
- **Stop mechanism**: Allow mid-execution cancellation, clean up sandbox state

### Phase 2: Git Integration & Auth

#### 2.1 GitHub Integration
- Create a **GitHub App**:
  - Permissions: repo read/write, PR create, webhook subscriptions
  - Use app credentials for cloning (no user context needed)
  - Use user OAuth tokens for PR creation (attributed to user)
- **Dynamic git config**: Set `user.name` and `user.email` per commit based on the prompting user
- **Webhook handler**: Listen for branch push, PR update, PR merge, PR close events
  - Update session state when external changes occur
  - Auto-close sessions when PRs are merged

#### 2.2 Attribution System
- Track which user sent each prompt
- Tag commits/code changes with the initiating user
- Enforce: users cannot approve their own agent-generated PRs (separation of concerns)

### Phase 3: Client Interfaces

#### 3.1 Web Client (Primary)
- **Frontend** (React/Next.js):
  - Session list, create new session, view active sessions
  - Real-time agent output stream (WebSocket)
  - Embedded **code-server** (VS Code in browser) connected to the sandbox filesystem
  - Streamed desktop view (VNC/noVNC) for visual verification
  - Before/after screenshot comparison UI
  - Usage dashboard: merged PRs, concurrent users, session stats
- **Mobile responsive** version of the above

#### 3.2 Slack Client
- **Slack Bot**:
  - Listen to messages/mentions in configured channels
  - **Classifier** (LLM call) to determine: target repo, intent, from message + thread + channel name
  - Create session, stream progress updates via Block Kit messages
  - Post PR link when complete
  - Support thread-based follow-ups (queue as prompts)

#### 3.3 Chrome Extension
- **Sidebar panel**: Chat interface for the agent
- **Screenshot tool**: Capture current page, extract DOM tree, identify React component hierarchy
- **Context injection**: Send screenshot + DOM + React internals as context to the agent
- **Distribution**: Internal extension server or MDM policy push (ExtensionInstallForcelist)
- **Update server**: Self-hosted extension update endpoint (bypasses Chrome Web Store)

#### 3.4 PR Interface
- Comments on PRs trigger agent sessions or follow-ups
- Session sharing: generate a link, another user joins the same session
- Inline discussion on agent-proposed changes

### Phase 4: Multiplayer & Collaboration

- **Session sharing**: Multiple users connect to the same session via WebSocket
- **State sync**: All clients see the same agent output, file changes, terminal state
- **Prompt attribution**: Each prompt tagged with sender; code changes attributed accordingly
- **Concurrent editing**: Handle prompt queue from multiple users (FIFO or priority-based)
- **Use cases**: Pair programming with agent, QA review, teaching non-engineers

### Phase 5: Verification & Quality

#### 5.1 Automated Verification
- **Test execution**: Agent runs your test suite in the sandbox, interprets results
- **Visual verification**:
  - Start dev server in sandbox
  - Take screenshots (Puppeteer/Playwright)
  - Use vision model to verify UI matches intent
- **Telemetry checks**: Query Sentry/Datadog for error spikes after deploy
- **Feature flag checks**: Query LaunchDarkly (or equivalent) for flag state

#### 5.2 CI/CD Integration
- Trigger your CI pipeline (Buildkite, GitHub Actions, etc.) on agent-created PRs
- Feed CI results back to the agent for self-correction
- Auto-request human review when CI passes

### Phase 6: Operational Concerns

- **Monitoring**: Track session count, agent success rate, time-to-PR, sandbox utilization
- **Cost management**: Sandbox compute costs, LLM API costs per session
- **Security**:
  - Sandboxes must be fully isolated (no network access to prod, secrets management)
  - Audit log of all agent actions
  - User auth via SSO/OAuth
- **Rate limiting**: Per-user session limits, org-wide concurrency caps

---

## Recommended Tech Stack

| Component | Technology |
|---|---|
| Sandbox VMs | Modal, Firecracker, or Fly Machines |
| Agent framework | OpenCode or Claude Code SDK |
| LLM providers | Anthropic Claude, OpenAI GPT |
| API/Session state | Cloudflare Durable Objects or custom stateful service |
| Real-time comms | WebSockets (Cloudflare Workers or custom) |
| Web frontend | React/Next.js |
| In-browser IDE | code-server (VS Code) |
| Visual verification | Playwright + vision model |
| Git integration | GitHub App + webhooks |
| Slack integration | Slack Bolt SDK |
| Chrome extension | Manifest V3 + custom update server |
| Monitoring | Sentry, Datadog, or your existing stack |

---

## Build Order Recommendation

1. **Sandbox + Agent** (Phase 1) — this is the core. Get a single session working end-to-end: user sends prompt, agent writes code in sandbox, creates PR.
2. **Git integration** (Phase 2) — proper attribution, GitHub App, webhooks.
3. **Web client** (Phase 3.1) — primary interface for power users.
4. **Slack client** (Phase 3.2) — highest-leverage distribution channel for organic adoption.
5. **Verification** (Phase 5) — test execution, visual checks. This is what differentiates from generic agents.
6. **Multiplayer** (Phase 4) — collaboration features.
7. **Chrome extension** (Phase 3.3) — nice-to-have, high impact for frontend work.

---

## MVP Definition

The minimum viable version is **Phase 1 + Phase 2 + a basic web client** — that alone gives you a working background agent that takes prompts and produces PRs.
