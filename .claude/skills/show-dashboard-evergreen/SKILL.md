# Show Dashboard

Start the Evergreen dashboard web server and provide the URL.

## What to do

1. Check if the dashboard is already running:

```bash
lsof -t -i:8080 2>/dev/null
```

2. If not running, start it in the background:

```bash
cd /home/ben/Projects/evergreen && python3 scripts/dashboard.py &
```

3. Get the Tailscale IP so the user can access it from another device:

```bash
tailscale ip -4
```

4. Tell the user:
   - Local URL: `http://localhost:8080`
   - Tailscale URL: `http://<tailscale-ip>:8080`
   - To stop: `kill $(lsof -t -i:8080)`

If port 8080 is already in use by something else, set a different port:

```bash
EVERGREEN_DASHBOARD_PORT=8081 python3 scripts/dashboard.py &
```
