# claude-code-python

[![Python >= 3.9](https://img.shields.io/badge/python-%3E%3D3.9-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)]()

> **Python SDK for Claude Code CLI** — A complete Python port of [claude-code-node](https://github.com/oceanz0312/claude-code-node), letting you drive Claude Code programmatically from Python with a clean async API.

[中文文档](./README.md)

---

## Critical Prerequisite

> **This SDK does NOT call the Claude API directly — it drives the Claude Code CLI (npm package [`@anthropic-ai/claude-code`](https://www.npmjs.com/package/@anthropic-ai/claude-code)) as a subprocess.** Since the CLI is a Node.js application, your runtime environment must have Node.js and the npm package installed:

| Dependency | Version | Description |
|-----------|---------|-------------|
| **Node.js** | >= 22 | Runtime for Claude Code CLI |
| **Claude Code CLI** | latest | npm package `@anthropic-ai/claude-code` |
| **Python** | >= 3.9 | Runtime for this SDK |

### Deployment Script

Use the following script to automatically install Claude Code CLI and its dependencies during server deployment:

```bash
#!/bin/bash
# deploy_claude_code.sh — Auto-install Claude Code CLI on deployment

set -e

# 1. Install Node.js (skip if already installed)
if ! command -v node &> /dev/null; then
    echo "Installing Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y nodejs
fi

# 2. Install Claude Code CLI
if ! command -v claude &> /dev/null; then
    echo "Installing Claude Code CLI..."
    npm install -g @anthropic-ai/claude-code
fi

# 3. Install Python SDK
pip install git+https://github.com/oceanz0312/claude-code-python.git

# 4. Verify installation
echo "Node.js: $(node --version)"
echo "Claude CLI: $(claude --version)"
echo "Python SDK: OK"
```

### K8s Init Container

If your service runs on Kubernetes, install dependencies via an init container before the Pod starts:

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      initContainers:
        - name: install-claude-cli
          image: node:22-slim
          command:
            - sh
            - -c
            - |
              npm install -g @anthropic-ai/claude-code
              cp -r /usr/local/lib/node_modules /shared/node_modules
              cp /usr/local/bin/node /shared/node
              cp /usr/local/bin/claude /shared/claude
          volumeMounts:
            - name: claude-bin
              mountPath: /shared
      containers:
        - name: your-service
          env:
            - name: PATH
              value: "/shared:/usr/local/bin:/usr/bin:/bin"
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: claude-secrets
                  key: api-key
          volumeMounts:
            - name: claude-bin
              mountPath: /shared
      volumes:
        - name: claude-bin
          emptyDir: {}
```

---

## What You Get

```python
from claude_code import ClaudeCode, SessionOptions

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    dangerously_skip_permissions=True,
))

# Buffered — collect all events, get final response
turn = await session.run("Fix the failing tests in src/")
print(turn.final_response)

# Multi-turn: just keep calling run() — session resume is automatic
turn2 = await session.run("Now add test coverage for edge cases")
```

**No HTTP server, no protocol translation, no abstractions over abstractions.** Just a typed, async wrapper around the Claude Code CLI that handles the messy parts for you:

| Capability | What it does |
|------------|-------------|
| **Session management** | Auto `--resume` across turns — you never touch session IDs |
| **Streaming** | `AsyncIterator[RelayEvent]` with 7 typed event kinds |
| **35+ CLI options** | Every useful flag mapped to a typed field — `model`, `system_prompt`, `allowed_tools`, `json_schema`, `max_budget_usd`, `agents`, `mcp_config`... |
| **Structured output** | Pass a JSON Schema, get parsed objects back in `turn.structured_output` |
| **Image input** | Send local screenshots alongside text prompts |
| **Abort** | Cancel any turn with `asyncio.Event` or any signal-like object |
| **Fail-fast** | Detect API errors in seconds, not minutes (critical for CI/CD) |
| **Zero dependencies** | Pure Python, stdlib only — no third-party packages required |

---

## Pure TDD — Complete Replication of Claude Code CLI

This project is a **1:1 faithful port** of [claude-code-node](https://github.com/oceanz0312/claude-code-node) (TypeScript) to Python, built entirely with **Test-Driven Development**:

> **Tests first, code second.** Every module was implemented by porting the TypeScript test suite first, then writing production code until all tests passed. The result: not a "best-effort" translation, but a **verified, behavior-identical replication** of the original SDK.

The TDD process guarantees:

- **35+ CLI parameters** — identical coverage, every flag verified by test
- **Dual-layer stream deduplication** — same algorithm, same behavior, same edge cases
- **7 relay event types** — same semantic event model, same serialization
- **Fail-fast mechanism** — same stderr + stdout detection, same timing
- **Raw event logging** — same NDJSON format, same file layout
- **Environment isolation** — same subprocess sandboxing, same env inheritance rules
- **All 70+ tests pass** — unit tests + real-model E2E tests, **all green, zero skips**

Every test case maps 1:1 to its TypeScript counterpart. If the TypeScript SDK has a test for it, the Python SDK has the same test — and it passes.

---

## Tested & Reliable

| Metric | Detail |
|--------|--------|
| **70+ test cases** | Unit tests + real-model E2E tests — **all passing** |
| **Fake CLI simulator** | `fake_claude.py` emulates the full `stream-json` protocol — unit tests run without a real CLI or API key |
| **Real E2E suite** | Hits the actual Claude CLI with real credentials — tests multi-turn memory, auth paths, system prompts, image understanding, agent identity, and 15+ CLI flag forwarding |
| **E2E test artifacts** | Every run saves NDJSON logs, relay events, and final responses for post-mortem analysis |
| **Zero dependencies** | Pure stdlib — nothing to break, nothing to audit |

---

## Streaming Example

```python
import asyncio
from claude_code import ClaudeCode, SessionOptions

async def main():
    claude = ClaudeCode()
    session = claude.start_session(SessionOptions(
        model="sonnet",
        dangerously_skip_permissions=True,
    ))

    streamed = await session.run_streamed("Explain how async works in Python")

    async for event in streamed.events:
        if event.type == "text_delta":
            print(event.content, end="", flush=True)
        elif event.type == "turn_complete":
            print(f"\n\nTokens: {event.input_tokens} in / {event.output_tokens} out")
            print(f"Cost: ${event.cost_usd:.4f}")

asyncio.run(main())
```

---

## Structured Output

```python
from claude_code import ClaudeCode, SessionOptions

schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
    "required": ["name", "age"],
}

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    json_schema=schema,
    dangerously_skip_permissions=True,
))

turn = await session.run("Extract: John is 30 years old")
print(turn.structured_output)  # {"name": "John", "age": 30}
```

---

## Image Input

```python
from claude_code import ClaudeCode, SessionOptions

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    dangerously_skip_permissions=True,
))

turn = await session.run([
    {"type": "text", "text": "What's in this image?"},
    {"type": "local_image", "path": "./screenshot.png"},
])
print(turn.final_response)
```

---

## Session Management

```python
from claude_code import ClaudeCode, SessionOptions

claude = ClaudeCode()

# Start a new session
session = claude.start_session(SessionOptions(model="sonnet"))
turn1 = await session.run("Remember: the secret code is 42")

# Resume automatically across turns
turn2 = await session.run("What's the secret code?")  # "42"

# Or resume by session ID in a new instance
session2 = claude.resume_session(session.id, SessionOptions(model="sonnet"))
turn3 = await session2.run("What's the secret code?")  # Still "42"

# Or continue the most recent session
session3 = claude.continue_session(SessionOptions(model="sonnet"))
```

---

## Abort / Cancellation

```python
import asyncio
from claude_code import ClaudeCode, SessionOptions, TurnOptions

claude = ClaudeCode()
session = claude.start_session(SessionOptions(
    model="sonnet",
    dangerously_skip_permissions=True,
))

abort = asyncio.Event()

# Cancel after 5 seconds
asyncio.get_event_loop().call_later(5, abort.set)

turn = await session.run(
    "Write a very long essay about the history of computing",
    TurnOptions(signal=abort),
)
```

---

## Authentication

```python
from claude_code import ClaudeCode, ClaudeCodeOptions

# Option 1: API Key
claude = ClaudeCode(ClaudeCodeOptions(api_key="sk-ant-..."))

# Option 2: Auth Token + Base URL (for proxies / compatible endpoints)
claude = ClaudeCode(ClaudeCodeOptions(
    auth_token="your-token",
    base_url="https://your-proxy.com/v1",
))

# Option 3: Environment variables (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL)
claude = ClaudeCode()
```

---

## All SessionOptions

```python
@dataclass
class SessionOptions:
    # Model & context
    model: str                          # Model name (e.g. "sonnet", "opus")
    cwd: str                            # Working directory for the CLI
    additional_directories: list[str]   # Extra directories to expose
    max_turns: int                      # Max agentic turns
    max_budget_usd: float               # Spending cap

    # System prompt
    system_prompt: str                  # Override system prompt
    system_prompt_file: str             # Load system prompt from file
    append_system_prompt: str           # Append to default system prompt
    append_system_prompt_file: str      # Append from file

    # Permissions
    permission_mode: PermissionMode     # "default" | "acceptEdits" | "plan" | "auto" | "dontAsk" | "bypassPermissions"
    dangerously_skip_permissions: bool  # Skip all permission prompts
    allowed_tools: list[str]            # Allowlisted tools
    disallowed_tools: list[str]         # Blocklisted tools

    # MCP
    mcp_config: str | list[str]        # MCP config file path(s)

    # Agents
    agents: dict | str                  # Sub-agent definitions

    # Structured output
    json_schema: str | dict             # JSON Schema for structured output

    # ... and 20+ more options
```

---

## Architecture

```
┌──────────────┐     ┌────────────┐     ┌──────────────┐
│  Your Code   │────▶│  Session    │────▶│ ClaudeCodeExec│
│              │     │ (state mgmt)│     │ (subprocess)  │
│  ClaudeCode  │     │ (dedup)    │     │ (stdin/stdout)│
│  (entry pt)  │     │ (fail-fast)│     │ (abort)       │
└──────────────┘     └──────┬─────┘     └───────┬───────┘
                            │                   │
                     ┌──────▼─────┐      ┌──────▼───────┐
                     │ Translator  │      │ Claude Code  │
                     │ (raw→relay) │      │ CLI Process  │
                     └─────────────┘      └──────────────┘
```

**Data flow**: Your code → `ClaudeCode` → `Session` → `ClaudeCodeExec` → spawns `claude` CLI subprocess → NDJSON over stdout → `parse_line()` → `Translator.translate()` → `RelayEvent` stream → your callback or async iterator.

---

## Relay Event Types

| Event | Fields | Description |
|-------|--------|-------------|
| `TextDeltaEvent` | `content` | Incremental text output |
| `ThinkingDeltaEvent` | `content` | Incremental thinking/reasoning |
| `ToolUseEvent` | `tool_use_id`, `tool_name`, `input` | Tool invocation |
| `ToolResultEvent` | `tool_use_id`, `output`, `is_error` | Tool execution result |
| `SessionMetaEvent` | `model` | Session metadata (model name) |
| `TurnCompleteEvent` | `session_id`, `cost_usd`, `input_tokens`, `output_tokens`, `context_window` | Turn finished |
| `ErrorEvent` | `message`, `session_id` | Error occurred |

---

## What It's NOT

- Not an HTTP API server
- Not a multi-model gateway (it wraps Claude Code, period)
- Not a replacement for the CLI (it drives it)

---

## Installation

```bash
# Install directly from GitHub
pip install git+https://github.com/oceanz0312/claude-code-python.git

# Or clone and install in development mode
git clone https://github.com/oceanz0312/claude-code-python.git
cd claude-code-python
pip install -e .
```

---

## E2E Testing

Real end-to-end tests against the Claude Code CLI are included:

```bash
# 1. Set environment variables for authentication
export E2E_MODEL=sonnet
export E2E_API_KEY=sk-ant-...        # or use auth token:
# export E2E_AUTH_TOKEN=your-token
# export E2E_BASE_URL=https://your-proxy.com/

# 2. Run the E2E suite
python -m pytest tests/e2e/ -v
```

---

## Development

```bash
# Install in development mode
pip install -e .

# Run all tests
python -m pytest tests/ -v

# Run unit tests only
python -m pytest tests/unit/ -v

# Run E2E tests only
python -m pytest tests/e2e/ -v
```

---

## Related Projects

| Project | Description |
|---------|-------------|
| [claude-code-node](https://github.com/oceanz0312/claude-code-node) | The original TypeScript SDK — this project is its Python port |
| [claude-code-openai-wrapper](https://github.com/RichardAtCT/claude-code-openai-wrapper) | Python/FastAPI service exposing Claude Code as an OpenAI-compatible API |
| [claude-code-api](https://github.com/bethington/claude-code-api) | Node.js/Express bridge to OpenAI-compatible endpoint |
| [claude-code-api-rs](https://github.com/ZhangHanDong/claude-code-api-rs) | Rust/Axum high-performance API server |

---

## License

[MIT](LICENSE)
