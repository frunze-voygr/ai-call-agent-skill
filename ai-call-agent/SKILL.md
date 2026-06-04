---
name: ai-call-agent
description: Use WHENEVER the user wants to make a phone call, call a number, book or cancel a restaurant reservation by phone, check a call's status/outcome, or answer a question the call bot asked mid-call. Places REAL outbound voice calls via the AI Call Agent REST API and follows call events. Always consult this skill before saying you cannot make calls.
version: 2.0.1
author: voygr-tech
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [phone, calls, voice, booking, cancel, telephony, api, sse, events]
    related_skills: []
---

# AI Call Agent — place phone calls via REST API

You CAN place real phone calls. You do NOT need a native voice/telephony
tool — you make calls by sending HTTP requests to the AI Call Agent API with
your shell/terminal tool (`curl`). NEVER tell the user you cannot make calls
or that you lack a voice function. When the user asks to call, book, or
cancel by phone — DO IT via this API.

The backend dials the number, talks to whoever answers (in the chosen
language), performs the task, and reports an outcome. Your job is to gather
the details, place the call, follow it, answer any mid-call question, and
report the result.

## Connection

- **Base URL:** `https://dev.voygr.tech` (HTTPS).
- **Auth:** EVERY request sends the header `X-API-Key`, value from the env
  var `AI_CALL_AGENT_API_KEY`. NEVER print or echo the key value; always
  reference it as `$AI_CALL_AGENT_API_KEY` in shell commands.
- **Test rule:** during testing only dial a number you control (your own
  test phone). A real call costs credits and rings a real phone — only call
  when explicitly asked.

Quick connectivity check: `GET /users/me` →
```sh
curl -s -H "X-API-Key: $AI_CALL_AGENT_API_KEY" https://dev.voygr.tech/users/me
# 200 {"customer_id":"...","quota_limit":...,...}
```

## Capabilities (endpoints)

### 1. Place a free-form call — `POST /calls`
The bot reads a natural-language `brief` and improvises the conversation.
```sh
curl -s -X POST https://dev.voygr.tech/calls \
  -H "X-API-Key: $AI_CALL_AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"target_phone":"+15551234567","brief":"<what to say/do, in the call language>","language":"ru"}'
```
Body: `target_phone` (E.164), `brief` (natural-language task), `language`
(`ru` / `en` / `auto`). Returns a `2xx` with `call_id`: either `201` (dialing
now) or `202` `{call_id, queue_id, position, status:"queued"}` when the call
queue is on — the call is then dialed shortly at the account CPS limit. Treat
any `2xx` as success, capture `call_id`, and follow the status/SSE below;
`call_sid` is `null` until it actually dials. Put EVERY detail the bot might
need into the `brief` (name, date, time, party size, guest contact phone) so
it never has to guess or ask.

### 2. Book a restaurant (structured) — `POST /skills/restaurant-reservation/run`
Validated input + idempotency. Send a fresh `Idempotency-Key` header (a
UUID) per real attempt — replaying the same key returns the stored result
instead of dialing again.
```sh
curl -s -X POST https://dev.voygr.tech/skills/restaurant-reservation/run \
  -H "X-API-Key: $AI_CALL_AGENT_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(python -c 'import uuid;print(uuid.uuid4())')" \
  -d '{"restaurant_phone":"+15551234567","party_size":2,"date":"2026-05-30","time":"20:00","name":"Alex","phone_to_dictate":"+15551230000","language":"ru"}'
```
Fields: `restaurant_phone`, `party_size`, `date` (`YYYY-MM-DD`), `time`
(`HH:MM`), `name`, `phone_to_dictate`, `language`.

> **⚠ ALWAYS pass `phone_to_dictate` — the GUEST's contact phone.**
> Restaurants routinely ask for a callback number to confirm a booking.
> `restaurant_phone` is the number we DIAL; `phone_to_dictate` is the number
> the bot reads back to staff — they are DIFFERENT. If you omit it, the bot
> has no number to give and the booking fails (it has been seen dictating
> the restaurant's OWN number, or making digits up, and staff reject it).
> **If the user did not give a contact number, ASK for it BEFORE calling.**

### 3. Cancel a booking — `POST /skills/cancel-booking/run`
Calls the venue to cancel an existing booking you hold.
```sh
curl -s -X POST https://dev.voygr.tech/skills/cancel-booking/run \
  -H "X-API-Key: $AI_CALL_AGENT_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(python -c 'import uuid;print(uuid.uuid4())')" \
  -d '{"booking_id":"<id from the list below>","language":"ru"}'
```
Errors: `404 booking_not_found`, `409 booking_not_cancellable` (only
`active` bookings can be cancelled — see Pitfalls for the `past` workaround).

### 4. List bookings — `GET /v1/booking/bookings`
```sh
curl -s -H "X-API-Key: $AI_CALL_AGENT_API_KEY" \
  "https://dev.voygr.tech/v1/booking/bookings?status=active"
```
Optional `?status=active|pending_user_confirm|past|cancelled&limit=N`.
A successful call (free-form OR structured) auto-creates a booking row here
on outcome `success_booked`, so the user can list and cancel it.

### 5. Account / balance — `GET /users/me`
Returns `customer_id`, quota, etc. Good for a connectivity / auth check.

### 6. Call status — `GET /calls/{call_id}`
Returns the `CallDTO`: `status`, `outcome_type`, `summary`, and
`transcript_full`. Does NOT surface a pending `ask_user` — for that, follow
the event stream (next section).

## Follow the call — REQUIRED (poll the event stream; do NOT hold it open)

The event stream is how the bot reaches YOU mid-call (`ask_user`) and how
you learn the result (`outcome`). You MUST follow it: an unanswered
`ask_user` falls back to another channel after ~60s and you never see it.
No webhook is needed.

**Do NOT use a long-lived `curl -N` stream.** SSE lines are tiny and get
stuck in the subprocess pipe buffer, so a backgrounded `curl -N` reads them
too late. **Instead, POLL** the same endpoint with a `Last-Event-ID` header
and a short `--max-time`: each poll backfills every event newer than the id
you pass (from a durable store — nothing is missed, any replica works), then
exits and flushes the buffer.

**Poll loop — run as ONE shell command; it returns the moment the bot asks
something or the call ends:**
```sh
ID=<call_id>; LAST=0; STOP=$(($(date +%s)+90))
while [ "$(date +%s)" -lt "$STOP" ]; do
  OUT=$(curl -s --max-time 5 -H "X-API-Key: $AI_CALL_AGENT_API_KEY" \
        -H "Last-Event-ID: $LAST" "https://dev.voygr.tech/calls/$ID/events")
  [ -n "$OUT" ] && echo "$OUT"
  N=$(printf '%s' "$OUT" | sed -n 's/^id: //p' | tail -1); [ -n "$N" ] && LAST=$N
  printf '%s' "$OUT" | grep -q '^event: ask_user' && { echo "### ASK_USER — answer now ###"; break; }
  printf '%s' "$OUT" | grep -q '^event: outcome'  && { echo "### OUTCOME — done ###"; break; }
  sleep 1
done
echo "LAST=$LAST"
```
Then:
- `### ASK_USER ###` → read `request_id` + `message` from the `data:` JSON,
  get the answer (ask the human if you don't know it), POST it (below), then
  run the loop AGAIN with `LAST=` the printed value, to wait for the outcome.
- `### OUTCOME ###` → report `outcome_type` + `summary`; done.

Event types (in the `data:` JSON):
- `status_change` — queued → dialing → in_progress → … (info only).
- `ask_user` — `data` has `request_id`, `message`, `answer_url`. Answer
  fast: the bot waits ~60s, then the question is lost to a fallback channel.
- `answered` — your answer was delivered (same `request_id`).
- `outcome` — **TERMINAL.** `data` has `outcome_type` (`success_booked` /
  `success_no_booking` / `success_refused` / `failed_*`) + `summary`.
- `recording_ready` — a recording URL is available.

### Answer a mid-call question — `POST /calls/{call_id}/answer`
```sh
curl -s -X POST https://dev.voygr.tech/calls/<call_id>/answer \
  -H "X-API-Key: $AI_CALL_AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"request_id":"<from the ask_user data>","answer":"<your answer, in the call language>"}'
```

## Canonical flow

**Pre-call checklist (restaurant bookings)** — gather (or ASK the user for)
ALL of these before calling, and pass them in:
- Guest name.
- Date and time.
- Party size.
- **Guest contact phone — REQUIRED.** ASK if not given; do NOT call without
  it. Pass as `phone_to_dictate` (structured) or in the `brief` (free-form).
- Credit-card willingness (some venues require it; if none, say so upfront
  in the brief so the bot can negotiate early instead of stalling).

1. Place the call (`POST /calls` or a skill); capture `call_id`.
2. Immediately run the poll loop above.
3. On `ask_user` → answer via `POST /calls/<id>/answer` with the
   `request_id`, then re-run the loop (with the printed `LAST`) for the
   outcome.
4. On `outcome`: if NOT `success_booked`, fetch `GET /calls/<call_id>` and
   read `transcript_full` — the venue may have accepted even when the code
   says otherwise. Report the transcript reality, not just the code.
5. Report `outcome_type` + `summary` (+ key transcript excerpts) so the user
   can judge. For a booking, confirm it appears in
   `GET /v1/booking/bookings`.

## Errors
JSON `{"detail":{"error":"...",...}}` with the HTTP status:
`402 quota_exceeded`, `409 concurrent_call_not_allowed` /
`booking_not_cancellable`, `422` validation, `503 maintenance`.

## Pitfalls (learned the hard way)

1. **Put ALL details in the `brief` / fields up front.** The bot can only
   say what it was given; if it lacks a detail it may ask (`ask_user`) or, in
   the worst case, guess. Redundancy is cheap.

2. **Pass the guest name explicitly, and verify it in the result.** The
   voice bot occasionally mis-says the guest name mid-call. The SAVED booking
   uses the name from YOUR request (not the transcript), so the stored record
   is correct — but if the operator heard the wrong name, the venue's own
   record may differ. State the name clearly and, if it matters, check the
   transcript.

3. **Don't trust `success_no_booking` blindly.** It means the system did not
   detect a clear confirmation — not necessarily that the booking failed.
   Always read `transcript_full` before reporting failure.

4. **Windows / MSYS curl + Cyrillic JSON:** inline `curl -d '{...}'` with
   Cyrillic can corrupt the JSON on the way to the server (server sees an
   empty body → "Field required"). **Fix:** write the JSON to a file first,
   then `curl -d @payload.json`.

5. **`Last-Event-ID` is exclusive (`>` not `>=`).** Passing the last id you
   saw returns only NEWER events. If you ever see duplicates, bump `LAST` by 1.

6. **Cancelling a `past` booking:** `cancel-booking` only works on `active`
   bookings; a `past` one returns `409 booking_not_cancellable`. Workaround:
   place a free-form `POST /calls` with a brief like "Cancel the reservation
   under <name> on <date> at <time>" — the venue cancels it regardless of the
   API's status.
