# AI Call Agent — Agent Skill

Lets an AI agent place **real outbound phone calls** (book / cancel
restaurant reservations, call any number and perform a task) via the AI
Call Agent REST API.

`SKILL.md` is the **Agent Skills** format (YAML front-matter + Markdown).
**One file works across all skill-compatible agents** — verified identical
and tested on Hermes and OpenClaw; Claude Code uses the same spec.

```
ai-call-agent/
├── SKILL.md   ← the skill (agents load this). All English.
└── README.md  ← this file (install per agent; for humans).
```

---

## 1. Install — copy the `ai-call-agent/` folder into the agent's skills dir

| Agent | Skills dir | Verify |
|---|---|---|
| **Hermes** | `<HERMES_HOME>/skills/ai-call-agent/` (Windows default `%LOCALAPPDATA%\hermes\skills`, else `~/.hermes`) | `hermes skills list` → `ai-call-agent \| enabled` |
| **OpenClaw** | `~/.openclaw/workspace/skills/ai-call-agent/` | `openclaw skills list` → `ai-call-agent ✓ ready` |
| **Claude Code** | `~/.claude/skills/ai-call-agent/` | `/` skill list |

**Windows (PowerShell), example for Hermes:**
```powershell
Copy-Item -Recurse -Force `
  "D:\fr-work\voyger\AI-call-agent\skills\ai-call-agent" `
  "$env:LOCALAPPDATA\hermes\skills\"
```
**macOS / Linux, example for OpenClaw:**
```sh
cp -r ai-call-agent "$HOME/.openclaw/workspace/skills/"
```
Skills are discovered at session start — start a **new** agent session
after copying.

---

## 2. API key (once per machine)

The skill reads the key from env `AI_CALL_AGENT_API_KEY` (never in the
prompt). It must be in the environment the agent's shell/gateway runs in.

```powershell
setx AI_CALL_AGENT_API_KEY "pk_live_..."        # Windows, persistent — restart terminal
```
```sh
export AI_CALL_AGENT_API_KEY="pk_live_..."      # macOS/Linux: add to ~/.bashrc / ~/.zshrc
```
Verify: `curl -s -H "X-API-Key: $AI_CALL_AGENT_API_KEY" http://gw.vox-bot.live/users/me` → `200`.

> **OpenClaw note:** OpenClaw runs a long-lived Gateway. Start it from a
> terminal where `AI_CALL_AGENT_API_KEY` (and `ANTHROPIC_API_KEY` for its
> own LLM, which uses `mode: api_key` — not a subscription) are set, or
> persist them and restart the gateway.

---

## 3. Use it

Start a new session and ask in natural language, e.g.:
> "Call +15551234567 and book a table for 2 tomorrow at 8pm, name Alex,
> my contact +15551230000."

The agent loads `ai-call-agent`, places the call, follows the SSE event
stream, answers any mid-call `ask_user` in the terminal, and reports the
outcome. A successful booking appears in `GET /v1/booking/bookings`.

> **Test safety:** while testing, only dial a number you control (your own
> test phone, as stated in `SKILL.md`). Real calls cost credits and ring a real phone.

---

## Portability — does this skill work in any agent?

**No single format works everywhere.** This `SKILL.md` is the Agent Skills
convention:

- ✅ **Hermes / Claude Code / OpenClaw** — drop the folder into the skills
  dir (above). Same file, all three.
- ⚠️ **Other agents (no skill support)** — use a fallback:
  1. **System prompt:** paste the body of `SKILL.md` (after the front-matter)
     into the agent's system prompt / custom instructions — plain
     instructions + `curl`, works anywhere there's a shell.
  2. **MCP server:** for MCP-capable agents (Claude Desktop, …), use the
     `ai-call-agent-mcp` package — same capabilities as native MCP tools.

---

## Distribute to users

- **Ship the folder** — users copy `ai-call-agent/` into their agent's
  skills dir. The skill is **key-less**: each user sets their own
  `AI_CALL_AGENT_API_KEY`.
- **OpenClaw / ClawHub** — `npx clawhub publish ./ai-call-agent` →
  users `npx clawhub install ai-call-agent`.

---

## Notes
- **Base URL** in `SKILL.md` is `http://gw.vox-bot.live` (test gateway,
  HTTP only). For production, swap it for the prod gateway in the
  `Connection` section + update the test-number rule.
