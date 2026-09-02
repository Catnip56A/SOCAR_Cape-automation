---
name: verify-live
description: Use when someone asks to verify a change works, test it live, check whether a fix took effect, or says it's still broken after a fix. Also use right after editing code that needs to actually run. Determines which dev server is serving the user's browser, applies the correct reload path, then proves the change works against real logs, DB, and HTTP.
argument-hint: [what changed, optional]
allowed-tools: Bash, Read, Grep, Glob
---

## What This Skill Does

Closes the loop between "I edited a file" and "the change is provably running." This project can serve the app from **two independent servers at once**, so a change can look broken purely because the reload landed on the server the browser isn't using.

Focus of this run: $ARGUMENTS

## Step 1 — Find out which server is actually live

Never assume. Run both checks:

```bash
docker compose ps --format '{{.Service}}\t{{.Status}}'
curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://localhost:5000/ || echo "not responding"
grep '^LOCAL_DOMAIN=' .env
```

| Result | Server | Browser URL |
|---|---|---|
| `app-dev` + `caddy-dev` up | Docker stack | `https://<LOCAL_DOMAIN>` |
| `:5000` returns a status code | `just dev` (local Flask) | `http://localhost:5000` |
| Both | **Ambiguous — ask the user which URL their browser is on** | — |

Both servers share the same Postgres (`127.0.0.1:5432`) and Redis (`127.0.0.1:6380`), so DB/Redis state looks identical either way. Only the *application code* differs. If both are up and the user hasn't said which they're on, ask before rebuilding anything.

## Step 2 — Apply the correct reload path

`app-dev` has **no source bind-mount** (only `data/*` volumes), so `docker compose restart` never picks up code changes. It must be `--build`.

| What changed | `just dev` (:5000) | Docker `app-dev` |
|---|---|---|
| Python | automatic (`--debug` reloader) | `docker compose up -d --build app-dev` |
| Jinja template | automatic | `docker compose up -d --build app-dev` |
| `.env` | **restart the `just dev` process** (`set dotenv-load` reads it at launch) | `docker compose up -d app-dev` (recreate — plain `restart` keeps old env) |
| `static/` CSS or JS | browser hard-refresh | rebuild + browser hard-refresh |
| Model/schema | `just makemigrations message="..."` then `just migrate` | same |
| Worker/job code | restart `just worker` | `docker compose up -d --build worker-dev` |

Apply the path, then continue to Step 3 — do not report success on the rebuild alone.

## Step 3 — Prove it works

Pick whichever apply. Quote real output; never claim verified without evidence.

```bash
# Server-side errors and log lines
docker compose logs app-dev --tail 100
docker compose logs app-dev --since 10m 2>&1 | grep -i "error\|<relevant term>"
docker compose logs worker-dev --since 10m   # queued/RQ jobs

# Run against a real app context (DB queries, service functions)
docker compose exec -T app-dev python -c "
from lms import create_app
app = create_app()
with app.app_context():
    ...
"

# Redis — always scope to a key pattern, never dump everything
docker compose exec -T redis-dev redis-cli --scan --pattern 'LIMITS:*'

# Real HTTP
curl -sk -o /dev/null -w "%{http_code}" https://<LOCAL_DOMAIN>/<path>
```

For browser-facing changes, note that values interpolated into templates at render time
(e.g. `{{ config.GOOGLE_APP_ID | tojson }}`) are baked into the served HTML — the user needs a
**hard refresh** (Ctrl+Shift+R), and a stale tab will keep using old values indefinitely.

## Step 4 — Lint

Per CLAUDE.md, run on every edited Python file before finishing:

```bash
uv run ruff check <files>
```

## Output

Report back, briefly:
1. Which server is live, and which one the change was applied to
2. What reload path was used
3. The concrete evidence it works — actual command output, not a summary of intent
4. Anything still unverified, stated plainly

## Guardrails

- **Never run `redis-cli FLUSHDB`.** It wipes RQ's job state along with the rate-limit keys — worker registration, scheduler lock, and the recurring jobs (`translation-sweep-recurring`, `embedding-sweep-recurring`, `conversation-purge-recurring`). The embedding sweep does **not** self-heal; recovery needs `docker compose restart worker-dev`. Scope every Redis inspection to a key pattern.
- **Don't rebuild without knowing which server the user is on** when both are running — that's the exact failure this skill exists to prevent.
- **Don't report "fixed" from a clean rebuild.** A successful build proves nothing about behavior.
- **Don't burn paid API calls to verify** (Gemini, DeepL) without checking with the user first — free tiers here are small and quota-exhaustion cascades into unrelated failures.
- If verification fails, say so with the output, and don't start speculative fixes inside this skill — hand the finding back.
