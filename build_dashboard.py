"""Generate docs/index.html — a static dashboard from flights/* + state.json.

Run after each tracker poll to keep the published GitHub Pages site up to date.
Uses Leaflet via CDN for the map; everything else is plain HTML.
"""

import datetime
import html
import json
import pathlib
from typing import Optional

from lib import airports, geo
from lib.timefmt import fmt_dual


ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
FLIGHTS_DIR = ROOT / "flights"
OUT_PATH = ROOT / "docs" / "index.html"
REPO_URL = "https://github.com/steev611/flight-tracker"


def main():
    config = json.loads(CONFIG_PATH.read_text())
    state = json.loads(STATE_PATH.read_text())
    tz = config.get("display_timezone", "Europe/London")

    flights_raw = load_raw_flights()
    flights = [n for n in (_normalize(rec) for rec in flights_raw) if n]
    flights = _dedupe_flights(flights)
    events = load_recent_events(days=30)
    stats = compute_stats(flights)

    OUT_PATH.parent.mkdir(exist_ok=True)
    html_out = render_page(config, state, flights, events, stats, tz)
    OUT_PATH.write_text(html_out, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)} ({len(flights)} flights, {len(events)} events)")

    # Per-flight detail pages — only for flights with a real position track.
    detail_dir = OUT_PATH.parent / "flights"
    detail_dir.mkdir(exist_ok=True)
    written = 0
    for f in flights:
        if f.get("track_full") and f.get("flight_id"):
            page = build_flight_detail_page(f, tz)
            (detail_dir / f"{f['flight_id']}.html").write_text(page, encoding="utf-8")
            written += 1
    print(f"wrote {written} per-flight detail page(s) in docs/flights/")


def compute_stats(flights: list[dict]) -> dict:
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    week_cutoff = now_ts - 7 * 86400
    month_cutoff = now_ts - 30 * 86400

    durations = [f.get("duration_minutes") or 0 for f in flights]
    total_minutes = sum(durations)
    longest_minutes = max(durations) if durations else 0

    return {
        "total":       len(flights),
        "this_week":   sum(1 for f in flights if (f.get("first_seen") or 0) >= week_cutoff),
        "this_month":  sum(1 for f in flights if (f.get("first_seen") or 0) >= month_cutoff),
        "total_time":  _fmt_hm(total_minutes),
        "longest":     _fmt_hm(longest_minutes),
    }


def _dedupe_flights(flights: list[dict], tolerance_seconds: int = 600) -> list[dict]:
    """If a backfill and a tracker record represent the same flight (same icao24,
    first_seen within tolerance), keep the tracker one (richer data)."""
    by_key: dict[tuple, dict] = {}
    for f in sorted(flights, key=lambda x: (x.get("icao24") or "", x.get("first_seen") or 0)):
        # Find an existing bucket within tolerance for this aircraft.
        match_key = None
        for k in by_key:
            icao, ts = k
            if icao == (f.get("icao24") or "") and abs(ts - (f.get("first_seen") or 0)) <= tolerance_seconds:
                match_key = k
                break
        if match_key is None:
            by_key[(f.get("icao24") or "", f.get("first_seen") or 0)] = f
        else:
            current = by_key[match_key]
            # Prefer the record that has a real track.
            if (not current.get("track_full")) and f.get("track_full"):
                by_key[match_key] = f
    return list(by_key.values())


def _fmt_hm(mins: int) -> str:
    h, m = divmod(int(mins), 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def load_raw_flights() -> list[dict]:
    out = []
    if not FLIGHTS_DIR.exists():
        return out
    for pat in ("backfill_*.jsonl", "flights_*.jsonl"):
        for path in sorted(FLIGHTS_DIR.glob(pat)):
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rec["_source_file"] = path.name
                    out.append(rec)
    return out


def _normalize(rec: dict) -> Optional[dict]:
    if rec.get("_source_file", "").startswith("backfill_"):
        return _normalize_backfill(rec)
    return _normalize_tracker_flight(rec)


def _normalize_backfill(rec: dict) -> Optional[dict]:
    dep_icao = rec.get("estDepartureAirport")
    arr_icao = rec.get("estArrivalAirport")
    dep_a = airports.by_icao(dep_icao) if dep_icao else None
    arr_a = airports.by_icao(arr_icao) if arr_icao else None
    if not (dep_a and arr_a):
        return None
    return {
        "reg": (rec.get("callsign") or "").strip() or None,
        "icao24": rec.get("icao24"),
        "departure_icao": dep_icao,
        "departure_name": dep_a.get("name"),
        "departure_lat": dep_a["lat"],
        "departure_lon": dep_a["lon"],
        "arrival_icao": arr_icao,
        "arrival_name": arr_a.get("name"),
        "arrival_lat": arr_a["lat"],
        "arrival_lon": arr_a["lon"],
        "first_seen": rec["firstSeen"],
        "last_seen": rec["lastSeen"],
        "duration_minutes": rec.get("duration_minutes", (rec["lastSeen"] - rec["firstSeen"]) // 60),
        "source": "backfill",
        "track_pts": None,
    }


def _normalize_tracker_flight(rec: dict) -> Optional[dict]:
    track = rec.get("track") or []
    if not track or rec.get("destination_lat") is None:
        return None
    # Snap origin / destination to nearest airport for the table.
    dep_a = airports.nearest(rec.get("origin_lat"), rec.get("origin_lon"), max_nm=10) \
        if rec.get("origin_lat") is not None else None
    arr_a = airports.nearest(rec["destination_lat"], rec["destination_lon"], max_nm=10)
    first = track[0]
    last = track[-1]
    return {
        "reg": rec.get("registration"),
        "icao24": rec.get("icao24"),
        "flight_id": rec.get("flight_id"),
        "departure_icao": (dep_a or {}).get("icao") or "?",
        "departure_name": (dep_a or {}).get("name") or rec.get("origin") or "—",
        "departure_lat": first[0],
        "departure_lon": first[1],
        "arrival_icao": (arr_a or {}).get("icao") or "?",
        "arrival_name": (arr_a or {}).get("name") or rec.get("destination") or "—",
        "arrival_lat": last[0],
        "arrival_lon": last[1],
        "first_seen": rec.get("takeoff_ts") or first[3],
        "last_seen": rec.get("landed_ts") or last[3],
        "duration_minutes": (rec.get("elapsed_seconds") or 0) // 60,
        "source": "tracker",
        "track_pts": [[p[0], p[1]] for p in track if p[0] is not None and p[1] is not None],
        "track_full": track,  # full [lat, lon, alt, ts, gs] for per-flight detail page
    }


def load_recent_events(days: int = 30) -> list[dict]:
    if not FLIGHTS_DIR.exists():
        return []
    cutoff = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - days * 86400
    out = []
    for path in sorted(FLIGHTS_DIR.glob("events_*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("ts", 0) >= cutoff:
                    out.append(rec)
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def render_page(config: dict, state: dict, flights: list[dict],
                events: list[dict], stats: dict, tz: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    now_str = fmt_dual(now, tz)

    cards = []
    for ac in config["aircraft"]:
        reg = ac["registration"]
        s = state.get("aircraft", {}).get(reg) or {}
        status = s.get("status", "unknown")
        pos = s.get("last_position") or {}
        place = airports.describe_position(pos.get("lat"), pos.get("lon"), max_nm=15) \
            if pos.get("lat") is not None else "no position recorded"
        status_color = {
            "airborne": "#16a34a", "ground": "#1d4ed8",
            "absent": "#6b7280", "unknown": "#9ca3af",
        }.get(status, "#374151")
        cards.append(f"""
        <div class="card">
          <div class="card-h">
            <div class="reg">{html.escape(reg)}</div>
            <div class="badge" style="background:{status_color}">{html.escape(status.upper())}</div>
          </div>
          <div class="meta">{html.escape(ac.get('type',''))} &middot; {html.escape(ac.get('owner',''))}</div>
          <div class="loc">Last seen: {html.escape(place)}</div>
          <a class="btn" href="https://globe.adsb.lol/?icao={html.escape(ac['icao24'].lower())}" target="_blank">Track live &rarr;</a>
        </div>""")

    events_rows = []
    for ev in events[:50]:
        t = fmt_dual(datetime.datetime.fromtimestamp(ev["ts"], datetime.timezone.utc), tz)
        events_rows.append(
            f"<tr><td>{html.escape(t)}</td>"
            f"<td>{html.escape(ev.get('registration','?'))}</td>"
            f"<td><span class='ev ev-{html.escape(ev.get('type','?'))}'>"
            f"{html.escape(ev.get('type','?'))}</span></td></tr>"
        )
    if not events_rows:
        events_rows = ["<tr><td colspan='3' class='empty'>No tracker events yet — first will appear after N83TY's next flight.</td></tr>"]

    flights_rows = []
    for f in sorted(flights, key=lambda x: x["first_seen"], reverse=True)[:30]:
        t = datetime.datetime.fromtimestamp(f["first_seen"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
        date_cell = f"{html.escape(t)}Z"
        if f.get("flight_id") and f.get("track_full"):
            date_cell = (f"<a href='flights/{html.escape(f['flight_id'])}.html'>"
                         f"{html.escape(t)}Z &rarr;</a>")
        flights_rows.append(
            f"<tr><td>{date_cell}</td>"
            f"<td><b>{html.escape(f['departure_icao'])}</b><br>"
            f"<span class='dim'>{html.escape(f.get('departure_name') or '')}</span></td>"
            f"<td><b>{html.escape(f['arrival_icao'])}</b><br>"
            f"<span class='dim'>{html.escape(f.get('arrival_name') or '')}</span></td>"
            f"<td>{f['duration_minutes']//60}h {f['duration_minutes']%60}m</td></tr>"
        )
    if not flights_rows:
        flights_rows = ["<tr><td colspan='4' class='empty'>No flight history available.</td></tr>"]

    flights_json = json.dumps([
        {"d": [f["departure_lat"], f["departure_lon"]],
         "a": [f["arrival_lat"],   f["arrival_lon"]],
         "label": f"{f['departure_icao']} → {f['arrival_icao']}",
         "src": f["source"],
         "track": f.get("track_pts")}
        for f in flights
    ])
    tracked_json = json.dumps([
        {"reg": ac["registration"], "icao24": ac["icao24"].lower(),
         "type": ac.get("type", ""), "owner": ac.get("owner", "")}
        for ac in config["aircraft"]
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>flight-tracker dashboard</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin="">
  <style>
    *{{box-sizing:border-box}}
    body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f3f4f6;color:#111827}}
    header{{background:#111827;color:#fff;padding:18px 24px}}
    header h1{{margin:0;font-size:18px;font-weight:600}}
    header .sub{{font-size:12px;opacity:0.7;margin-top:2px}}
    .container{{max-width:1100px;margin:0 auto;padding:24px}}
    h2{{font-size:14px;text-transform:uppercase;letter-spacing:0.8px;color:#6b7280;margin:24px 0 8px}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
    .stat-cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
    .stat{{background:#fff;border-radius:8px;padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,0.06)}}
    .stat-num{{font-size:22px;font-weight:700;color:#111827}}
    .stat-label{{font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;margin-top:2px}}
    .card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,0.06)}}
    .card-h{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}
    .reg{{font-size:18px;font-weight:700}}
    .badge{{color:#fff;font-size:11px;font-weight:700;letter-spacing:0.5px;padding:3px 8px;border-radius:4px}}
    .meta{{font-size:12px;color:#6b7280;margin-bottom:8px}}
    .loc{{font-size:13px;margin-bottom:12px}}
    .btn{{display:inline-block;padding:6px 12px;background:#1d4ed8;color:#fff;border-radius:5px;text-decoration:none;font-size:13px;font-weight:500}}
    #map{{height:420px;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,0.06)}}
    table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,0.06);font-size:13px}}
    th{{text-align:left;padding:10px 12px;background:#f9fafb;color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:0.5px}}
    td{{padding:10px 12px;border-top:1px solid #f3f4f6;vertical-align:top}}
    .dim{{color:#9ca3af;font-size:11px}}
    .empty{{text-align:center;color:#9ca3af;padding:20px;font-style:italic}}
    .ev{{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px}}
    .ev-takeoff{{background:#dcfce7;color:#166534}}
    .ev-landing{{background:#dbeafe;color:#1e40af}}
    .ev-in_flight_progress{{background:#fef3c7;color:#92400e}}
    .ev-signal_lost{{background:#e5e7eb;color:#374151}}
    .ev-emergency_squawk{{background:#fee2e2;color:#991b1b}}
    footer{{padding:20px;text-align:center;color:#9ca3af;font-size:12px}}
    footer a{{color:#6b7280}}
    /* Live-status panel — populated by JS on page load */
    .live-panel{{display:none;background:#fff;border-radius:8px;padding:14px 18px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,0.06);border-left:4px solid #6b7280}}
    .live-panel.airborne{{border-left-color:#16a34a;background:linear-gradient(90deg,rgba(22,163,74,0.06),#fff 40%)}}
    .live-panel.ground{{border-left-color:#6b7280}}
    .live-row{{display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
    .live-dot{{width:10px;height:10px;border-radius:50%;background:#6b7280;flex-shrink:0}}
    .live-panel.airborne .live-dot{{background:#16a34a;animation:pulse 1.4s ease-in-out infinite}}
    @keyframes pulse{{0%{{box-shadow:0 0 0 0 rgba(22,163,74,0.6)}}70%{{box-shadow:0 0 0 8px rgba(22,163,74,0)}}100%{{box-shadow:0 0 0 0 rgba(22,163,74,0)}}}}
    .live-title{{font-size:11px;text-transform:uppercase;letter-spacing:0.8px;color:#6b7280}}
    .live-main{{font-size:15px;font-weight:600}}
    .live-meta{{font-size:13px;color:#6b7280;margin-top:2px}}
    .live-meta b{{color:#111827;font-weight:500}}
    .live-stale{{font-size:11px;color:#9ca3af;margin-left:auto}}
    .plane-marker{{background:#16a34a;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 2px rgba(22,163,74,0.4);width:14px!important;height:14px!important;margin-left:-7px!important;margin-top:-7px!important}}
  </style>
</head>
<body>
  <header>
    <h1>flight-tracker dashboard</h1>
    <div class="sub">Generated {html.escape(now_str)}</div>
  </header>
  <div class="container">
    <h2>Live</h2>
    <div id="live-panels"></div>

    <h2>Watched aircraft</h2>
    <div class="cards">{''.join(cards)}</div>

    <h2>Stats</h2>
    <div class="stat-cards">
      <div class="stat"><div class="stat-num">{stats['total']}</div><div class="stat-label">Total flights tracked</div></div>
      <div class="stat"><div class="stat-num">{stats['this_week']}</div><div class="stat-label">This week</div></div>
      <div class="stat"><div class="stat-num">{stats['this_month']}</div><div class="stat-label">Last 30 days</div></div>
      <div class="stat"><div class="stat-num">{html.escape(stats['total_time'])}</div><div class="stat-label">Total time aloft</div></div>
      <div class="stat"><div class="stat-num">{html.escape(stats['longest'])}</div><div class="stat-label">Longest flight</div></div>
    </div>

    <h2>Recent flight routes</h2>
    <div id="map"></div>

    <h2>Recent events (tracker)</h2>
    <table><thead><tr><th>When</th><th>Aircraft</th><th>Event</th></tr></thead>
      <tbody>{''.join(events_rows)}</tbody></table>

    <h2>Flight history (last 30 entries)</h2>
    <table><thead><tr><th>Date (UTC)</th><th>From</th><th>To</th><th>Duration</th></tr></thead>
      <tbody>{''.join(flights_rows)}</tbody></table>
  </div>
  <footer>
    <a href="{REPO_URL}">View source on GitHub</a> &middot;
    rebuilt automatically by the tracker workflow
  </footer>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
          crossorigin=""></script>
  <script>
    const flights = {flights_json};
    const tracked = {tracked_json};
    const map = L.map('map');
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 18, attribution: '&copy; OpenStreetMap'
    }}).addTo(map);
    if (flights.length) {{
      const bounds = [];
      flights.forEach(f => {{
        if (f.track && f.track.length > 1) {{
          L.polyline(f.track, {{color:'#1d4ed8', weight:3, opacity:0.85}})
            .addTo(map).bindTooltip(f.label + ' (real track)');
          f.track.forEach(pt => bounds.push(pt));
        }} else {{
          L.polyline([f.d, f.a], {{color:'#9ca3af', weight:1.5, opacity:0.6, dashArray:'4 6'}})
            .addTo(map).bindTooltip(f.label + ' (estimated)');
          bounds.push(f.d, f.a);
        }}
        L.circleMarker(f.d, {{radius:4, color:'#16a34a', fillOpacity:0.8}}).addTo(map);
        L.circleMarker(f.a, {{radius:4, color:'#dc2626', fillOpacity:0.8}}).addTo(map);
      }});
      map.fitBounds(bounds, {{padding:[20,20]}});
    }} else {{
      map.setView([54, 0], 4);
    }}

    // -------- Live current-position polling --------
    // Uses airplanes.live (CORS-open). Polls every 60s only when at least one
    // watched aircraft is airborne; falls silent when all are on the ground.
    const LIVE_API = "https://api.airplanes.live/v2/reg/";
    const POLL_MS = 60000;
    const STALE_MS = 180000;  // 3 polls
    const panelsRoot = document.getElementById('live-panels');
    const liveMarkers = {{}};   // keyed by icao24
    const liveTrails = {{}};    // keyed by icao24
    let pollTimer = null;

    function fmtDuration(secs) {{
      const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
      return h ? `${{h}}h ${{m}}m` : `${{m}}m`;
    }}
    function fmtFL(altFt) {{
      if (!altFt && altFt !== 0) return '—';
      if (altFt >= 18000) return 'FL' + String(Math.floor(altFt / 100)).padStart(3, '0');
      return altFt.toLocaleString() + ' ft';
    }}

    async function fetchOne(ac) {{
      try {{
        const r = await fetch(LIVE_API + encodeURIComponent(ac.reg));
        if (!r.ok) return {{ ac, error: 'HTTP ' + r.status }};
        const data = await r.json();
        const entry = (data.ac || [])[0] || null;
        return {{ ac, entry, fetchedAt: Date.now() }};
      }} catch (e) {{
        return {{ ac, error: String(e) }};
      }}
    }}

    function renderPanel(r) {{
      const id = 'live-' + r.ac.icao24;
      let el = document.getElementById(id);
      if (!el) {{
        el = document.createElement('div');
        el.id = id;
        el.className = 'live-panel';
        panelsRoot.appendChild(el);
      }}
      el.style.display = 'block';

      if (r.error || !r.entry) {{
        el.classList.remove('airborne');
        el.classList.add('ground');
        el.innerHTML = `
          <div class="live-row">
            <span class="live-dot"></span>
            <div>
              <div class="live-title">Currently</div>
              <div class="live-main">${{r.ac.reg}} — on the ground</div>
              <div class="live-meta">${{r.ac.type}}. No transponder signal right now.</div>
            </div>
          </div>`;
        return false;
      }}

      const e = r.entry;
      const onGround = e.alt_baro === 'ground';
      const isAirborne = !onGround && typeof e.alt_baro === 'number';

      if (!isAirborne) {{
        el.classList.remove('airborne');
        el.classList.add('ground');
        el.innerHTML = `
          <div class="live-row">
            <span class="live-dot"></span>
            <div>
              <div class="live-title">Currently</div>
              <div class="live-main">${{r.ac.reg}} — on the ground</div>
              <div class="live-meta">Position: <b>${{(e.lat||0).toFixed(3)}}, ${{(e.lon||0).toFixed(3)}}</b></div>
            </div>
          </div>`;
        return false;
      }}

      el.classList.add('airborne');
      el.classList.remove('ground');
      el.innerHTML = `
        <div class="live-row">
          <span class="live-dot"></span>
          <div>
            <div class="live-title">Live — airborne</div>
            <div class="live-main">${{r.ac.reg}} ${{e.flight ? '· ' + e.flight.trim() : ''}}</div>
            <div class="live-meta">
              <b>${{fmtFL(e.alt_baro)}}</b> &middot;
              <b>${{Math.round(e.gs || 0)}} kts</b> &middot;
              heading <b>${{Math.round(e.track || 0)}}°</b> &middot;
              <b>${{(e.lat||0).toFixed(3)}}, ${{(e.lon||0).toFixed(3)}}</b>
            </div>
          </div>
          <div class="live-stale" id="${{id}}-stale"></div>
        </div>`;
      updateMarker(r.ac.icao24, e);
      return true;
    }}

    function updateMarker(icao, e) {{
      if (!liveMarkers[icao]) {{
        const m = L.circleMarker([e.lat, e.lon], {{
          radius: 9, color: '#16a34a', weight: 3, fillColor: '#16a34a', fillOpacity: 0.9
        }}).addTo(map);
        liveMarkers[icao] = {{ marker: m }};
        liveTrails[icao] = {{ polyline: L.polyline([], {{color:'#16a34a', weight:2.5, opacity:0.85, dashArray:'2 4'}}).addTo(map), points: [] }};
      }}
      const lm = liveMarkers[icao];
      lm.marker.setLatLng([e.lat, e.lon]);
      lm.marker.bindTooltip(`<b>${{(e.flight||'').trim()}}</b><br>${{fmtFL(e.alt_baro)}} · ${{Math.round(e.gs||0)}} kts`);
      const trail = liveTrails[icao];
      const last = trail.points[trail.points.length - 1];
      if (!last || last[0] !== e.lat || last[1] !== e.lon) {{
        trail.points.push([e.lat, e.lon]);
        trail.polyline.setLatLngs(trail.points);
      }}
      lm.lastUpdate = Date.now();
    }}

    function clearMarker(icao) {{
      if (liveMarkers[icao]) {{ map.removeLayer(liveMarkers[icao].marker); delete liveMarkers[icao]; }}
      if (liveTrails[icao])  {{ map.removeLayer(liveTrails[icao].polyline); delete liveTrails[icao]; }}
    }}

    async function pollOnce() {{
      const results = await Promise.all(tracked.map(fetchOne));
      let anyAirborne = false;
      for (const r of results) {{
        const flying = renderPanel(r);
        if (flying) anyAirborne = true;
        else clearMarker(r.ac.icao24);
      }}
      if (anyAirborne) {{
        if (!pollTimer) pollTimer = setInterval(pollOnce, POLL_MS);
      }} else {{
        if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
      }}
    }}

    pollOnce();
    // Pause/resume polling when the tab visibility changes (be polite to the API)
    document.addEventListener('visibilitychange', () => {{
      if (document.hidden && pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
      else if (!document.hidden) pollOnce();
    }});
  </script>
</body>
</html>"""


def build_flight_detail_page(f: dict, tz: str) -> str:
    """Render a standalone HTML page for one flight: map + altitude + speed charts."""
    track = f["track_full"]  # [[lat, lon, alt, ts, gs], ...]
    distance_nm = round(geo.track_distance_nm(track))
    peak_alt = geo.peak_altitude_ft(track)
    dur = f["duration_minutes"]
    dur_str = f"{dur // 60}h {dur % 60}m"

    takeoff_str = fmt_dual(datetime.datetime.fromtimestamp(f["first_seen"], datetime.timezone.utc), tz)
    landed_str = fmt_dual(datetime.datetime.fromtimestamp(f["last_seen"], datetime.timezone.utc), tz)

    # Time-series for charts: convert ts deltas (seconds since takeoff) → minutes
    t0 = f["first_seen"]
    chart_data = [
        {
            "t": round((p[3] - t0) / 60, 1),
            "alt": p[2] if isinstance(p[2], (int, float)) else 0,
            "gs": p[4] if len(p) > 4 and isinstance(p[4], (int, float)) else None,
        }
        for p in track if p[3] is not None
    ]
    track_latlon = [[p[0], p[1]] for p in track if p[0] is not None and p[1] is not None]

    title = f"{f['reg']} · {f['departure_icao']} → {f['arrival_icao']}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)} · flight-tracker</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin="">
  <style>
    *{{box-sizing:border-box}}
    body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f3f4f6;color:#111827}}
    header{{background:#111827;color:#fff;padding:18px 24px}}
    header a{{color:#9ca3af;text-decoration:none;font-size:12px}}
    header h1{{margin:6px 0 0;font-size:20px;font-weight:600}}
    header .route{{font-size:13px;opacity:0.8;margin-top:4px}}
    .container{{max-width:1100px;margin:0 auto;padding:24px}}
    .stat-cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:20px}}
    .stat{{background:#fff;border-radius:8px;padding:12px;box-shadow:0 1px 2px rgba(0,0,0,0.06)}}
    .stat-num{{font-size:18px;font-weight:700}}
    .stat-label{{font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;margin-top:2px}}
    #map{{height:380px;border-radius:8px;margin-bottom:20px;box-shadow:0 1px 2px rgba(0,0,0,0.06)}}
    .chart-card{{background:#fff;border-radius:8px;padding:16px;margin-bottom:14px;box-shadow:0 1px 2px rgba(0,0,0,0.06)}}
    .chart-card h3{{margin:0 0 12px;font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px}}
    canvas{{width:100%!important;height:200px!important}}
  </style>
</head>
<body>
  <header>
    <a href="../index.html">&larr; back to dashboard</a>
    <h1>{html.escape(title)}</h1>
    <div class="route">{html.escape(f.get('departure_name') or '')} &rarr; {html.escape(f.get('arrival_name') or '')}</div>
  </header>
  <div class="container">
    <div class="stat-cards">
      <div class="stat"><div class="stat-num">{dur_str}</div><div class="stat-label">Duration</div></div>
      <div class="stat"><div class="stat-num">{distance_nm:,} nm</div><div class="stat-label">Distance flown</div></div>
      <div class="stat"><div class="stat-num">{html.escape(geo.fmt_fl(peak_alt))}</div><div class="stat-label">Peak altitude</div></div>
      <div class="stat"><div class="stat-num" style="font-size:14px;font-weight:500">{html.escape(takeoff_str)}</div><div class="stat-label">Takeoff</div></div>
      <div class="stat"><div class="stat-num" style="font-size:14px;font-weight:500">{html.escape(landed_str)}</div><div class="stat-label">Landing</div></div>
    </div>

    <div id="map"></div>

    <div class="chart-card">
      <h3>Altitude profile</h3>
      <canvas id="alt-chart"></canvas>
    </div>
    <div class="chart-card">
      <h3>Ground speed profile</h3>
      <canvas id="speed-chart"></canvas>
    </div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
          crossorigin=""></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
    const track = {json.dumps(track_latlon)};
    const series = {json.dumps(chart_data)};

    const map = L.map('map');
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 18, attribution: '&copy; OpenStreetMap'
    }}).addTo(map);
    L.polyline(track, {{color:'#1d4ed8', weight:3, opacity:0.9}}).addTo(map);
    L.circleMarker(track[0], {{radius:5, color:'#16a34a', fillOpacity:0.9}}).addTo(map);
    L.circleMarker(track[track.length-1], {{radius:5, color:'#dc2626', fillOpacity:0.9}}).addTo(map);
    map.fitBounds(track, {{padding:[20,20]}});

    const t = series.map(p => p.t.toFixed(0) + 'm');
    new Chart(document.getElementById('alt-chart'), {{
      type: 'line',
      data: {{ labels: t,
        datasets: [{{ data: series.map(p => p.alt), borderColor:'#1d4ed8',
                     backgroundColor:'rgba(29,78,216,0.1)', fill:true, tension:0.3,
                     pointRadius:1.5 }}] }},
      options: {{ plugins:{{legend:{{display:false}}}},
        scales:{{ y:{{ ticks:{{ callback:v=>v.toLocaleString()+' ft' }} }} }} }}
    }});
    new Chart(document.getElementById('speed-chart'), {{
      type: 'line',
      data: {{ labels: t,
        datasets: [{{ data: series.map(p => p.gs), borderColor:'#ca8a04',
                     backgroundColor:'rgba(202,138,4,0.1)', fill:true, tension:0.3,
                     spanGaps:true, pointRadius:1.5 }}] }},
      options: {{ plugins:{{legend:{{display:false}}}},
        scales:{{ y:{{ ticks:{{ callback:v=>v+' kts' }} }} }} }}
    }});
  </script>
</body>
</html>"""


if __name__ == "__main__":
    main()
