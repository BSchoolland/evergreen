# Status Graph

Send an uptime status graph to Discord when someone asks for one.

Generate it — `python3 scripts/uptime_graph.py [days]` prints the path to a PNG: a
GitHub-status-style chart of every monitored project's uptime. Then post it:

```
python3 scripts/discord_send.py --image <path> --channel <id> --no-wait "<short caption>"
```

Use the channel from `/read-config-evergreen` (a project's alert channel, or the instance
default).

`discord_send.py --image` uploads any local image, so you can answer other visual requests
the same way — build whatever PNG makes sense (e.g. response times for one project) and
upload it.
