import datetime
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.players import PLAYER_TRACKER, format_duration, parse_timestamp
from core.world_stats import WORLD_STATS

# Curated Viking palette with distinct accents, glowing backgrounds, and avatars
PLAYER_PALETTE: Dict[str, Dict[str, str]] = {
    "Drewfus": {
        "color": "#38bdf8",  # Sky / Cyan
        "bg": "rgba(56, 189, 248, 0.22)",
        "border": "rgba(56, 189, 248, 0.6)",
        "glow": "0 0 10px rgba(56, 189, 248, 0.5)",
        "avatar": "🛡️",
    },
    "Pentone": {
        "color": "#34d399",  # Emerald / Mint
        "bg": "rgba(52, 211, 153, 0.22)",
        "border": "rgba(52, 211, 153, 0.6)",
        "glow": "0 0 10px rgba(52, 211, 153, 0.5)",
        "avatar": "⚔️",
    },
    "Shrub Nubbins": {
        "color": "#fbbf24",  # Amber / Gold
        "bg": "rgba(251, 191, 36, 0.22)",
        "border": "rgba(251, 191, 36, 0.6)",
        "glow": "0 0 10px rgba(251, 191, 36, 0.5)",
        "avatar": "🏹",
    },
    "BiG BeRtHa": {
        "color": "#c084fc",  # Amethyst / Purple
        "bg": "rgba(192, 132, 252, 0.22)",
        "border": "rgba(192, 132, 252, 0.6)",
        "glow": "0 0 10px rgba(192, 132, 252, 0.5)",
        "avatar": "⚡",
    },
    "Redmane": {
        "color": "#fb7185",  # Rose / Ruby
        "bg": "rgba(251, 113, 133, 0.22)",
        "border": "rgba(251, 113, 133, 0.6)",
        "glow": "0 0 10px rgba(251, 113, 133, 0.5)",
        "avatar": "🪓",
    },
}

FALLBACK_PALETTE: List[Dict[str, str]] = [
    {
        "color": "#f472b6",
        "bg": "rgba(244, 114, 182, 0.22)",
        "border": "rgba(244, 114, 182, 0.6)",
        "glow": "0 0 10px rgba(244, 114, 182, 0.5)",
        "avatar": "🗡️",
    },
    {
        "color": "#60a5fa",
        "bg": "rgba(96, 165, 250, 0.22)",
        "border": "rgba(96, 165, 250, 0.6)",
        "glow": "0 0 10px rgba(96, 165, 250, 0.5)",
        "avatar": "🧭",
    },
    {
        "color": "#a3e635",
        "bg": "rgba(163, 230, 53, 0.22)",
        "border": "rgba(163, 230, 53, 0.6)",
        "glow": "0 0 10px rgba(163, 230, 53, 0.5)",
        "avatar": "🌿",
    },
    {
        "color": "#f97316",
        "bg": "rgba(249, 115, 22, 0.22)",
        "border": "rgba(249, 115, 22, 0.6)",
        "glow": "0 0 10px rgba(249, 115, 22, 0.5)",
        "avatar": "🔥",
    },
    {
        "color": "#2dd4bf",
        "bg": "rgba(45, 212, 191, 0.22)",
        "border": "rgba(45, 212, 191, 0.6)",
        "glow": "0 0 10px rgba(45, 212, 191, 0.5)",
        "avatar": "🌊",
    },
]


def get_player_style(name: str, index: int = 0) -> Dict[str, str]:
    """Retrieve player visual styling tokens."""
    if name in PLAYER_PALETTE:
        return PLAYER_PALETTE[name]
    return FALLBACK_PALETTE[index % len(FALLBACK_PALETTE)]


class TimelineEngine:
    """Computes vertical descending timeline chunks with player swimlanes, deaths, and world events."""

    def __init__(self):
        self.lock = threading.RLock()

    def get_earliest_datetime(self) -> datetime.datetime:
        """Find the earliest recorded timestamp across all sessions, events, and bosses."""
        timestamps: List[datetime.datetime] = []

        with PLAYER_TRACKER.lock:
            for s in PLAYER_TRACKER.sessions:
                t1 = parse_timestamp(s.get("login_time", ""))
                if t1:
                    timestamps.append(t1)
            for e in PLAYER_TRACKER.events:
                t = parse_timestamp(e.get("timestamp", ""))
                if t:
                    timestamps.append(t)

        with WORLD_STATS.lock:
            for b in WORLD_STATS.boss_triumphs:
                t = parse_timestamp(b.get("slain_at", ""))
                if t:
                    timestamps.append(t)
            for r in WORLD_STATS.raids_history:
                t = parse_timestamp(r.get("timestamp", ""))
                if t:
                    timestamps.append(t)
            for d in WORLD_STATS.discovered_bosses.values():
                t = parse_timestamp(d.get("discovered_at", ""))
                if t:
                    timestamps.append(t)

        if timestamps:
            return min(timestamps)
        return datetime.datetime.now() - datetime.timedelta(days=3)

    def get_all_players(self) -> List[Dict[str, Any]]:
        """Return structured list of all known players with styles and metrics."""
        summary = PLAYER_TRACKER.get_summary()
        players: List[Dict[str, Any]] = []

        with PLAYER_TRACKER.lock:
            # Count deaths per player
            death_counts: Dict[str, int] = {}
            for e in PLAYER_TRACKER.events:
                if e.get("event") == "death":
                    pname = e.get("player_name", "")
                    death_counts[pname] = death_counts.get(pname, 0) + 1

            for idx, p in enumerate(summary.get("all_players", [])):
                name = p["name"]
                style = get_player_style(name, idx)
                players.append({
                    "name": name,
                    "steam_id": p.get("steam_id", ""),
                    "is_online": p.get("is_online", False),
                    "total_playtime": p.get("total_playtime", "--"),
                    "total_sessions": p.get("total_sessions", 0),
                    "deaths_count": death_counts.get(name, 0),
                    "color": style["color"],
                    "bg": style["bg"],
                    "border": style["border"],
                    "glow": style["glow"],
                    "avatar": style["avatar"],
                })

        return players

    def _get_activity_in_window(
        self, start_dt: datetime.datetime, end_dt: datetime.datetime
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Collect all sessions, events, boss triumphs, raids, and altar discoveries within [start_dt, end_dt]."""
        sessions: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []
        boss_triumphs: List[Dict[str, Any]] = []
        raids: List[Dict[str, Any]] = []
        altars: List[Dict[str, Any]] = []
        now = datetime.datetime.now()

        # 1. Sessions (both historical and currently active)
        with PLAYER_TRACKER.lock:
            for s in PLAYER_TRACKER.sessions:
                t_in = parse_timestamp(s.get("login_time", ""))
                t_out = parse_timestamp(s.get("logout_time") or s.get("login_time", ""))
                if t_in and t_out:
                    if max(start_dt, t_in) < min(end_dt, t_out):
                        sessions.append({
                            "player_name": s["player_name"],
                            "steam_id": s.get("steam_id", ""),
                            "login_dt": t_in,
                            "logout_dt": t_out,
                            "is_active_now": False,
                        })

            # Check currently online players
            for pname, pdata in PLAYER_TRACKER.known_players.items():
                if pdata.get("is_online"):
                    t_in = parse_timestamp(pdata.get("current_session_start", ""))
                    if t_in and max(start_dt, t_in) < min(end_dt, now):
                        sessions.append({
                            "player_name": pname,
                            "steam_id": pdata.get("steam_id", ""),
                            "login_dt": t_in,
                            "logout_dt": now,
                            "is_active_now": True,
                        })

            # 2. Player events (deaths, etc.)
            for e in PLAYER_TRACKER.events:
                t = parse_timestamp(e.get("timestamp", ""))
                if t and start_dt <= t <= end_dt:
                    events.append({
                        "id": e.get("id", ""),
                        "player_name": e.get("player_name", ""),
                        "event": e.get("event", ""),
                        "timestamp_dt": t,
                        "time_str": t.strftime("%H:%M:%S"),
                    })

        # 3. World events (Forsaken triumphs & Raids)
        with WORLD_STATS.lock:
            for b in WORLD_STATS.boss_triumphs:
                t = parse_timestamp(b.get("slain_at", ""))
                if t and start_dt <= t <= end_dt:
                    boss_triumphs.append({
                        "id": b.get("id", "boss"),
                        "name": b.get("name", "Forsaken"),
                        "title": b.get("title", ""),
                        "icon": b.get("icon", "🦌"),
                        "defeat_number": b.get("defeat_number", 1),
                        "world_day": b.get("world_day", 1),
                        "timestamp_dt": t,
                        "time_str": t.strftime("%H:%M:%S"),
                    })

            for r in WORLD_STATS.raids_history:
                t = parse_timestamp(r.get("timestamp", ""))
                if t and start_dt <= t <= end_dt:
                    raids.append({
                        "id": r.get("id", "raid"),
                        "title": r.get("title", "Raid Assault"),
                        "quote": r.get("quote", ""),
                        "biome": r.get("biome", "Midgard"),
                        "icon": r.get("icon", "⚡"),
                        "timestamp_dt": t,
                        "time_str": t.strftime("%H:%M:%S"),
                    })

            # 4. Boss Altar Discoveries (Vegvisir Runestones Revealed)
            for d in WORLD_STATS.discovered_bosses.values():
                t = parse_timestamp(d.get("discovered_at", ""))
                if t and start_dt <= t <= end_dt:
                    altars.append({
                        "id": d.get("id", "boss"),
                        "name": d.get("name", "Forsaken"),
                        "title": d.get("title", ""),
                        "biome": d.get("biome", "Midgard"),
                        "icon": d.get("icon", "🗝️"),
                        "timestamp_dt": t,
                        "time_str": t.strftime("%H:%M:%S"),
                    })

        return sessions, events, boss_triumphs, raids, altars

    def get_chunk(self, before_ts: Optional[float] = None) -> Dict[str, Any]:
        """Fetch the next timeline slice descending into the past."""
        earliest_dt = self.get_earliest_datetime()
        players = self.get_all_players()
        now = datetime.datetime.now()

        # Determine target window
        if before_ts is None:
            end_dt = now
            if end_dt.minute != 0 or end_dt.second != 0:
                start_dt = end_dt.replace(minute=0, second=0, microsecond=0)
            else:
                start_dt = end_dt - datetime.timedelta(hours=1)
        else:
            end_dt = datetime.datetime.fromtimestamp(before_ts)
            start_dt = end_dt - datetime.timedelta(hours=1)

        # Check for activity in [start_dt, end_dt]
        sessions, events, bosses, raids, altars = self._get_activity_in_window(start_dt, end_dt)
        has_activity = bool(sessions or events or bosses or raids or altars)

        # If idle, scan backwards to concatenate all consecutive idle hours into one tranquility block
        if not has_activity and start_dt > earliest_dt:
            gap_end = end_dt
            gap_start = start_dt
            while gap_start > earliest_dt:
                prev_start = gap_start - datetime.timedelta(hours=1)
                s2, e2, b2, r2, a2 = self._get_activity_in_window(prev_start, gap_start)
                if s2 or e2 or b2 or r2 or a2:
                    break
                gap_start = prev_start

            gap_seconds = (gap_end - gap_start).total_seconds()
            hours_val = round(gap_seconds / 3600.0, 1)
            is_end = (gap_start <= earliest_dt)

            if hours_val < 1.0:
                mins = max(1, int(round(gap_seconds / 60.0)))
                dur_label = f"{mins} Minutes"
            elif hours_val >= 24:
                days = int(hours_val // 24)
                hrs = int(round(hours_val % 24))
                dur_label = f"{days}d {hrs}h" if hrs > 0 else f"{days} Days"
            else:
                dur_label = f"{hours_val:g} Hours"

            return {
                "type": "idle",
                "chunk_id": f"idle_{int(gap_start.timestamp())}",
                "gap_hours": hours_val,
                "duration_label": dur_label,
                "gap_start_str": gap_start.strftime("%a %d %b %H:%M"),
                "gap_end_str": gap_end.strftime("%a %d %b %H:%M"),
                "next_before_ts": gap_start.timestamp(),
                "is_end": is_end,
                "earliest_date_str": earliest_dt.strftime("%d %b %Y"),
                "players": players,
            }

        # Active 1-Hour Chunk
        total_seconds = max(1.0, (end_dt - start_dt).total_seconds())

        # Construct player swimlanes
        player_lanes: List[Dict[str, Any]] = []
        for p in players:
            pname = p["name"]
            lane_sessions: List[Dict[str, Any]] = []
            lane_deaths: List[Dict[str, Any]] = []

            # 1. Sessions for this player
            for s in sessions:
                if s["player_name"] == pname:
                    seg_start = max(start_dt, s["login_dt"])
                    seg_end = min(end_dt, s["logout_dt"])
                    if seg_end > seg_start:
                        top_ratio = (end_dt - seg_end).total_seconds() / total_seconds
                        height_ratio = (seg_end - seg_start).total_seconds() / total_seconds
                        top_pct = round(top_ratio * 100.0, 2)
                        height_pct = max(3.0, round(height_ratio * 100.0, 2))
                        dur_sec = int((seg_end - seg_start).total_seconds())

                        lane_sessions.append({
                            "top_pct": top_pct,
                            "height_pct": height_pct,
                            "start_time": seg_start.strftime("%H:%M"),
                            "end_time": "Now" if s.get("is_active_now") and seg_end >= now - datetime.timedelta(seconds=60) else seg_end.strftime("%H:%M"),
                            "duration_str": format_duration(dur_sec),
                            "is_active_now": s.get("is_active_now", False),
                            "login_full": s["login_dt"].strftime("%m/%d %H:%M:%S"),
                            "logout_full": s["logout_dt"].strftime("%m/%d %H:%M:%S") if not s.get("is_active_now") else "Active Now",
                        })

            # 2. Deaths for this player
            for e in events:
                if e.get("event") == "death" and e.get("player_name") == pname:
                    death_dt = e["timestamp_dt"]
                    top_ratio = (end_dt - death_dt).total_seconds() / total_seconds
                    top_pct = round(top_ratio * 100.0, 2)
                    lane_deaths.append({
                        "top_pct": top_pct,
                        "time_str": e["time_str"],
                        "timestamp_full": death_dt.strftime("%m/%d/%Y %H:%M:%S"),
                        "player_name": pname,
                    })

            player_lanes.append({
                "player": p,
                "sessions": lane_sessions,
                "deaths": lane_deaths,
            })

        # Construct World Events landmark horizontal lines
        landmark_events: List[Dict[str, Any]] = []

        # Boss Triumphs
        for b in bosses:
            b_dt = b["timestamp_dt"]
            top_ratio = (end_dt - b_dt).total_seconds() / total_seconds
            top_pct = round(top_ratio * 100.0, 2)
            landmark_events.append({
                "type": "boss",
                "top_pct": top_pct,
                "time_str": b["time_str"],
                "timestamp_full": b_dt.strftime("%m/%d/%Y %H:%M:%S"),
                "title": f"{b['name']} Slain (Defeat #{b['defeat_number']})",
                "sub": f"Forsaken Altar · Day {b['world_day']}",
                "icon": b.get("icon", "🦌"),
                "day": b.get("world_day", 1),
            })

        # Raids
        for r in raids:
            r_dt = r["timestamp_dt"]
            top_ratio = (end_dt - r_dt).total_seconds() / total_seconds
            top_pct = round(top_ratio * 100.0, 2)
            landmark_events.append({
                "type": "raid",
                "top_pct": top_pct,
                "time_str": r["time_str"],
                "timestamp_full": r_dt.strftime("%m/%d/%Y %H:%M:%S"),
                "title": f"Raid: {r['title']}",
                "sub": r.get("quote", ""),
                "icon": r.get("icon", "⚡"),
                "biome": r.get("biome", "Midgard"),
            })

        # Boss Altar Discoveries (Vegvisir Runestones Revealed)
        for a in altars:
            a_dt = a["timestamp_dt"]
            top_ratio = (end_dt - a_dt).total_seconds() / total_seconds
            top_pct = round(top_ratio * 100.0, 2)
            boss_ico = a.get("icon", "⚔️")
            landmark_events.append({
                "type": "altar",
                "top_pct": top_pct,
                "time_str": a["time_str"],
                "timestamp_full": a_dt.strftime("%m/%d/%Y %H:%M:%S"),
                "title": f"{a['name']} Altar Located",
                "sub": f"Vegvisir Revealed · {a.get('biome', 'Midgard')}",
                "icon": f"🗝️ {boss_ico}",
                "biome": a.get("biome", "Midgard"),
            })

        is_end = (start_dt <= earliest_dt)

        return {
            "type": "active",
            "chunk_id": f"chunk_{int(start_dt.timestamp())}",
            "start_dt": start_dt,
            "end_dt": end_dt,
            "start_time_str": start_dt.strftime("%H:%M"),
            "end_time_str": "NOW" if before_ts is None else end_dt.strftime("%H:%M"),
            "end_time_badge": end_dt.strftime("%H:%M"),
            "date_str": start_dt.strftime("%a %d %b"),
            "next_before_ts": start_dt.timestamp(),
            "is_end": is_end,
            "earliest_date_str": earliest_dt.strftime("%d %b %Y"),
            "players": players,
            "lanes": player_lanes,
            "events": landmark_events,
            "deaths_in_chunk": sum(len(l["deaths"]) for l in player_lanes),
            "sessions_in_chunk": sum(len(l["sessions"]) for l in player_lanes),
        }


TIMELINE_ENGINE = TimelineEngine()
