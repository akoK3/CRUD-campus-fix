# Local presentation workflow

Use the current `akoK3/CRUD-campus-fix` checkout, not an older experimental copy.
On this machine the final checkout is `C:\Users\kakij\Downloads\CRUD-campus-fix-final`.
The pre-RAG model/policy and frozen evaluation remain at the documented baseline.

## One-time checks before presenting

- Sign in with an existing account. Confirm you can create/read your own tickets.
  Use an existing authorized staff/admin account to show the original submission.
- Apply the committed `ticket_ai_explanation` and `ticket_original_title`
  migrations through the normal deployment workflow if not already applied.
  This demo fix changes no database schema or permissions.
- Backend: Python 3.11+ virtual environment and pinned requirements installed.
  If needed, from `backend`: `py -3.11 -m venv .venv`, then
  `.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c requirements.lock.txt`.
- Keep the existing private Supabase server configuration in `backend/.env`.
  Do not copy it into the frontend or Git. The backend needs its service-role
  key locally; `/api/health` should report `admin_enabled` and `ai_enabled` true.
- Ollama must have `deepseek-r1:8b` installed (`ollama pull deepseek-r1:8b` once
  if necessary). Keep its API bound to loopback port 11434.

In private `backend/.env`, retain:

```dotenv
OLLAMA_URL=http://localhost:11434/api/chat
OLLAMA_MODEL=deepseek-r1:8b
OLLAMA_TIMEOUT_SECONDS=180
CORS_ORIGINS=https://campusfizz.netlify.app,http://localhost:5173
CORS_ALLOW_LOCALHOST=true
```

`CORS_ALLOW_LOCALHOST` defaults to true, so existing local setups do not need an
extra edit. It adds only anchored HTTP `localhost:<port>` and
`127.0.0.1:<port>` origins. It does not allow arbitrary hostnames, HTTPS local
origins, suffix lookalikes, or `*`. Credentials and bearer/content-type preflight
headers are preserved. For a production-only API, set it false and remove any
explicit local entries from `CORS_ORIGINS`; keep the exact Netlify origin.

In `frontend/.env` use the existing public Supabase configuration plus:

```dotenv
VITE_API_URL=http://localhost:8000
```

Copy `frontend/.env.example` only if no frontend `.env` exists; preserve existing
values. Restart Vite after environment edits. `VITE_*` values are public, so
never place server keys/passwords there. Netlify still needs the HTTPS Cloudflare
FastAPI tunnel URL at build time instead of localhost, followed by a redeploy.

## Start three terminals from the final checkout

Stop an older backend with Ctrl+C first so it releases port 8000 and reloads
these CORS changes. Do not start a second Ollama server if the tray app is already
serving the same port. Each terminal starts from the repository root.

Terminal 1: Ollama already running, or:

```powershell
ollama serve
```

Optional warm-up before the audience arrives, in a separate terminal:

```powershell
ollama run deepseek-r1:8b "Reply with OK."
```

Wait for a successful reply before presenting. If Ollama returns a runner/HTTP
500 error, recover/restart the local Ollama server and warm this same model
again before the demonstration. A healthy `/api/health` only confirms backend
configuration, not model readiness.

Terminal 2:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 3:

```powershell
cd frontend
npm run dev
```

Open the Vite URL actually printed, such as `http://localhost:5173` or
`http://localhost:5174`. The same backend allows both, as well as
`http://127.0.0.1:5173` and `http://127.0.0.1:5174`, without another config edit.
Do not kill an unrelated process merely to force Vite onto 5173.

## Verify and demonstrate

From a fourth terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
curl.exe -i -X OPTIONS http://127.0.0.1:8000/api/tickets/1/classify -H "Origin: http://localhost:5174" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization,content-type"
```

The preflight should return 200 with the requested origin and credentials
allowed. A POST without a valid sign-in token should still return 401. If a
preflight still returns 400, restart the backend from this checkout and check
whether private configuration explicitly sets `CORS_ALLOW_LOCALHOST=false`.

After restarting the backend, submit a new demo ticket (not a frozen holdout
case). An older ticket such as #324 whose POST was blocked is not a queued job;
opening its page does not automatically retry classification.

Use:

- Title: `Bathroom sink clogged`
- Description: `The sink in my dorm bathroom is clogged and the water is draining very slowly.`
- Category: `Not sure - let CampusFix decide`
- Location: choose a real building/location in the form; location validation
  still applies even when category is blank.

The approximate expected result is **Plumbing / Facilities / MEDIUM /
AUTO_ROUTED**. This is a model prediction, not a newly hard-coded demo rule.
Submission remains independent of AI speed. Keep the tab open; the ticket page
quietly refreshes its ticket row every three seconds while routing is `PENDING`.
The routing badge and saved-ticket message make progress visible.

Polling stops at the first non-pending routing status, on a missing/inaccessible
ticket, on navigation/unmount, or after six minutes. Reads never overlap. A
transient read error does not create a false submission error. If the time budget
expires, the page says the ticket is saved and suggests reloading; it does not
mark the ticket failed or automatically classify it again.

Routing is saved before separate enrichment. In the submitting browser tab,
completion of the classification POST triggers one final bounded refresh so
polished title/description and **AI routing explanation** appear even if routing
polling has stopped. This notification is local to that tab, not a WebSocket.
Other tabs opened after routing may need a refresh to see later enrichment.
Comment drafts, action forms, and attachments are not reloaded by background
polling. Staff membership choices follow any department change.

For staff/admin, expand **Original student submission** when the original and
polished fields differ. It shows saved `original_title`/`original_text` verbatim;
legacy missing titles are labeled unavailable. Students do not get this extra
comparison panel. React renders the stored concise explanation as text; no
prompt, chain-of-thought, or model internals are displayed. Enrichment failure
still cannot undo a successful route; an explanation appears only when saved.

For `NEEDS_REVIEW`, explain that staff review is intentional. For `FAILED`, the
ticket remains saved for manual review. Do not change the frozen prompt/model to
force a desired presentation outcome. Do not rerun or tune against holdout cases.

The frontend trigger is best effort, not a durable queue. Closing the tab before
its POST reaches FastAPI can prevent classification. Cloudflare can time out a
response even after the backend saved routing; inspect ticket state before
retrying. For a local-only demo, no Cloudflare Tunnel is required.
