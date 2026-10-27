# Machine Panel

A small self-hosted dashboard that shows which of your machines are online and lets you shut each one down remotely.

Each machine runs a tiny **agent** that sends a heartbeat to a **FastAPI** backend on a VPS. A **React** dashboard reads the status and queues commands. Agents always connect *out* to the server, so nothing on your home network needs port forwarding, dynamic DNS, or exposure to the internet.

## How it works

```
 Home machines                        VPS
┌──────────────┐   heartbeat    ┌──────────────────────────────────┐
│ agent.py     │ ─────────────▶ │ Caddy (HTTPS)                    │
│ (per machine)│ ◀───────────── │  ├─ /api/*  ─▶ FastAPI + SQLite  │
└──────────────┘ command reply  │  └─ /*      ─▶ React static files│
                                └──────────────────────────────────┘
                                              ▲
                                  Browser: dashboard (you)
```

1. Every ~15 seconds each agent sends `POST /api/heartbeat` with its own bearer token.
2. The server records `last_seen`. A machine counts as **online** if it was seen in the last 45 seconds.
3. Clicking **Shut down** in the dashboard stores a pending command for that machine.
4. On its next heartbeat, the agent receives the command in the response and runs it.

Commands are delivered exactly once, and queued commands expire after 5 minutes, so a shutdown queued while a machine was off won't fire when it boots later.

## Features

- Live online/offline status, refreshed every 5 seconds
- Remote shutdown with a confirm step to prevent misclicks
- One unique token per machine (only a SHA-256 hash is stored)
- Agents only run commands from a hardcoded whitelist, never arbitrary strings from the server
- Works on Linux and Windows agents
- SQLite storage in a single file, no database server needed
- `DRY_RUN` mode for testing agents without powering anything off

## Repository layout

```
.
├── backend/
│   ├── main.py            # FastAPI app
│   ├── requirements.txt
│   └── Dockerfile
├── agent/
│   ├── agent.py           # runs on each machine
│   └── requirements.txt
├── panel-ui/              # React (Vite) dashboard
│   └── src/
│       ├── App.jsx
│       └── App.css
└── README.md
```

## Requirements

- A VPS with a domain or subdomain pointing at it (for HTTPS)
- Docker and Docker Compose on the VPS (the examples use Caddy in Docker)
- Python 3.10+ on each machine that runs an agent
- Node.js 18+ on your development machine to build the dashboard

## Quick start (local development)

**Backend**

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export ADMIN_KEY="$(openssl rand -base64 32)"   # save this, it's your login
echo "$ADMIN_KEY"
uvicorn main:app --reload
```

The API starts on `http://localhost:8000`. It refuses to start if `ADMIN_KEY` is not set. Interactive API docs are at `/docs`.

**Dashboard**

```bash
cd panel-ui
npm install
npm run dev
```

The Vite dev server proxies `/api` to `localhost:8000` (see `vite.config.js`):

```js
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
```

Open the URL Vite prints and enter your admin key.

## Registering a machine and running its agent

**1. Register the machine** (any computer that can reach the API):

```bash
curl -X POST https://panel.example.com/api/machines \
  -H "X-Admin-Key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"home-server"}'
```

The response contains the machine's token:

```json
{"name": "home-server", "token": "k3J9...long-random-string"}
```

> **Copy the token now.** Only its hash is stored, so it cannot be shown again. If you lose it, remove the machine and register it again under a new name or token.

**2. Install the agent on that machine:**

```bash
cd agent
pip install -r requirements.txt
```

**3. Test in dry-run mode** (logs what it *would* do, never shuts down):

```bash
export PANEL_URL=https://panel.example.com
export PANEL_TOKEN=k3J9...long-random-string
export DRY_RUN=1
python agent.py
```

Within about 15 seconds the machine shows as online in the dashboard. Queue a shutdown from the dashboard and the agent logs `DRY RUN: would run [...]`.

**4. Go live:** remove `DRY_RUN` and set the agent up to start at boot.

### Agent environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PANEL_URL` | yes | | Base URL of your panel, no trailing slash |
| `PANEL_TOKEN` | yes | | This machine's token from registration |
| `HEARTBEAT_INTERVAL` | no | `15` | Seconds between heartbeats |
| `DRY_RUN` | no | off | `1`/`true` logs commands instead of running them |

### Running the agent at startup

**Linux (systemd).** Create `/etc/panel-agent.env` (`chmod 600`):

```
PANEL_URL=https://panel.example.com
PANEL_TOKEN=your-token
```

Create `/etc/systemd/system/panel-agent.service`:

```ini
[Unit]
Description=Panel heartbeat agent
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/panel-agent.env
ExecStart=/usr/bin/python3 /opt/panel-agent/agent.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now panel-agent
```

Running as root lets `shutdown` work without extra setup. To run as a normal user instead, change `User=` and add a narrow sudoers rule with `visudo`:

```
youruser ALL=(ALL) NOPASSWD: /sbin/shutdown
```

If you use a virtualenv for `requests`, point `ExecStart` at the venv's `python` instead of `/usr/bin/python3`.

**Windows.** Install Python and `pip install requests`. Set `PANEL_URL` and `PANEL_TOKEN` as user environment variables, then create a Task Scheduler task that runs `pythonw.exe C:\path\to\agent.py` at logon (`pythonw` avoids a console window).

## Deploying the server (Docker + Caddy)

**1. DNS.** Create an A record for your subdomain (for example `panel.example.com`) pointing at the VPS. Make sure ports 80 and 443 are reachable. Caddy needs them to obtain a certificate.

**2. Backend image.** `backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

RUN useradd --system app && mkdir /data && chown app /data
USER app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**3. Compose service.** Add to the compose file that runs your Caddy container. The API publishes no ports, so it is only reachable through Caddy:

```yaml
services:
  panel-api:
    build: ./backend
    restart: unless-stopped
    environment:
      ADMIN_KEY: ${PANEL_ADMIN_KEY}
      DB_PATH: /data/panel.db
    volumes:
      - panel-data:/data
    # must share a Docker network with your Caddy container

  caddy:
    # ...your existing Caddy config...
    volumes:
      - /var/www/panel:/srv/panel:ro

volumes:
  panel-data:
```

Put the secret in a `.env` file next to the compose file (never commit it):

```
PANEL_ADMIN_KEY=your-long-random-string
```

**4. Caddyfile.**

```
panel.example.com {
    encode gzip zstd

    handle /api/* {
        reverse_proxy panel-api:8000
    }

    handle {
        root * /srv/panel
        try_files {path} /index.html
        file_server
    }
}
```

`panel-api` must match the compose service name.

**5. Build and upload the dashboard.**

```bash
cd panel-ui
npm run build
rsync -avz --delete dist/ youruser@your-vps:/var/www/panel/
```

**6. Start it.**

```bash
docker compose up -d --build panel-api
docker compose up -d caddy
```

**7. Check it.**

```bash
curl https://panel.example.com/api/machines -H "X-Admin-Key: YOUR_ADMIN_KEY"
```

An empty list (`[]`) means the whole chain works. To update later, rebuild and `rsync` the frontend, or run `docker compose up -d --build panel-api` after backend changes. The `panel-data` volume keeps your machines and tokens across rebuilds.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser shows nothing / certificate errors | DNS not pointing at the VPS, or ports 80/443 blocked. Check `docker logs caddy` |
| Certificate warning in the browser | Caddy is using Let's Encrypt **staging**. Remove any `acme_ca` staging line from the Caddyfile |
| API returns 502 | Caddy can't reach `panel-api`: wrong service name or different Docker networks |
| Page is blank or 404 | Frontend files missing or the volume path doesn't match `root * /srv/panel` |
| Timeouts on 80/443 with ufw + Docker | Forward rules tied to the Caddy container's IP went stale after it was recreated. Re-add rules for its current IP, or pin its IP in compose |
| `401` from the API | Wrong admin key, or the container was not recreated after changing `.env` |
| Agent logs "token rejected" | Typo in `PANEL_TOKEN`, or the machine was never registered |
| Shutdown does nothing on Linux | Agent isn't root and has no sudoers rule. Check the agent's logs |

## API reference

Admin endpoints require an `X-Admin-Key` header. Agent endpoints require `Authorization: Bearer <machine token>`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/machines` | admin | Register a machine `{"name": "..."}`. Returns its token once |
| `GET` | `/api/machines` | admin | List machines with `online`, `last_seen`, `seconds_since_seen`, `pending_cmd` |
| `POST` | `/api/machines/{name}/command` | admin | Queue a command `{"command": "shutdown"}` |
| `POST` | `/api/heartbeat` | machine token | Record a heartbeat. Returns `{"command": "shutdown"}` or `{"command": null}` |

### Server configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `ADMIN_KEY` | yes | | Master key for admin endpoints. The app won't start without it |
| `DB_PATH` | no | `panel.db` | Path to the SQLite file |

Tunable constants at the top of `main.py`: `ONLINE_WINDOW` (45s), `COMMAND_TTL` (300s), and `ALLOWED_COMMANDS`.

## Security notes

This panel can power off your machines from the internet, so treat it accordingly.

- **Always use HTTPS.** The admin key and tokens travel in request headers.
- **Use a long random admin key** (32+ characters). Anyone with it has full control.
- **Machine tokens are isolated.** A stolen agent token can only send heartbeats for that one machine. It cannot queue commands or read other machines.
- **Commands are whitelisted on both sides.** The server rejects unknown commands, and the agent only runs functions in its own `COMMANDS` table.
- **The API is not published to the host.** Only Caddy can reach it.
- **Don't put Caddy `basicauth` in front of `/api/*`.** It uses the same `Authorization` header as the agents' bearer tokens and will break heartbeats. If you want an extra layer, protect only the static-file block.
- **The dashboard stores the admin key in `sessionStorage`** for the length of the browser tab. That is acceptable for a single-user setup with no third-party scripts, but a login-cookie flow is the better long-term design (see roadmap).
- **Never commit** `.env`, `panel.db`, or tokens.

## Limitations

- Single shared admin key, no user accounts or audit log
- Commands are delivered once and not acknowledged. If a shutdown fails on the machine, check the agent's logs and click again
- Shutdown latency is up to one heartbeat interval (polling, not push)
- Offline machines can't be commanded, since queued commands expire after 5 minutes

## Roadmap

- [ ] Login with a session cookie (`HttpOnly`, `Secure`, `SameSite=Strict`) instead of the admin key in the browser
- [ ] "Register machine" form in the dashboard
- [ ] Token rotation and machine deletion endpoints
- [ ] More commands: restart, sleep, lock
- [ ] Command acknowledgements and retries
- [ ] Wake-on-LAN relay through an always-on machine
- [ ] CPU, RAM, and uptime in the heartbeat
- [ ] WebSocket connection for instant commands


