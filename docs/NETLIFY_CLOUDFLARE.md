# Netlify frontend with local DeepSeek through Cloudflare Tunnel

The deployment path is:

```text
Browser at https://campusfizz.netlify.app
  -> HTTPS Cloudflare Tunnel URL
  -> FastAPI at http://127.0.0.1:8000 on the teammate machine
  -> Ollama at http://localhost:11434/api/chat on the same machine
```

Supabase stays hosted. Its frontend URL/publishable key and the existing bearer
JWT authentication flow stay the same. Only FastAPI port 8000 is published.
Keep Ollama bound to loopback, do not publish port 11434, and do not configure
router port forwarding for either service.

Commands below use Windows PowerShell. Run them on the teammate machine, not on
Netlify. Prerequisites: Python 3.11+, Ollama, the `deepseek-r1:8b` model, and
[cloudflared installed and available on PATH](https://developers.cloudflare.com/tunnel/downloads/).
The local host must remain awake and connected while the backend is in use.

## 1. Configure the private backend environment

From the checked-out repository root:

```powershell
Set-Location backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt -c requirements.lock.txt
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
notepad .env
```

Create the virtual environment only once; reuse it on subsequent starts. Set
these values in `backend/.env`, together with the existing private Supabase
server configuration:

```dotenv
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_KEY
SUPABASE_SERVICE_ROLE_KEY=YOUR_PRIVATE_SERVER_KEY
AI_CLASSIFICATION_ENABLED=true
CORS_ORIGINS=https://campusfizz.netlify.app,http://localhost:5173
CORS_ALLOW_LOCALHOST=true
APP_URL=https://campusfizz.netlify.app
OLLAMA_URL=http://localhost:11434/api/chat
OLLAMA_MODEL=deepseek-r1:8b
OLLAMA_TIMEOUT_SECONDS=180
```

Replace placeholders locally. Never commit `.env`, service-role keys, SMTP
passwords, or tunnel credentials. No server key belongs in a `VITE_*` variable.
`APP_URL` is the frontend URL for ticket links, not the tunnel URL. CORS contains
frontend origins, not API paths or tunnel URLs. Use exact origins without a
trailing slash; Netlify preview domains need separate explicit entries if used.
Restart FastAPI after changing its environment or backend code. The restricted
loopback regex also accepts HTTP localhost/127.0.0.1 development ports such as
5174, so Vite port changes need no new allowlist entry. `CORS_ALLOW_LOCALHOST`
defaults to true; for a production-only API, set it false and remove explicit
local origins while retaining the exact Netlify origin. No wildcard is used.
See [the local presentation workflow](LOCAL_DEMO.md) for a demo without a tunnel.

## 2. Start Ollama locally

Terminal 1:

```powershell
$env:OLLAMA_HOST = "127.0.0.1:11434"
ollama serve
```

If the Ollama Windows tray app is already serving on loopback port 11434, use
that running server instead of starting a second one. To use the explicit
terminal-managed server above, quit the tray app first.

In another terminal, download the model if needed and optionally warm it:

```powershell
ollama pull deepseek-r1:8b
ollama run deepseek-r1:8b "Reply with OK."
ollama ps
```

Only FastAPI calls this local Ollama API. Neither Netlify nor the browser needs
an Ollama URL, Ollama CORS setting, or direct access to port 11434.

## 3. Start FastAPI

Terminal 2, with the current directory set to `backend`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a separate terminal verify local startup:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

`ai_enabled` reports configuration, not a live model inference check. With the
server key configured and classification enabled, it should be true. This setup
preserves classification, routing, enrichment, and emergency behavior.

## 4A. Quick tunnel: temporary URL for demos

Terminal 3:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Copy the printed `https://...trycloudflare.com` URL. It forwards to FastAPI only.
The hostname changes each time a new quick tunnel starts, and the URL stops
working when its process stops. Quick tunnels are for development/testing.
A quick tunnel does not require your own domain or a Cloudflare account.

After every URL change, update Netlify's `VITE_API_URL` and rebuild/redeploy the
frontend. Do not point this command at port 11434. If quick tunnels fail because
an existing default `.cloudflared/config.yml` or `config.yaml` is loaded, use a
separate quick-tunnel environment without that default configuration; preserve
existing credentials rather than deleting them.

## 4B. Named tunnel: stable hostname

Use this instead of 4A for a persistent deployment address. Prerequisites: a
Cloudflare account and a domain whose DNS is managed by Cloudflare. You cannot
create a custom API subdomain beneath the Netlify-owned `netlify.app` domain.
Use a hostname on a domain you control, such as `api.your-domain.com`.

One-time setup:

```powershell
cloudflared tunnel login
cloudflared tunnel create campusfix-api
```

The create command prints a tunnel UUID and the path of its credential JSON.
Keep the account certificate and credential JSON in the user's `.cloudflared`
directory outside the repository. Reuse this tunnel instead of creating it
again on each start.

Set the following two values to the real UUID and your chosen hostname, then
create a local configuration file outside the repository:

```powershell
$tunnelId = "REPLACE_WITH_THE_PRINTED_TUNNEL_UUID"
$apiHostname = "api.your-domain.com"
$cfConfig = Join-Path $env:USERPROFILE ".cloudflared\campusfix.yml"
$cfCredentials = (Join-Path $env:USERPROFILE ".cloudflared\$tunnelId.json").Replace("\", "/")
@"
tunnel: $tunnelId
credentials-file: '$cfCredentials'
ingress:
  - hostname: $apiHostname
    service: http://127.0.0.1:8000
  - service: http_status:404
"@ | Set-Content -LiteralPath $cfConfig -Encoding utf8
cloudflared tunnel --config $cfConfig ingress validate
cloudflared tunnel route dns campusfix-api $apiHostname
cloudflared tunnel --config $cfConfig run campusfix-api
```

Only the selected hostname forwards to FastAPI port 8000. Other hostnames get
404. No ingress rule publishes Ollama. The URL is `https://api.your-domain.com`
with your actual chosen domain, and stays the same across process restarts.
To restart the named connector later:

```powershell
$cfConfig = Join-Path $env:USERPROFILE ".cloudflared\campusfix.yml"
cloudflared tunnel --config $cfConfig run campusfix-api
```

A stable hostname does not keep the teammate machine online: Ollama, FastAPI,
and cloudflared must all remain running. Do not commit the credential JSON,
account certificate, tunnel tokens, or a filled-in private tunnel config.

## 5. Configure Netlify and redeploy

In the existing `campusfizz` site's environment variables, set this for the
production build context, with **Builds** scope:

```dotenv
VITE_API_URL=<HTTPS tunnel URL>
```

Quick example: `VITE_API_URL=https://YOUR_GENERATED_NAME.trycloudflare.com`.
Named example: `VITE_API_URL=https://api.your-domain.com`.
Replace examples with the real hostname. Use the base HTTPS URL only: no `/api`,
no `/api/health`, no trailing slash, no Ollama port, and no `localhost`.

For the existing Vite app, Netlify's base directory should be `frontend`, the
build command `npm run build`, and the publish directory `dist` relative to that
base. Retain the existing `VITE_SUPABASE_URL` and
`VITE_SUPABASE_PUBLISHABLE_KEY`. They remain pointed at hosted Supabase.

Trigger a new production deploy after saving the variable. Vite embeds
`VITE_API_URL` at build time, so changing a Netlify variable alone does not
update an already deployed JavaScript bundle. A visitor's `localhost` refers
to their own machine, not the teammate backend.

## 6. Verify the public route and CORS

On a machine with Internet access, replace the base URL below:

```powershell
$apiBase = "https://YOUR_ACTUAL_TUNNEL_HOSTNAME"
Invoke-RestMethod "$apiBase/api/health"
foreach ($origin in @("https://campusfizz.netlify.app", "http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173", "http://127.0.0.1:5174")) {
  curl.exe -i -X OPTIONS "$apiBase/api/tickets/1/classify" -H "Origin: $origin" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization,content-type"
}
curl.exe -i -X POST "$apiBase/api/tickets/1/classify"
```

All five preflights should return 200 with `Access-Control-Allow-Origin` equal to
the requested allowed origin and permission for authorization/content-type.
The unauthenticated POST should return 401 and must not classify a ticket.
Then sign in at `https://campusfizz.netlify.app` and submit a test ticket. Browser
Network requests should target the HTTPS tunnel URL, not `localhost:8000`.
Confirm final routing/enrichment on the ticket and inspect backend logs.

Classification starts immediately after Supabase confirms ticket creation,
before any optional photo upload. The frontend sends the existing authenticated
POST as a fire-and-forget request: submission does not await AI, and there is no
browser-side classification timeout. DeepSeek can take about 19 seconds when
warm and substantially longer on a cold start; enrichment adds another model
call. Session, network, or tunnel response errors do not display a false
submission failure or undo the saved ticket.

Cloudflare can still impose a proxy response timeout. A lost HTTP response is
not proof that server-side classification failed: inspect the persisted ticket
and backend logs before retrying. The backend saves routing before enrichment;
enrichment failure cannot undo it. The existing 180-second Ollama timeout
applies to each model call, not to ticket creation. This remains a best-effort
browser trigger, not a durable job queue: closing the browser before the
request reaches FastAPI can prevent classification. Keep the tab open for the
initial request. The ticket page polls while routing is pending (bounded to six
minutes) and stops at completion. In the submitting tab, one final refresh when
the POST finishes picks up enrichment; the stored AI explanation is displayed.
Staff can expand a separate original-submission comparison when fields differ.

## References

- [Cloudflare quick tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
- [Cloudflare locally managed named tunnels](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/)
- [Cloudflare configuration files](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)
- [Cloudflare connection limits](https://developers.cloudflare.com/fundamentals/reference/connection-limits/)
- [Ollama server configuration](https://docs.ollama.com/faq)
- [Vite environment variables](https://vite.dev/guide/env-and-mode)
- [Netlify build environment variables](https://docs.netlify.com/build/frameworks/use-environment-variables-with-frameworks/)
