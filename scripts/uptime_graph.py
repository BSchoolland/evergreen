#!/usr/bin/env python3
"""Render a GitHub-status-style uptime graph (PNG) for all monitored projects.

Design ported from Ducktape's statusGraph: a dark card per project with a row of
color-coded time slots (up / partial / down / no-data), a status dot, and an uptime
percentage. Reads evergreen's uptime_checks. Prints the output PNG path on success.

Usage: uptime_graph.py [days]   (default 7)
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

from evergreen.db import epoch, get_connection, list_projects

C = {
    "background": "#0d1117", "card": "#161b22", "textPrimary": "#e6edf3",
    "textSecondary": "#8b949e", "textMuted": "#6e7681",
    "up": "#b1cd32", "upBright": "#2ea043", "down": "#da3633",
    "downBright": "#f85149", "partial": "#d29922", "noData": "#21262d",
}
WIDTH, ROW, PAD, BAR_H, SLOTS, GAP = 1000, 90, 40, 24, 84, 2
HEADER, FOOTER = 80, 90


def slot_color(checks, ups):
    if checks == 0:
        return C["noData"]
    r = ups / checks
    if r >= 1: return C["upBright"]
    if r >= 0.8: return C["up"]
    if r >= 0.5: return C["partial"]
    if r > 0: return C["down"]
    return C["downBright"]


def badge_color(pct):
    if pct >= 99.9: return C["upBright"]
    if pct >= 99: return C["up"]
    if pct >= 95: return C["partial"]
    return C["down"]


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    projects = [p for p in list_projects(include_inactive=False) if p.get("base_url")]
    if not projects:
        sys.exit("No monitored projects (none have a base_url).")

    now = epoch()
    start = now - days * 86400
    span = (now - start) / SLOTS
    conn = get_connection()

    height = HEADER + len(projects) * ROW + FOOTER
    fig = plt.figure(figsize=(WIDTH / 100, height / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, WIDTH)
    ax.set_ylim(height, 0)  # invert y so we can lay out top-down like a canvas
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), WIDTH, height, facecolor=C["background"], edgecolor="none"))
    ax.text(PAD, 44, f"Uptime Status — Last {days} Days", color=C["textPrimary"],
            fontsize=18, fontweight="bold", va="center")

    bar_x, bar_w = PAD + 8, WIDTH - 2 * PAD - 16
    slot_w = (bar_w - (SLOTS - 1) * GAP) / SLOTS

    for i, p in enumerate(projects):
        y = HEADER + i * ROW
        rows = conn.execute(
            "SELECT checked_at, is_up FROM uptime_checks WHERE project_id = ? AND checked_at >= ? ORDER BY checked_at",
            (p["id"], start),
        ).fetchall()
        total = len(rows)
        ups = sum(1 for _, u in rows if u)
        pct = (ups / total * 100) if total else None

        buckets = [[0, 0] for _ in range(SLOTS)]
        for ts, u in rows:
            j = int((ts - start) / span)
            if 0 <= j < SLOTS:
                buckets[j][0] += 1
                buckets[j][1] += 1 if u else 0

        # card
        ax.add_patch(Rectangle((PAD - 8, y), WIDTH - 2 * PAD + 16, ROW - 8,
                               facecolor=C["card"], edgecolor="none"))
        # status dot + name
        cur = rows[-1][1] if rows else None
        dot = C["upBright"] if cur else C["downBright"] if cur is not None else C["textMuted"]
        ax.add_patch(Circle((PAD + 6, y + 16), 5, facecolor=dot, edgecolor="none"))
        ax.text(PAD + 20, y + 16, p["name"], color=C["textPrimary"], fontsize=11,
                fontweight="bold", va="center")
        if cur is not None:
            ax.text(PAD + 20, y + 30, "UP" if cur else "DOWN", color=C["textSecondary"],
                    fontsize=8, va="center")
        # uptime % (right-aligned, color-coded)
        btxt = f"{pct:.1f}%" if pct is not None else "N/A"
        ax.text(WIDTH - PAD, y + 18, btxt, color=badge_color(pct or 0), fontsize=12,
                fontweight="bold", va="center", ha="right", family="monospace")

        # slot bar
        for j in range(SLOTS):
            x = bar_x + j * (slot_w + GAP)
            ax.add_patch(Rectangle((x, y + 36), slot_w, BAR_H,
                                   facecolor=slot_color(*buckets[j]), edgecolor="none"))

    # day axis labels
    axis_y = HEADER + len(projects) * ROW + 22
    for d in range(days + 1):
        x = bar_x + (d / days) * bar_w
        dt = datetime.fromtimestamp(start + d * 86400, tz=timezone.utc)
        label = "Now" if d == days else dt.strftime("%b %d")
        ha = "right" if d == days else ("left" if d == 0 else "center")
        ax.text(x, axis_y, label, color=C["textMuted"], fontsize=8, va="center", ha=ha)

    # legend
    legend = [("Up", C["upBright"]), ("Partial", C["partial"]),
              ("Down", C["downBright"]), ("No Data", C["noData"])]
    lx, ly = PAD, axis_y + 30
    for label, color in legend:
        ax.add_patch(Rectangle((lx, ly - 6), 12, 12, facecolor=color, edgecolor="none"))
        ax.text(lx + 18, ly, label, color=C["textSecondary"], fontsize=9, va="center")
        lx += 18 + len(label) * 7 + 28

    out = Path("/tmp") / f"uptime-{now}.png"
    fig.savefig(out, facecolor=C["background"])
    plt.close(fig)
    print(out)


if __name__ == "__main__":
    main()
