# AI Call Agent — Agent Skill (public test repo)

An **Agent Skill** that lets an AI agent place **real outbound phone calls**
(book / cancel restaurant reservations, or call any number and perform a task)
via the AI Call Agent REST API.

This repo exists so you can **install the skill straight from GitHub** into a
skill-compatible agent (Hermes, OpenClaw, Claude Code) and test the flow.

```
ai-call-agent/
├── SKILL.md   ← the skill itself (the agent loads this). All English.
└── README.md  ← per-agent install + usage details.
```

> The skill is **key-less**: it reads your API key from the `AI_CALL_AGENT_API_KEY`
> environment variable — no secret is committed here.

---

## Install from GitHub

### 1. Get the `ai-call-agent/` folder

**Clone:**
```sh
git clone https://github.com/frunze-voygr/ai-call-agent-skill.git
```
**…or download a ZIP** (green **Code** button → *Download ZIP*) and unzip it.

Either way you end up with an `ai-call-agent/` folder containing `SKILL.md`.

### 2. Copy that folder into your agent's skills directory

| Agent | Skills directory |
|---|---|
| **Hermes** | `<HERMES_HOME>/skills/` (Windows default `%LOCALAPPDATA%\hermes\skills`, else `~/.hermes/skills`) |
| **OpenClaw** | `~/.openclaw/workspace/skills/` |
| **Claude Code** | `~/.claude/skills/` |

**Windows (PowerShell) — Hermes:**
```powershell
git clone https://github.com/frunze-voygr/ai-call-agent-skill.git
Copy-Item -Recurse -Force `
  ".\ai-call-agent-skill\ai-call-agent" `
  "$env:LOCALAPPDATA\hermes\skills\"
```
**macOS / Linux — OpenClaw:**
```sh
git clone https://github.com/frunze-voygr/ai-call-agent-skill.git
cp -r ai-call-agent-skill/ai-call-agent "$HOME/.openclaw/workspace/skills/"
```

Skills are discovered at session start — **start a new agent session** after copying.

### 3. Set your API key (once per machine)

```powershell
setx AI_CALL_AGENT_API_KEY "pk_live_..."        # Windows, persistent — restart the terminal
```
```sh
export AI_CALL_AGENT_API_KEY="pk_live_..."      # macOS/Linux: add to ~/.bashrc / ~/.zshrc
```
Verify: `curl -s -H "X-API-Key: $AI_CALL_AGENT_API_KEY" https://dev.voygr.tech/users/me` → `200`.

> **OpenClaw note:** it runs a long-lived gateway — start it from a terminal
> where `AI_CALL_AGENT_API_KEY` (and `ANTHROPIC_API_KEY`, used in `api_key`
> mode, not a subscription) are set, or persist them and restart the gateway.

---

## Use it

Start a new session and ask in natural language, e.g.:

> "Call +15551234567 and book a table for 2 tomorrow at 8pm, name Alex,
> my contact +15551230000."

The agent loads `ai-call-agent`, places the call, follows the live event
stream, answers any mid-call question in the terminal, and reports the outcome.

> **Test safety:** while testing, only dial a number you control (your own
> test phone, as stated in `SKILL.md`). Real calls cost credits and ring a real phone.

See [`ai-call-agent/README.md`](ai-call-agent/README.md) for full per-agent
details, the fallback (system-prompt / MCP) options, and distribution notes.
