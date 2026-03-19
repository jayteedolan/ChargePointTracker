# ChargePointTracker

A self-hosted FastAPI app that monitors a ChargePoint EV charging station and sends push notifications via [ntfy.sh](https://ntfy.sh) when a port becomes available.

## Features

- Polls your ChargePoint station on a configurable interval (default: 2 minutes)
- **Watch mode** — enable from the web UI to get an instant push notification the moment a port opens up
- Hourly reminders while watch mode is active and no ports are free
- Manual refresh button to trigger an immediate poll
- Lightweight web dashboard showing port status and watch mode controls
- Persistent state via SQLite (survives restarts)

## How It Works

1. The app authenticates with ChargePoint using your account credentials
2. A background scheduler polls the station at the configured interval
3. Port status is stored in a local SQLite database
4. When watch mode is active and a port becomes available, a notification is pushed to your ntfy.sh topic
5. The ntfy notification includes an action button to stop watch mode remotely

## Setup

### Prerequisites

- Python 3.11+
- A ChargePoint account with access to a specific station
- An [ntfy.sh](https://ntfy.sh) topic (pick a long random name — it acts as a shared secret)

### Install

```bash
git clone https://github.com/jayteedolan/ChargePointTracker.git
cd ChargePointTracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

| Variable | Description |
|---|---|
| `CHARGEPOINT_USERNAME` | Your ChargePoint account email |
| `CHARGEPOINT_PASSWORD` | Your ChargePoint account password |
| `CHARGEPOINT_STATION_ID` | Integer station ID from the ChargePoint URL |
| `NTFY_TOPIC` | Your ntfy.sh topic name (long random string recommended) |
| `NTFY_URL` | ntfy server URL (default: `https://ntfy.sh`) |
| `APP_PORT` | Port the web app listens on (default: `8080`) |
| `POLL_INTERVAL_SECONDS` | How often to poll ChargePoint (default: `120`) |
| `PI_HOST` | Your Pi's LAN IP or DDNS hostname (used in ntfy action button URLs) |

### Run

```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8765
```

Open `http://localhost:8765` in your browser.

### Run as a systemd service (Raspberry Pi)

A sample service file is included:

```bash
sudo cp chargepoint_monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chargepoint_monitor
```

## API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/status` | Current port status and watch mode state |
| `POST` | `/api/refresh` | Trigger an immediate poll |
| `POST` | `/api/watch` | Enable or disable watch mode `{"enabled": true}` |
| `POST` | `/api/watch/acknowledge` | Stop watch mode (used by ntfy action button) |
| `GET` | `/api/health` | Health check with uptime |
