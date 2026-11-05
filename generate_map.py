"""
generate_map.py

Reads map_sites_index.csv (must be in the same folder as this script) and
serves an interactive map of all water inspection sites in Canada straight
from memory over a local web server (Leaflet.js with an OpenFreeMap base
map). No HTML file is written to disk unless you pass --export.

LIBRARIES (downloaded up front, then fully local)
----------------------------------------------------------------
Every run starts by making sure the `lib/` folder next to this script holds
all of the JavaScript/CSS/image dependencies (Leaflet, MarkerCluster,
PapaParse, MapLibre GL, the Leaflet-MapLibre bridge, and Leaflet's marker
images). Anything missing is downloaded right away, before the map is built.
If a download fails, the script stops with an error instead of quietly
falling back to a CDN, so the generated HTML always points at local files
in lib/.

Once lib/ is complete, no further downloads happen. Your data (the site
index CSV and the basin CSVs in wq_basin_data/) is never downloaded by this
script at all; it only reads what is already on disk.

BASE MAP
----------------------------------------------------------------
The base map is OpenFreeMap (vector tiles rendered with MapLibre GL inside
Leaflet). It needs no API key, no account and has no usage limits, so
anyone can clone and run this project with nothing to sign up for. The tiles
are loaded live by the browser from tiles.openfreemap.org, so the map needs
internet access to show the background. The required attribution
(OpenFreeMap, OpenMapTiles, OpenStreetMap) is shown on the map. Do not
remove it.

CONTAMINANT DATA (per-site, read live from the basin CSVs)
-------------------------------------------------------------
Clicking a site's marker fetches that site's *basin* CSV file straight
out of wq_basin_data/ (path taken from the BASIN_FILE column in
map_sites_index.csv — see generate_map_index.py), parses it in the
browser with PapaParse, and lists the distinct contaminants (VARIABLE
column) measured at that specific site. Nothing about contaminants is
baked into the page when it is built — it's read fresh from the CSV
each time a popup opens (cached per-basin in memory so re-opening popups
in the same basin doesn't re-fetch/re-parse).

The script always starts a local web server (browsers block pages opened
from disk from reading the basin CSVs) and opens the map in your browser at
http://localhost:8000/ . Press Ctrl+C to stop.

HOW TO PLUG IN CALCULATIONS LATER
-----------------------------------
Once a contaminant is chosen from the dropdown, that selection is
available in the `onContaminantChosen(site, rows)` function inside the
generated HTML — `rows` is the array of that contaminant's raw
measurement rows for that site (date, value, unit, etc.), ready to feed
into a trend test (e.g. Seasonal Kendall) whenever you're ready to add
that.

Usage:
    python3 generate_map.py              # build in memory, serve, open browser
    python3 generate_map.py --port 8001  # use a different port
    python3 generate_map.py --no-browser # serve without opening a browser tab
    python3 generate_map.py --export     # write index.html (for GitHub Pages) and exit
"""

import argparse
import csv
import http.server
import json
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
EXPORT_PATH = SCRIPT_DIR / "index.html"
CSV_PATH = SCRIPT_DIR / "map_sites_index.csv"
LIB_DIR = SCRIPT_DIR / "lib"
IMG_DIR = LIB_DIR / "images"

REQUIRED_LOCAL_LIBS = [
    "leaflet.min.js", "leaflet.min.css",
    "leaflet.markercluster.js", "MarkerCluster.css", "MarkerCluster.Default.css",
    "papaparse.min.js",
    "maplibre-gl.js", "maplibre-gl.css", "leaflet-maplibre-gl.js",
]

# --------------------------------------------------------------------------
# Library vendoring (formerly download_libs.py — now runs automatically,
# on demand, instead of needing to be run as a separate one-off script)
# --------------------------------------------------------------------------

LIB_FILES = {
    "leaflet.min.js": "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js",
    "leaflet.min.css": "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css",
    "leaflet.markercluster.js": "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js",
    "MarkerCluster.css": "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css",
    "MarkerCluster.Default.css": "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.Default.css",
    "papaparse.min.js": "https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.4.1/papaparse.min.js",
    "maplibre-gl.js": "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js",
    "maplibre-gl.css": "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css",
    "leaflet-maplibre-gl.js": "https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js",
}

# Leaflet's CSS references these marker icon images by relative path
# (images/marker-icon.png etc.) — needed so pins render correctly offline.
LIB_IMAGES = {
    "marker-icon.png": "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png",
    "marker-icon-2x.png": "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png",
    "marker-shadow.png": "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
    "layers.png": "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/layers.png",
    "layers-2x.png": "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/layers-2x.png",
}

def _fetch(url: str, dest: Path) -> bool:
    print(f"  downloading {dest.name} ...", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        print(f"ok ({dest.stat().st_size:,} bytes)")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False


def ensure_local_libs() -> bool:
    """
    Make sure lib/ has everything generate_map.py needs, downloading
    whatever's missing. Returns True if local libs are usable afterward.
    """
    LIB_DIR.mkdir(exist_ok=True)
    IMG_DIR.mkdir(exist_ok=True)

    missing_libs = [f for f in LIB_FILES if not (LIB_DIR / f).exists()]
    missing_images = [f for f in LIB_IMAGES if not (IMG_DIR / f).exists()]

    if not missing_libs and not missing_images:
        return True  # already fully downloaded

    print(f"Local lib/ folder is missing {len(missing_libs) + len(missing_images)} "
          f"file(s) — downloading them now.\n")

    all_ok = True
    for name in missing_libs:
        all_ok &= _fetch(LIB_FILES[name], LIB_DIR / name)
    for name in missing_images:
        all_ok &= _fetch(LIB_IMAGES[name], IMG_DIR / name)

    print()
    return all_ok and detect_local_libs()


def load_sites(csv_path: Path) -> list[dict]:
    """Read and lightly validate the site CSV, returning a list of dicts."""
    if not csv_path.exists():
        sys.exit(f"ERROR: could not find {csv_path.name} next to this script.")

    sites = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required_cols = {"SITE_NO", "SITE_NAME", "LATITUDE", "LONGITUDE",
                          "BASIN_SLUG", "BASIN_NAME", "BASIN_FILE"}
        missing = required_cols - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"ERROR: CSV is missing expected columns: {missing}")

        for i, row in enumerate(reader, start=2):  # start=2 accounts for header row
            try:
                lat = float(row["LATITUDE"])
                lon = float(row["LONGITUDE"])
            except ValueError:
                print(f"WARNING: skipping row {i} ({row.get('SITE_NO')}) "
                      f"— non-numeric coordinates.")
                continue

            sites.append({
                "site_no": row["SITE_NO"].strip(),
                "site_name": row["SITE_NAME"].strip(),
                "lat": lat,
                "lon": lon,
                "basin_slug": row["BASIN_SLUG"].strip(),
                "basin_name": row["BASIN_NAME"].strip(),
                "basin_file": row["BASIN_FILE"].strip(),
            })
    return sites


def detect_local_libs() -> bool:
    if not LIB_DIR.exists():
        return False
    return (all((LIB_DIR / f).exists() for f in REQUIRED_LOCAL_LIBS)
            and all((IMG_DIR / f).exists() for f in LIB_IMAGES))


def build_html(sites: list[dict]) -> str:
    sites_json = json.dumps(sites)
    basins = sorted({s["basin_name"] for s in sites})
    basins_json = json.dumps(basins)

    # --- library sources: always the local lib/ folder ---------------------
    leaflet_css = "lib/leaflet.min.css"
    cluster_css1 = "lib/MarkerCluster.css"
    cluster_css2 = "lib/MarkerCluster.Default.css"
    leaflet_js = "lib/leaflet.min.js"
    cluster_js = "lib/leaflet.markercluster.js"
    papaparse_js = "lib/papaparse.min.js"
    maplibre_css = "lib/maplibre-gl.css"
    maplibre_js = "lib/maplibre-gl.js"
    maplibre_leaflet_js = "lib/leaflet-maplibre-gl.js"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Canada Water Inspection Sites</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0" />

<link rel="stylesheet" href="{leaflet_css}" />
<link rel="stylesheet" href="{maplibre_css}" />
<link rel="stylesheet" href="{cluster_css1}" />
<link rel="stylesheet" href="{cluster_css2}" />

<style>
  html, body {{ height: 100%; margin: 0; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; }}
  #map {{ height: 100%; width: 100%; }}

  #controls {{
    position: absolute;
    top: 10px;
    right: 10px;
    z-index: 1000;
    background: white;
    padding: 10px 12px;
    border-radius: 6px;
    box-shadow: 0 1px 6px rgba(0,0,0,0.3);
    max-width: 260px;
    font-size: 13px;
  }}
  #controls label {{ font-weight: 600; display: block; margin-bottom: 4px; }}
  #basin-filter {{ width: 100%; padding: 4px; }}
  #site-count {{ margin-top: 6px; color: #555; }}

  .popup-title {{ font-weight: 700; margin-bottom: 4px; font-size: 14px; }}
  .popup-basin {{ color: #256; font-size: 12px; margin-bottom: 6px; }}
  .popup-row {{ font-size: 12px; margin: 2px 0; }}
  .popup-label {{ color: #666; }}
  .popup-loading {{ font-style: italic; color: #888; font-size: 12px; }}
  .popup-error {{ color: #b00020; font-size: 12px; }}
  .popup-data-section {{ margin-top: 8px; border-top: 1px solid #ddd; padding-top: 6px; }}
  .popup-contaminant-select {{ width: 100%; padding: 3px; font-size: 12px; margin-top: 4px; }}
  .popup-chart {{ margin-top: 6px; }}

  .popup-excluded-note {{
    margin-top: 6px;
    font-size: 11px;
    color: #777;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }}
  .popup-excluded-toggle {{
    font-size: 11px;
    padding: 1px 8px;
    border: 1px solid #ccc;
    border-radius: 10px;
    background: #f5f5f5;
    color: #333;
    cursor: pointer;
    flex-shrink: 0;
  }}
  .popup-excluded-toggle:hover {{ background: #eaeaea; }}
  .popup-excluded-list {{
    margin: 4px 0 0 0;
    padding-left: 16px;
    font-size: 11px;
    color: #888;
    max-height: 120px;
    overflow-y: auto;
  }}
  .popup-excluded-list li {{ margin: 1px 0; }}

  #runtime-error {{
    display: none;
    position: absolute;
    bottom: 24px; left: 10px;
    z-index: 2000;
    max-width: 480px;
    background: #b00020;
    color: white;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
  }}

  #load-error {{
    display: none;
    position: absolute;
    top: 0; left: 0; right: 0;
    z-index: 2000;
    background: #b00020;
    color: white;
    padding: 12px 16px;
    font-size: 14px;
  }}
</style>
</head>
<body>

<div id="load-error">
  Map libraries failed to load from the local <code>lib/</code> folder. Open the
  map through the local server (<code>python3 generate_map.py</code>), and re-run
  <code>generate_map.py</code> with internet access if lib/ is incomplete.
</div>
<div id="map"></div>
<div id="runtime-error"></div>
<div id="controls">
  <label for="basin-filter">Filter by basin</label>
  <select id="basin-filter">
    <option value="__all__">All basins ({len(sites)} sites)</option>
  </select>
  <div id="site-count"></div>
</div>

<script src="{leaflet_js}"></script>
<script src="{maplibre_js}"></script>
<script src="{maplibre_leaflet_js}"></script>
<script src="{cluster_js}"></script>
<script src="{papaparse_js}"></script>
<script>
  if (typeof L === 'undefined' || typeof Papa === 'undefined' || typeof maplibregl === 'undefined' || typeof L.maplibreGL !== 'function') {{
      document.getElementById('load-error').style.display = 'block';
      throw new Error('Map libraries failed to load.');
  }}
</script>

<script>
function showRuntimeError(msg) {{
    const el = document.getElementById('runtime-error');
    el.style.display = 'block';
    el.textContent = (el.textContent ? el.textContent + ' | ' : '') + msg;
}}
window.addEventListener('error', (e) => showRuntimeError(e.message));
window.addEventListener('unhandledrejection', (e) => showRuntimeError(String(e.reason)));
</script>

<script>
// ---------------------------------------------------------------------
// DATA: baked in at generation time from map_sites_index.csv
// ---------------------------------------------------------------------
const SITES = {sites_json};
const BASINS = {basins_json};

// ---------------------------------------------------------------------
// Basin CSV loading — fetched live from wq_basin_data/ (NOT baked into
// this HTML at generation time; these files can be tens of thousands of
// rows). Cached in memory per basin file so opening several site popups
// within the same basin only fetches/parses that basin's CSV once.
// ---------------------------------------------------------------------
const BASIN_DATA_DIR = "wq_basin_data";
const basinCsvCache = {{}};   // basin_file -> Promise<Array<row objects>>

function loadBasinCSV(basinFile) {{
    if (basinCsvCache[basinFile]) return basinCsvCache[basinFile];

    const url = `${{BASIN_DATA_DIR}}/${{encodeURIComponent(basinFile)}}`;
    basinCsvCache[basinFile] = fetch(url)
        .then(resp => {{
            if (!resp.ok) throw new Error(`HTTP ${{resp.status}} fetching ${{basinFile}}`);
            return resp.text();
        }})
        .then(text => new Promise((resolve, reject) => {{
            // Strip a leading UTF-8 BOM if present — these ECCC CSVs have one,
            // and unlike Python's utf-8-sig, fetch().text() does not strip it,
            // which would otherwise corrupt the first header (SITE_NO -> "\uFEFFSITE_NO").
            if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
            Papa.parse(text, {{
                header: true,
                skipEmptyLines: true,
                complete: (result) => resolve(result.data),
                error: (err) => reject(err),
            }});
        }}));

    return basinCsvCache[basinFile];
}}

// ---------------------------------------------------------------------
// Seasonal Kendall trend test (Hirsch, Slack & Smith, 1982)
// ---------------------------------------------------------------------
// Season = calendar month (12 possible seasons — the field-standard
// choice for water quality trend analysis). Compares each month only to
// the SAME month in other years, so a normal seasonal cycle (e.g. spring
// melt vs. summer low-flow) never gets mistaken for a long-term trend.
//
// GROUPING ALGORITHM:
//   1. Parse every row's date + value.
//   2. Collapse multiple samples in the same (site, month, year) down to
//      one value via median — the standard robust choice for skewed
//      environmental data, and required because the test needs exactly
//      one value per season-year.
//   3. Only keep months that have data in >=2 different years — a month
//      seen in just one year can never contribute a valid comparison, so
//      including it would just be noise. This is what actually
//      determines "which seasons are usable" for THIS site+contaminant,
//      rather than assuming all 12 months are meaningful.
//   4. Within each usable month, compute the Mann-Kendall S statistic and
//      its tie-corrected variance across years; sum across months for
//      the overall S and variance (assumes seasons are independent —
//      the standard/simple form of the test; Hirsch & Slack's 1984
//      covariance correction for cross-season autocorrelation is a
//      documented refinement, not applied here).
//   5. Z-score (with continuity correction) -> two-tailed p-value.
//   6. Sen's slope: median of every valid pairwise slope, computed within
//      each season across years, pooled across all seasons.
//
// NOTE ON NON-DETECTS: rows flagged "<" in FLAG_MARQUEUR (below the
// detection limit) are used at face value (ECCC reports the detection
// limit as VALUE_VALEUR for these). This is a simplification — the more
// rigorous approach treats non-detects as a special tie case — but keeps
// the first version straightforward.
function seasonalKendallTest(rows) {{
    const parsed = [];
    for (const r of rows) {{
        const dt = new Date((r.DATE_TIME_HEURE || "").replace(" ", "T"));
        const val = parseFloat(r.VALUE_VALEUR);
        if (isNaN(dt.getTime()) || isNaN(val)) continue;
        parsed.push({{ year: dt.getUTCFullYear(), month: dt.getUTCMonth() + 1, val }});
    }}

    const median = (arr) => {{
        const s = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(s.length / 2);
        return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
    }};

    // Step 2: collapse to one value per (year, month) via median
    const byYM = new Map();
    for (const {{ year, month, val }} of parsed) {{
        const key = `${{year}}-${{month}}`;
        if (!byYM.has(key)) byYM.set(key, []);
        byYM.get(key).push(val);
    }}
    const agg = new Map();
    for (const [key, vals] of byYM) agg.set(key, median(vals));

    // Step 3: which months have >=2 distinct years of data?
    const monthYears = new Map();
    for (const key of agg.keys()) {{
        const [year, month] = key.split('-').map(Number);
        if (!monthYears.has(month)) monthYears.set(month, new Set());
        monthYears.get(month).add(year);
    }}
    const usableMonths = [...monthYears.entries()]
        .filter(([, yrs]) => yrs.size >= 2)
        .map(([m]) => m)
        .sort((a, b) => a - b);

    if (usableMonths.length === 0) {{
        return {{ ok: false, reason: "Not enough repeated-year data in any single month to run a Seasonal Kendall test." }};
    }}

    // Step 4: per-season Mann-Kendall S + tie-corrected variance
    function mkSAndVar(values) {{
        const n = values.length;
        let s = 0;
        for (let i = 0; i < n - 1; i++) {{
            for (let j = i + 1; j < n; j++) {{
                const diff = values[j] - values[i];
                s += (diff > 0 ? 1 : 0) - (diff < 0 ? 1 : 0);
            }}
        }}
        const counts = new Map();
        for (const v of values) counts.set(v, (counts.get(v) || 0) + 1);
        let tieTerm = 0;
        for (const t of counts.values()) if (t > 1) tieTerm += t * (t - 1) * (2 * t + 5);
        const varS = (n * (n - 1) * (2 * n + 5) - tieTerm) / 18;
        return {{ s, varS }};
    }}

    let sTotal = 0, varTotal = 0;
    const slopes = [];
    for (const m of usableMonths) {{
        const years = [...monthYears.get(m)].sort((a, b) => a - b);
        const values = years.map(y => agg.get(`${{y}}-${{m}}`));
        const {{ s, varS }} = mkSAndVar(values);
        sTotal += s;
        varTotal += varS;
        for (let i = 0; i < values.length - 1; i++) {{
            for (let j = i + 1; j < values.length; j++) {{
                const dy = years[j] - years[i];
                if (dy !== 0) slopes.push((values[j] - values[i]) / dy);
            }}
        }}
    }}

    // Step 5: Z-score with continuity correction -> two-tailed p-value
    let z;
    if (sTotal > 0) z = (sTotal - 1) / Math.sqrt(varTotal);
    else if (sTotal < 0) z = (sTotal + 1) / Math.sqrt(varTotal);
    else z = 0;

    function erf(x) {{
        // Abramowitz-Stegun 7.1.26 approximation, accurate to ~1.5e-7
        const t = 1 / (1 + 0.3275911 * Math.abs(x));
        const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
        return x >= 0 ? y : -y;
    }}
    const normCdf = (zz) => 0.5 * (1 + erf(zz / Math.SQRT2));
    const p = 2 * (1 - normCdf(Math.abs(z)));

    // Step 6: Sen's slope
    const senSlope = slopes.length ? median(slopes) : null;

    return {{
        ok: true,
        S: sTotal,
        varS: varTotal,
        Z: z,
        p,
        senSlope,
        usableMonths,
        seasonsUsed: usableMonths.length,
        yearSpan: [...agg.keys()].map(k => Number(k.split('-')[0])),
    }};
}}

const MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

// ---------------------------------------------------------------------
// Seasonal comparison chart. Plots EXACTLY the months the Seasonal
// Kendall test above actually used (result.usableMonths) — i.e. only
// months with >=2 distinct years of data, same rule the test applies.
// This keeps the chart and the trend numbers below it consistent: if a
// month isn't in "Seasons used", it won't be drawn here either.
//
// One color per calendar month (index = month - 1), so a given month
// always renders in the same color across different contaminants/sites.
// ---------------------------------------------------------------------
const COMPARISON_COLORS = [
    "#2b8cbe", "#e6550d", "#31a354", "#756bb1", "#c51b8a", "#636363",
    "#1c9099", "#fd8d3c", "#78c679", "#9e9ac8", "#f768a1", "#969696",
]; // Jan..Dec, in order

function computeSeasonalComparisonData(rows, months) {{
    const median = (arr) => {{
        const s = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(s.length / 2);
        return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
    }};

    // month -> year -> [values]
    const byMonth = new Map(months.map(m => [m, new Map()]));
    let unit = "";
    for (const r of rows) {{
        const dt = new Date((r.DATE_TIME_HEURE || "").replace(" ", "T"));
        const val = parseFloat(r.VALUE_VALEUR);
        if (isNaN(dt.getTime()) || isNaN(val)) continue;
        const month = dt.getUTCMonth() + 1;
        if (!byMonth.has(month)) continue;
        const year = dt.getUTCFullYear();
        const yearMap = byMonth.get(month);
        if (!yearMap.has(year)) yearMap.set(year, []);
        yearMap.get(year).push(val);
        if (!unit && r.UNIT_UNITÉ) unit = r.UNIT_UNITÉ;
    }}

    // Collapse same (month, year) samples to one point via median.
    const series = months.map(m => {{
        const yearMap = byMonth.get(m);
        const points = [...yearMap.entries()]
            .map(([year, vals]) => ({{ year, value: median(vals) }}))
            .sort((a, b) => a.year - b.year);
        return {{ month: m, points }};
    }});

    return {{ series, unit }};
}}

function renderSeasonalComparisonSVG(data, months) {{
    const seriesWithData = data.series.filter(s => s.points.length > 0);
    if (seriesWithData.length === 0) {{
        return `<div class="popup-loading">No May/June data available to chart for this contaminant.</div>`;
    }}

    const W = 300, H = 170, padL = 42, padR = 10, padT = 12, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;

    const allYears = [...new Set(seriesWithData.flatMap(s => s.points.map(p => p.year)))].sort((a, b) => a - b);
    const allValues = seriesWithData.flatMap(s => s.points.map(p => p.value));
    let yMin = Math.min(...allValues), yMax = Math.max(...allValues);
    if (yMin === yMax) {{ yMin -= 1; yMax += 1; }} // avoid a zero-height scale
    const yPad = (yMax - yMin) * 0.08;
    yMin -= yPad; yMax += yPad;

    const xYear = (year) => padL + (allYears.length === 1 ? plotW / 2
        : (year - allYears[0]) / (allYears[allYears.length - 1] - allYears[0]) * plotW);
    const yVal = (v) => padT + plotH - (v - yMin) / (yMax - yMin) * plotH;

    // Y-axis ticks (4 evenly spaced)
    const yTicks = [0, 1, 2, 3, 4].map(i => yMin + (yMax - yMin) * i / 4);
    const yTicksSVG = yTicks.map(t => `
        <line x1="${{padL}}" y1="${{yVal(t).toFixed(1)}}" x2="${{W - padR}}" y2="${{yVal(t).toFixed(1)}}" stroke="#eee" stroke-width="1" />
        <text x="${{padL - 6}}" y="${{(yVal(t) + 3).toFixed(1)}}" font-size="9" fill="#666" text-anchor="end">${{t.toPrecision(3)}}</text>
    `).join("");

    // X-axis ticks: every year, or thinned out if too many to fit legibly
    const step = allYears.length > 8 ? Math.ceil(allYears.length / 8) : 1;
    const xTicksSVG = allYears.filter((_, i) => i % step === 0).map(y => `
        <text x="${{xYear(y).toFixed(1)}}" y="${{H - padB + 14}}" font-size="9" fill="#666" text-anchor="middle">${{y}}</text>
    `).join("");

    const seriesSVG = seriesWithData.map((s) => {{
        const color = COMPARISON_COLORS[s.month - 1];
        const linePoints = s.points.map(p => `${{xYear(p.year).toFixed(1)}},${{yVal(p.value).toFixed(1)}}`).join(" ");
        const dots = s.points.map(p => `<circle cx="${{xYear(p.year).toFixed(1)}}" cy="${{yVal(p.value).toFixed(1)}}" r="2.5" fill="${{color}}" />`).join("");
        return `<polyline points="${{linePoints}}" fill="none" stroke="${{color}}" stroke-width="1.5" />${{dots}}`;
    }}).join("");

    // Space legend items to fit however many months ended up qualifying
    // (could be 2, could be all 12) instead of a fixed 55px gap.
    const legendItemW = Math.min(55, (W - padL - padR) / seriesWithData.length);
    const legendSVG = seriesWithData.map((s, i) => {{
        const color = COMPARISON_COLORS[s.month - 1];
        const x = padL + i * legendItemW;
        return `
            <circle cx="${{x}}" cy="${{H - 6}}" r="3" fill="${{color}}" />
            <text x="${{x + 6}}" y="${{H - 3}}" font-size="9" fill="#333">${{MONTH_NAMES[s.month - 1]}}</text>
        `;
    }}).join("");

    const unitLabel = data.unit ? ` (${{data.unit}})` : "";

    return `
        <svg viewBox="0 0 ${{W}} ${{H}}" width="${{W}}" height="${{H}}" style="max-width:100%;">
            <text x="4" y="10" font-size="9" fill="#666">value${{unitLabel}}</text>
            ${{yTicksSVG}}
            <line x1="${{padL}}" y1="${{padT}}" x2="${{padL}}" y2="${{H - padB}}" stroke="#999" stroke-width="1" />
            <line x1="${{padL}}" y1="${{H - padB}}" x2="${{W - padR}}" y2="${{H - padB}}" stroke="#999" stroke-width="1" />
            ${{xTicksSVG}}
            ${{seriesSVG}}
            ${{legendSVG}}
        </svg>
    `;
}}

function renderTrendResultHTML(contaminant, result) {{
    if (!result.ok) {{
        return `<div class="popup-data-section popup-loading">${{result.reason}}</div>`;
    }}
    const years = result.yearSpan;
    // Direction is read from Z (equivalently, S), NOT from Sen's slope.
    // Sen's slope is the median of every pairwise slope pooled across all
    // usable months — with heavily tied data (e.g. detection-limit
    // non-detects tied at a floor value, or a plateau of repeated values)
    // that pooled median can land exactly on 0, and ">0" would then
    // silently default to "decreasing" even when the test itself (S/Z,
    // what the p-value is actually computed from) is strongly positive.
    // Z's sign always agrees with S's sign and isn't sensitive to that
    // tie-heavy median artifact, so it's the reliable source for direction.
    const direction = result.p >= 0.05 ? "No significant trend"
        : (result.Z > 0 ? "Significant increasing trend" : "Significant decreasing trend");
    const months = result.usableMonths.map(m => MONTH_NAMES[m - 1]).join(", ");
    return `<div class="popup-data-section">
        <div class="popup-row"><b>${{direction}}</b></div>
        <div class="popup-row"><span class="popup-label">p-value:</span> ${{result.p.toFixed(4)}}</div>
        <div class="popup-row"><span class="popup-label">Sen's slope:</span> ${{result.senSlope.toFixed(5)}} per year</div>
        <div class="popup-row"><span class="popup-label">S / Z:</span> ${{result.S}} / ${{result.Z.toFixed(3)}}</div>
        <div class="popup-row"><span class="popup-label">Seasons used:</span> ${{months}} (${{result.seasonsUsed}})</div>
        <div class="popup-row"><span class="popup-label">Years:</span> ${{Math.min(...years)}}–${{Math.max(...years)}}</div>
    </div>`;
}}

// ---------------------------------------------------------------------
// What happens once a contaminant is chosen: run the Seasonal Kendall
// test on that site+contaminant's rows and render the result.
// ---------------------------------------------------------------------
function onContaminantChosen(site, contaminant, rows) {{
    const resultContainer = document.getElementById(`trend-${{site.site_no}}`);
    if (!resultContainer) return;

    const result = seasonalKendallTest(rows);

    // Chart plots exactly the months the test used — if a month didn't
    // clear the >=2-years-of-data bar, it won't show up in either place.
    let chartHTML = "";
    if (result.ok) {{
        const chartData = computeSeasonalComparisonData(rows, result.usableMonths);
        chartHTML = `<div class="popup-chart">${{renderSeasonalComparisonSVG(chartData, result.usableMonths)}}</div>`;
    }}

    resultContainer.innerHTML = chartHTML + renderTrendResultHTML(contaminant, result);
}}

// ---------------------------------------------------------------------
// Popup: fetches the site's basin CSV, filters to this site, and lists
// the distinct contaminants (VARIABLE column) measured there.
// ---------------------------------------------------------------------
async function populateContaminantDropdown(site, container) {{
    container.innerHTML = '<div class="popup-loading">Loading contaminant list…</div>';

    let allRows;
    try {{
        allRows = await loadBasinCSV(site.basin_file);
    }} catch (err) {{
        container.innerHTML = `<div class="popup-error">
            Could not load ${{site.basin_file}}. Make sure the file exists in
            wq_basin_data/ and that this page was opened through the server
            started by <code>python3 generate_map.py</code>.
        </div>`;
        return;
    }}

    const siteRows = allRows.filter(r => (r.SITE_NO || "").trim() === site.site_no);
    if (siteRows.length === 0) {{
        container.innerHTML = '<div class="popup-loading">No measurements found for this site in its basin file.</div>';
        return;
    }}

    const allContaminants = Array.from(new Set(siteRows.map(r => (r.VARIABLE || "").trim())))
        .filter(v => v.length > 0)
        .sort();

    // Only list contaminants that actually clear the Seasonal Kendall
    // test's data bar (see seasonalKendallTest above — needs >=2 distinct
    // years of data in at least one calendar month). Anything that
    // wouldn't produce a result is pulled out of the dropdown and counted
    // separately instead, so the list only shows choices worth picking.
    const contaminants = [];
    const excluded = [];
    for (const c of allContaminants) {{
        const rows = siteRows.filter(r => (r.VARIABLE || "").trim() === c);
        const result = seasonalKendallTest(rows);
        (result.ok ? contaminants : excluded).push(c);
    }}

    const selectId = `contaminant-${{site.site_no}}`;
    const excludedListId = `excluded-list-${{site.site_no}}`;
    const excludedToggleId = `excluded-toggle-${{site.site_no}}`;

    const excludedHTML = excluded.length === 0 ? "" : `
        <div class="popup-excluded-note">
            <span>${{excluded.length}} contaminant${{excluded.length === 1 ? "" : "s"}} excluded (not enough data)</span>
            <button type="button" class="popup-excluded-toggle" id="${{excludedToggleId}}">Show</button>
        </div>
        <ul class="popup-excluded-list" id="${{excludedListId}}" style="display:none;">
            ${{excluded.map(c => `<li>${{c}}</li>`).join("")}}
        </ul>
    `;

    const dropdownHTML = contaminants.length === 0
        ? `<div class="popup-loading">No contaminants here have enough data for a trend test.</div>`
        : `
        <label class="popup-label" for="${{selectId}}">Contaminant (${{contaminants.length}} with enough data)</label>
        <select id="${{selectId}}" class="popup-contaminant-select">
            <option value="">— choose a contaminant —</option>
            ${{contaminants.map(c => `<option value="${{c.replace(/"/g, '&quot;')}}">${{c}}</option>`).join("")}}
        </select>
    `;

    container.innerHTML = `
        ${{dropdownHTML}}
        ${{excludedHTML}}
        <div id="trend-${{site.site_no}}"></div>
    `;

    if (excluded.length > 0) {{
        const toggleBtn = document.getElementById(excludedToggleId);
        const listEl = document.getElementById(excludedListId);
        toggleBtn.addEventListener('click', () => {{
            const nowHidden = listEl.style.display !== 'none';
            listEl.style.display = nowHidden ? 'none' : 'block';
            toggleBtn.textContent = nowHidden ? 'Show' : 'Hide';
        }});
    }}

    if (contaminants.length > 0) {{
        document.getElementById(selectId).addEventListener('change', (e) => {{
            const chosen = e.target.value;
            if (!chosen) return;
            const rows = siteRows.filter(r => (r.VARIABLE || "").trim() === chosen);
            onContaminantChosen(site, chosen, rows);
        }});
    }}
}}

// ---------------------------------------------------------------------
// Map setup
// ---------------------------------------------------------------------
// Keep the view inside Canada's extent (a little padding past the borders):
// panning/zooming out stops at these edges instead of drifting into the US.
const CANADA_BOUNDS = L.latLngBounds([[41, -142], [84, -52]]);
const map = L.map('map', {{
    zoomControl: true,
    // Required by the marker-cluster plugin: the vector basemap layer does not
    // declare a max zoom itself, so the map must.
    maxZoom: 18,
    maxBounds: CANADA_BOUNDS,
    maxBoundsViscosity: 1.0,
}}).setView([61, -98], 4);
function fitMinZoom() {{ map.setMinZoom(Math.min(map.getBoundsZoom(CANADA_BOUNDS), 4)); }}
fitMinZoom();
map.on('resize', fitMinZoom);

// OpenFreeMap vector basemap (Positron, light style) rendered by MapLibre GL
// inside Leaflet. No API key needed. Attribution must stay visible.
try {{
    L.maplibreGL({{
        style: 'https://tiles.openfreemap.org/styles/positron',
        attribution: '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> &copy; <a href="https://openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
    }}).addTo(map);
}} catch (err) {{
    console.error('Basemap failed to initialise:', err);
    showRuntimeError('Basemap failed to load: ' + err.message);
}}

const clusterGroup = L.markerClusterGroup();
const markerBySite = {{}};

function popupHTML(site) {{
    return `
        <div class="popup-title">${{site.site_name}}</div>
        <div class="popup-basin">${{site.basin_name}}</div>
        <div class="popup-row"><span class="popup-label">Site No:</span> ${{site.site_no}}</div>
        <div class="popup-row"><span class="popup-label">Coordinates:</span> ${{site.lat.toFixed(4)}}, ${{site.lon.toFixed(4)}}</div>
        <div id="contaminants-${{site.site_no}}" class="popup-data-section"></div>
    `;
}}

function makeMarker(site) {{
    const marker = L.marker([site.lat, site.lon]);
    marker.bindPopup(popupHTML(site), {{ maxWidth: 340, minWidth: 300 }});

    marker.on('popupopen', () => {{
        const container = document.getElementById(`contaminants-${{site.site_no}}`);
        if (!container) return;
        populateContaminantDropdown(site, container);
    }});

    markerBySite[site.site_no] = marker;
    return marker;
}}

SITES.forEach(site => clusterGroup.addLayer(makeMarker(site)));
map.addLayer(clusterGroup);

// ---------------------------------------------------------------------
// Basin filter dropdown
// ---------------------------------------------------------------------
const basinSelect = document.getElementById('basin-filter');
BASINS.forEach(b => {{
    const opt = document.createElement('option');
    opt.value = b;
    opt.textContent = b;
    basinSelect.appendChild(opt);
}});

function updateSiteCount(n) {{
    document.getElementById('site-count').textContent = `Showing ${{n}} site${{n === 1 ? '' : 's'}}`;
}}
updateSiteCount(SITES.length);

basinSelect.addEventListener('change', () => {{
    const chosen = basinSelect.value;
    clusterGroup.clearLayers();
    let shown = 0;
    SITES.forEach(site => {{
        if (chosen === '__all__' || site.basin_name === chosen) {{
            clusterGroup.addLayer(markerBySite[site.site_no]);
            shown++;
        }}
    }});
    updateSiteCount(shown);
}});
</script>

</body>
</html>
"""


def serve(html: str, port: int, open_browser: bool = True):
    """
    Serve the in-memory map page at / (and /index.html) and every other file
    (lib/, wq_basin_data/, ...) from SCRIPT_DIR, so the page's fetch() calls
    for basin CSVs work. Opens the map in the default browser, then blocks
    until Ctrl+C.
    """
    page = html.encode("utf-8")

    class MapHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(SCRIPT_DIR), **kwargs)

        def _is_page(self) -> bool:
            return self.path.split("?", 1)[0].split("#", 1)[0] in ("/", "/index.html")

        def _send_page(self, with_body: bool):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if with_body:
                self.wfile.write(page)

        def do_GET(self):
            if self._is_page():
                self._send_page(True)
            else:
                super().do_GET()

        def do_HEAD(self):
            if self._is_page():
                self._send_page(False)
            else:
                super().do_HEAD()

    try:
        httpd = http.server.ThreadingHTTPServer(("", port), MapHandler)
    except OSError as e:
        sys.exit(
            f"ERROR: couldn't start the local server on port {port} ({e}).\n"
            f"Something may already be using that port — try a different one:\n"
            f"    python3 {Path(__file__).name} --port {port + 1}"
        )

    url = f"http://localhost:{port}/"
    print(f"\nServing the map at {url}")
    print("Press Ctrl+C to stop the server.\n")

    if open_browser:
        # Small delay so the browser doesn't race the server's first request.
        threading.Timer(0.75, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000,
                         help="Port to serve on (default: 8000).")
    parser.add_argument("--export", action="store_true",
                         help="Write the map page to index.html (for GitHub Pages) and exit "
                              "instead of starting the local server.")
    parser.add_argument("--no-browser", action="store_true",
                         help="Start the server but don't auto-open a browser tab.")
    args = parser.parse_args()

    sites = load_sites(CSV_PATH)

    # Download every dependency up front (no-op if lib/ is already complete).
    if not ensure_local_libs():
        sys.exit("ERROR: could not download all required libraries into lib/ "
                 "(check your internet connection and re-run).")

    html = build_html(sites)

    print(f"Loaded {len(sites)} sites from {CSV_PATH.name}")
    print("Libraries: local (lib/ folder)")

    if args.export:
        EXPORT_PATH.write_text(html, encoding="utf-8")
        print(f"Wrote {EXPORT_PATH.name} — commit it together with lib/ and wq_basin_data/.")
        return

    serve(html, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
