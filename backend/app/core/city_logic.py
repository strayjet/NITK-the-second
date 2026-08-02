"""
city_logic.py

Consolidated, notebook-free version of the Smart Cities pipeline:
- Road network graph (OSMnx), with automatic Overpass-mirror retry
- Population data pipeline (WorldPop raster -> spread across graph nodes via KD-tree)
- Facility-location optimizer (population-favoring, p-median style) with a
  facility-type catalog and minimum-spacing enforcement against existing
  amenities
- Route simulation (population-avoiding, i.e. congestion-proxy avoidance),
  with curved-geometry route rendering
- Road-closure impact simulation
- Road-gap (new-road) suggestion: finds underserved shortcuts between
  populated areas that the current network forces long detours around

IMPORTANT: Fill in the CONFIG section below with your actual values
(area string, radius, raster file path) before running.
This file is imported by the FastAPI app via `app.services.city_service`, e.g.:

    from app.core.city_logic import initialize, get_route, simulate_closure_impact, recommend_facility_location

    G, node_population = initialize()
"""

import random
import numpy as np
import pandas as pd
import networkx as nx
import osmnx as ox
import rasterio
from rasterio.mask import mask
from shapely.geometry import box
import geopandas as gpd
from scipy.spatial import cKDTree

# ---------------------------------------------------------------------------
# CONFIG - edit these before running
# ---------------------------------------------------------------------------

AREA_QUERY = "Mumbai, Maharashtra, India"   # or "Surathkal, Karnataka, India"
GRAPH_RADIUS_M = 3000                       # radius in meters around the geocoded point
WORLDPOP_TIF_PATH = r"/app/data/worldpop.tif"  # overridden by CITY_LOGIC_WORLDPOP_TIF_PATH when run via the app; see app/config.py
KDTREE_RADIUS_DEG = 0.008                   # population-spreading radius (tune if coverage looks off)
CONGESTION_MULTIPLIER = 3                   # strength of population-avoidance penalty in routing
N_FACILITY_CANDIDATES = 50                  # how many nodes to test for facility-location optimization


# ---------------------------------------------------------------------------
# STEP 1: Road network graph
# ---------------------------------------------------------------------------

# Public Overpass API mirrors, tried in order. The default (overpass-api.de)
# has been unreliable on some networks — SSL handshake resets, connection
# aborts — that are network-level, not caused by your code or query. Rather
# than failing the whole server startup on the first mirror's hiccup, we
# retry against alternates automatically.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",       # default
    "https://overpass.kumi.systems/api/interpreter",  # alternate 1
    "https://overpass.openstreetmap.ru/api/interpreter",  # alternate 2
    "https://overpass.private.coffee/api/interpreter",  # alternate 3
]


def build_graph(area_query=AREA_QUERY, radius_m=GRAPH_RADIUS_M):
    """
    Builds a drivable road network graph centered on the given address/place.
    Tries each Overpass mirror in OVERPASS_MIRRORS in order — if one fails
    with a connection/SSL error (common on networks that reset connections
    to specific hosts), automatically retries against the next mirror
    instead of crashing the whole server startup.
    """
    last_error = None
    for mirror in OVERPASS_MIRRORS:
        ox.settings.overpass_url = mirror
        try:
            print(f"  Trying Overpass mirror: {mirror}")
            G = ox.graph_from_address(area_query, dist=radius_m, network_type="drive")
            print(f"  Success via {mirror}")
            return G
        except Exception as e:
            print(f"  Mirror {mirror} failed: {type(e).__name__}: {e}")
            last_error = e
            continue

    raise RuntimeError(
        f"All Overpass mirrors failed to build the road graph. This is almost "
        f"certainly a network-level issue (SSL inspection, firewall, or a "
        f"blocked/reset connection to these hosts) rather than a code bug — "
        f"try a different network (e.g. mobile hotspot) to confirm. "
        f"Last error: {last_error}"
    ) from last_error


def get_graph_bbox(G):
    """Returns (west, south, east, north) bounding box of the graph's nodes."""
    nodes, _ = ox.graph_to_gdfs(G)
    west, south, east, north = nodes.total_bounds
    return west, south, east, north


# ---------------------------------------------------------------------------
# STEP 2: Population raster clip
# ---------------------------------------------------------------------------

def clip_population_raster(bbox, tif_path=WORLDPOP_TIF_PATH):
    """
    Clips the WorldPop raster to the given bounding box.
    Returns (out_image, out_transform, nodata_val).
    """
    west, south, east, north = bbox
    bbox_geom = gpd.GeoDataFrame(
        {"geometry": [box(west, south, east, north)]}, crs="EPSG:4326"
    )

    with rasterio.open(tif_path) as src:
        out_image, out_transform = mask(src, bbox_geom.geometry, crop=True)
        nodata_val = src.nodata

    return out_image, out_transform, nodata_val


def extract_valid_population_cells(out_image, out_transform, nodata_val):
    """
    Returns (xs, ys, pops) arrays of valid (non-nodata) population cell
    centers and their population values.
    """
    valid_mask = out_image[0] != nodata_val
    rows, cols = np.where(valid_mask)

    xs, ys = rasterio.transform.xy(out_transform, rows, cols)
    xs = np.array(xs)
    ys = np.array(ys)
    pops = out_image[0][rows, cols]

    return xs, ys, pops


# ---------------------------------------------------------------------------
# STEP 3: Spread population onto graph nodes (KD-tree radius method)
# ---------------------------------------------------------------------------

def build_node_population(G, xs, ys, pops, radius_deg=KDTREE_RADIUS_DEG):
    """
    Spreads each raster cell's population across every graph node within
    radius_deg, rather than only the single nearest node. This avoids the
    severe data sparsity you get from pure nearest-node aggregation when
    raster resolution is coarse relative to node density.
    """
    node_ids = list(G.nodes)
    node_coords = np.array([(G.nodes[n]['x'], G.nodes[n]['y']) for n in node_ids])
    tree = cKDTree(node_coords)

    node_pop_totals = {n: 0.0 for n in node_ids}

    for x, y, pop in zip(xs, ys, pops):
        nearby_idx = tree.query_ball_point([x, y], r=radius_deg)
        if len(nearby_idx) == 0:
            continue
        share = pop / len(nearby_idx)
        for idx in nearby_idx:
            node_pop_totals[node_ids[idx]] += share

    return pd.Series(node_pop_totals)


# ---------------------------------------------------------------------------
# STEP 4: Population-aware edge weights (for ROUTING - avoidance)
# ---------------------------------------------------------------------------

def assign_edge_weights(G, node_population, congestion_multiplier=CONGESTION_MULTIPLIER):
    """
    Assigns two extra attributes to every edge:
    - pop_weight: average population of the edge's two endpoint nodes
    - combined_weight: length scaled up by a population-based congestion
      factor, used for ROUTE avoidance of high-population (proxy-congested)
      areas. Facility-location optimization does NOT use this - it uses
      raw node_population directly, since it wants to favor density, not
      avoid it.
    """
    for u, v, key, data in G.edges(keys=True, data=True):
        pop_u = node_population.get(u, 0)
        pop_v = node_population.get(v, 0)
        data["pop_weight"] = (pop_u + pop_v) / 2

    max_pop = max(data["pop_weight"] for _, _, data in G.edges(data=True))
    if max_pop == 0:
        max_pop = 1  # guard against div-by-zero if population data is empty

    for u, v, key, data in G.edges(keys=True, data=True):
        congestion_factor = 1 + congestion_multiplier * (data["pop_weight"] / max_pop)
        data["combined_weight"] = data["length"] * congestion_factor

    return G


# ---------------------------------------------------------------------------
# STEP 5: Routing (population-avoiding)
# ---------------------------------------------------------------------------

def get_route(G, origin_coords, dest_coords):
    """
    origin_coords / dest_coords: (lat, lon) tuples.
    Routes using combined_weight (population-avoidance) but reports the
    REAL physical distance (raw "length"), not the blended optimization
    weight - those are not meant to be the same number.
    """
    orig_node = ox.distance.nearest_nodes(G, X=origin_coords[1], Y=origin_coords[0])
    dest_node = ox.distance.nearest_nodes(G, X=dest_coords[1], Y=dest_coords[0])

    route = nx.shortest_path(G, orig_node, dest_node, weight="combined_weight")
    route_length_m = nx.shortest_path_length(G, orig_node, dest_node, weight="length")

    return route, route_length_m


def route_to_coords(G, route):
    """
    DEPRECATED for frontend rendering — connects only intersection nodes
    with straight lines, which cuts across curves since OSMnx graphs are
    simplified (only real intersections are nodes; curve detail lives in
    each edge's 'geometry' attribute). Use route_nodes_to_coords instead
    for anything drawn on a map. Kept here since some quick debugging
    scripts may still reference it.
    """
    return [[G.nodes[n]['y'], G.nodes[n]['x']] for n in route]


def haversine_km(lat1, lon1, lat2, lon2):
    """
    Great-circle distance between two lat/lon points, in km.
    Implemented directly (no geopy dependency) since this needs to run
    fast, in a loop, against every candidate x every existing amenity.
    """
    R = 6371.0  # Earth's radius in km
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def route_nodes_to_coords(G, route_nodes):
    """
    Converts a route (list of node IDs) into a detailed list of [lat, lon]
    points that follows the real road curvature, using each edge's stored
    'geometry' LineString instead of just straight lines between
    intersection nodes.

    IMPORTANT: OSM stores each edge's geometry in the direction of the
    original OSM way, which does NOT necessarily match the direction you're
    travelling in this route (u -> v). If left unchecked, this makes the
    drawn line jump backward at some edges, producing a path that visibly
    zig-zags instead of smoothly tracing the road. We fix this by checking
    which end of the stored geometry is actually closer to u, and reversing
    the point order when needed.
    """
    coords = []
    for u, v in zip(route_nodes[:-1], route_nodes[1:]):
        edge_data = G.get_edge_data(u, v)[0]  # first parallel edge if multiple exist
        u_lat, u_lon = G.nodes[u]["y"], G.nodes[u]["x"]

        if "geometry" in edge_data:
            xs, ys = edge_data["geometry"].xy
            pts = list(zip(ys, xs))  # (lat, lon) order

            # decide orientation: is the geometry's first point actually
            # closer to node u, or to node v? if it's closer to v, the
            # stored line runs backward relative to our direction of
            # travel, so reverse it.
            start_dist = haversine_km(u_lat, u_lon, pts[0][0], pts[0][1])
            end_dist = haversine_km(u_lat, u_lon, pts[-1][0], pts[-1][1])
            if end_dist < start_dist:
                pts = pts[::-1]

            coords.extend(pts)
        else:
            coords.append((u_lat, u_lon))
            coords.append((G.nodes[v]["y"], G.nodes[v]["x"]))

    return [[lat, lon] for lat, lon in coords]


# ---------------------------------------------------------------------------
# STEP 5b: Point -> edge snapping (map-click road selection)
# ---------------------------------------------------------------------------

def get_edge_coords(G, u, v):
    """
    Returns the [lat, lon] point list tracing a single edge's real curvature
    in the u -> v direction, using the same geometry-orientation fix as
    route_nodes_to_coords (stored OSM geometry direction doesn't always
    match u -> v, so we flip it when needed). Falls back to a straight
    2-point line if the edge has no stored geometry.
    """
    edge_data = G.get_edge_data(u, v)[0]  # first parallel edge if multiple exist
    u_lat, u_lon = G.nodes[u]["y"], G.nodes[u]["x"]
    v_lat, v_lon = G.nodes[v]["y"], G.nodes[v]["x"]

    if "geometry" in edge_data:
        xs, ys = edge_data["geometry"].xy
        pts = list(zip(ys, xs))  # (lat, lon) order

        start_dist = haversine_km(u_lat, u_lon, pts[0][0], pts[0][1])
        end_dist = haversine_km(u_lat, u_lon, pts[-1][0], pts[-1][1])
        if end_dist < start_dist:
            pts = pts[::-1]

        return [[lat, lon] for lat, lon in pts]

    return [[u_lat, u_lon], [v_lat, v_lon]]


def nearest_edge(G, lat, lon):
    """
    Snaps a clicked map point to the nearest existing road-network edge.

    Powers the map-first road-closure picker: instead of a user typing a
    raw (u, v) OSM node-id pair, the frontend sends the lat/lon of a map
    click and gets back the real edge (id pair + curved geometry) to
    highlight and, if confirmed, close.
    """
    u, v, _key = ox.distance.nearest_edges(G, X=lon, Y=lat)
    u_lat, u_lon = G.nodes[u]["y"], G.nodes[u]["x"]
    v_lat, v_lon = G.nodes[v]["y"], G.nodes[v]["x"]

    # Cheap "how close was the click to an actual road" signal: distance to
    # the nearer of the edge's two endpoint nodes. Good enough to flag a
    # bad snap without the cost of projecting onto the full curve.
    distance_to_point_m = (
        min(
            haversine_km(lat, lon, u_lat, u_lon),
            haversine_km(lat, lon, v_lat, v_lon),
        )
        * 1000
    )

    return {
        "node_u": int(u),
        "node_v": int(v),
        "point_u": [u_lat, u_lon],
        "point_v": [v_lat, v_lon],
        "geometry": get_edge_coords(G, u, v),
        "distance_to_point_m": distance_to_point_m,
    }


# ---------------------------------------------------------------------------
# STEP 5c: Full road network (for "Road Closure Simulation" map mode)
# ---------------------------------------------------------------------------

def get_all_edges(G):
    """
    Returns every edge in the graph as a lightweight, JSON-friendly dict,
    for rendering the entire road network as an interactive vector layer
    (as opposed to `nearest_edge`, which resolves a single edge from a
    clicked coordinate).

    Only the first parallel edge per (u, v) pair is included (same
    convention used everywhere else in this module, e.g. route_nodes_to_coords
    / get_edge_coords via `G.get_edge_data(u, v)[0]`) so the count here
    matches what closure/routing logic actually operates on.

    Each item's `edge_id` is the "u-v" string — stable for the lifetime of
    the in-memory graph and directly usable as a (u, v) pair for
    `simulate_closure_impact` without any extra lookup.
    """
    edges = []
    seen = set()
    for u, v, key, data in G.edges(keys=True, data=True):
        if (u, v) in seen:
            continue  # skip parallel edges beyond the first, same as elsewhere in this module
        seen.add((u, v))

        edges.append({
            "edge_id": f"{u}-{v}",
            "node_u": int(u),
            "node_v": int(v),
            "point_u": [G.nodes[u]["y"], G.nodes[u]["x"]],
            "point_v": [G.nodes[v]["y"], G.nodes[v]["x"]],
            "geometry": get_edge_coords(G, u, v),
            "length_m": data.get("length", 0.0),
            "name": data.get("name") if isinstance(data.get("name"), str) else None,
            "highway": data.get("highway") if isinstance(data.get("highway"), str) else None,
        })

    return edges


# ---------------------------------------------------------------------------
# STEP 6: Road-closure simulation
# ---------------------------------------------------------------------------

def simulate_closure_impact(G, origin_coords, dest_coords, edge_to_close):
    """
    edge_to_close: (u, v) node ID tuple to remove from a COPY of the graph.
    Never mutates the original G - every other module depends on it staying intact.
    """
    route_before, length_before = get_route(G, origin_coords, dest_coords)

    G_modified = G.copy()
    G_modified.remove_edge(*edge_to_close)

    try:
        route_after, length_after = get_route(G_modified, origin_coords, dest_coords)
    except nx.NetworkXNoPath:
        return {"status": "no alternative route exists - destination fully cut off"}

    return {
        "route_before": route_before,
        "length_before_km": length_before / 1000,
        "route_after": route_after,
        "length_after_km": length_after / 1000,
        "delay_km": (length_after - length_before) / 1000,
    }


# ---------------------------------------------------------------------------
# STEP 6b: Persistent road closures ("Block Road" mode)
# ---------------------------------------------------------------------------
#
# Unlike `simulate_closure_impact` (a one-off "what if?" comparison of a
# single before/after route), this powers a standing set of blocked roads
# that stays in effect - across every routing calculation, for every user -
# until it is explicitly cleared. See `CityService` for the persistent
# in-memory bookkeeping; the functions here only ever operate on a fresh
# copy of the graph, so the master graph (`CityService.graph`) is never
# mutated and every other module that depends on it staying intact keeps
# working unchanged.

def remove_blocked_edges(G, blocked_edges):
    """
    Returns a COPY of G with every edge in `blocked_edges` removed from
    routing calculations. Never mutates G in place.

    blocked_edges: iterable of (u, v) node-id pairs. A real-world road
    closure blocks travel in both directions, so both (u, v) and (v, u)
    are removed whenever present - the caller only needs to track one
    canonical direction per closed road.

    All parallel edges between a blocked (u, v) pair are removed (same
    "first parallel edge" convention used everywhere else in this module),
    so a blocked road can never still be found by the router.
    """
    if not blocked_edges:
        return G

    G2 = G.copy()
    for u, v in blocked_edges:
        for a, b in ((u, v), (v, u)):
            if G2.has_edge(a, b):
                # A MultiDiGraph can have several parallel edges between the
                # same pair of nodes; remove all of them, not just the first.
                keys = list(G2.get_edge_data(a, b).keys())
                for key in keys:
                    G2.remove_edge(a, b, key=key)
    return G2


# ---------------------------------------------------------------------------
# STEP 7: Facility-location optimization (population-FAVORING, p-median style)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Facility spacing constraints
# ---------------------------------------------------------------------------

# OSM's amenity tag has 100+ possible values (see wiki.openstreetmap.org/
# wiki/Key:amenity for the full list). This covers the subset that's
# practically relevant to city infrastructure planning - easy to extend by
# adding another entry in the same shape.
#
# Each entry: facility_type -> { "tags": OSM query tag, "label": display name,
#                                  "color": hex color used for both the map
#                                  marker and the spacing-radius circle,
#                                  "small": True if this should use the
#                                  tighter 2km spacing instead of 4km }
FACILITY_CATALOG = {
    # --- Emergency / civic (default 4km spacing) ---
    "hospital":       {"tags": {"amenity": "hospital"},       "label": "Hospital",        "color": "#e0522f", "small": False},
    "fire_station":    {"tags": {"amenity": "fire_station"},   "label": "Fire station",     "color": "#e0a72f", "small": False},
    "police":          {"tags": {"amenity": "police"},         "label": "Police station",   "color": "#3b82f6", "small": False},
    "school":          {"tags": {"amenity": "school"},         "label": "School",           "color": "#4ade80", "small": False},
    "university":      {"tags": {"amenity": "university"},     "label": "University/college","color": "#22d3ee", "small": False},
    "library":         {"tags": {"amenity": "library"},        "label": "Library",          "color": "#a78bfa", "small": False},
    "community_centre":{"tags": {"amenity": "community_centre"},"label": "Community centre","color": "#fb923c", "small": False},
    "post_office":     {"tags": {"amenity": "post_office"},    "label": "Post office",      "color": "#f472b6", "small": False},

    # --- Healthcare, smaller-scale (2km spacing) ---
    "clinic":          {"tags": {"amenity": "clinic"},         "label": "Clinic",           "color": "#f97316", "small": True},
    "medical_store":   {"tags": {"amenity": "pharmacy"},       "label": "Pharmacy / medical store","color": "#84cc16", "small": True},
    "dentist":         {"tags": {"amenity": "dentist"},        "label": "Dentist",          "color": "#06b6d4", "small": True},
    "veterinary":      {"tags": {"amenity": "veterinary"},     "label": "Veterinary clinic", "color": "#a3e635", "small": True},

    # --- Everyday amenities (2km spacing) ---
    "bank":            {"tags": {"amenity": "bank"},           "label": "Bank",             "color": "#eab308", "small": True},
    "atm":             {"tags": {"amenity": "atm"},            "label": "ATM",              "color": "#facc15", "small": True},
    "fuel_station":    {"tags": {"amenity": "fuel"},           "label": "Fuel station",     "color": "#78716c", "small": True},
    "restaurant":      {"tags": {"amenity": "restaurant"},     "label": "Restaurant",       "color": "#f43f5e", "small": True},
    "cafe":            {"tags": {"amenity": "cafe"},           "label": "Cafe",             "color": "#d97706", "small": True},
    "marketplace":     {"tags": {"amenity": "marketplace"},    "label": "Marketplace",      "color": "#65a30d", "small": True},
    "place_of_worship":{"tags": {"amenity": "place_of_worship"},"label": "Place of worship","color": "#c084fc", "small": True},

    # --- Transit / parking (4km spacing) ---
    "bus_station":     {"tags": {"amenity": "bus_station"},    "label": "Bus station",      "color": "#0ea5e9", "small": False},
    "parking":         {"tags": {"amenity": "parking"},        "label": "Parking facility",  "color": "#94a3b8", "small": True},
    "charging_station":{"tags": {"amenity": "charging_station"},"label": "EV charging station","color": "#10b981", "small": True},

    # --- Waste / utilities (4km spacing) ---
    "waste_disposal":  {"tags": {"amenity": "waste_disposal"}, "label": "Waste disposal site","color": "#57534e", "small": False},
    "recycling":       {"tags": {"amenity": "recycling"},      "label": "Recycling centre",  "color": "#059669", "small": True},

    # --- Recreation (4km spacing) ---
    "park":            {"tags": {"leisure": "park"},           "label": "Park",             "color": "#22c55e", "small": False},
    "playground":      {"tags": {"leisure": "playground"},     "label": "Playground",       "color": "#fbbf24", "small": True},
    "sports_centre":   {"tags": {"leisure": "sports_centre"},  "label": "Sports centre",    "color": "#818cf8", "small": False},
}

# Kept for backward compatibility with code that only reads tags/spacing,
# derived automatically from FACILITY_CATALOG so there's a single source
# of truth instead of two lists that can drift out of sync.
FACILITY_TAG_MAP = {k: v["tags"] for k, v in FACILITY_CATALOG.items()}
SMALL_AMENITY_TYPES = {k for k, v in FACILITY_CATALOG.items() if v["small"]}

DEFAULT_MIN_SPACING_KM = 4.0
SMALL_AMENITY_MIN_SPACING_KM = 2.0


def get_min_spacing_km(facility_type):
    if facility_type in SMALL_AMENITY_TYPES:
        return SMALL_AMENITY_MIN_SPACING_KM
    return DEFAULT_MIN_SPACING_KM


def get_all_facility_types():
    """
    Returns the full facility catalog in a shape the frontend can use
    directly to populate its dropdown and to color markers/circles
    consistently with the backend's spacing logic.
    """
    return [
        {
            "value": key,
            "label": entry["label"],
            "color": entry["color"],
            "min_spacing_km": SMALL_AMENITY_MIN_SPACING_KM if entry["small"] else DEFAULT_MIN_SPACING_KM,
        }
        for key, entry in FACILITY_CATALOG.items()
    ]


# Cache of existing-amenity coordinates, keyed by facility_type. Populated
# on first request per type, reused after that — avoids re-querying OSM's
# Overpass API (a slow network call) on every single /recommend request.
_existing_amenity_cache = {}


def get_existing_amenity_coords(bbox, facility_type):
    """
    Queries OSM for existing amenities of the given facility_type within
    the given bbox (west, south, east, north) — NOT the whole city/area
    name, which would be a much larger, slower query covering far more
    ground than the graph actually spans. Cached per facility_type after
    the first SUCCESSFUL call only — a failed query (network error, SSL
    issue, Overpass timeout) does NOT get cached, so a transient failure
    doesn't permanently disable the spacing constraint for that type until
    the server restarts. This was a real bug: the old version cached empty
    results on exceptions too, so one bad network moment silently turned
    off spacing enforcement for the rest of the server's life.
    """
    if facility_type in _existing_amenity_cache:
        return _existing_amenity_cache[facility_type]

    if facility_type not in FACILITY_TAG_MAP:
        # unrecognized type, not a failure — this is fine to cache as
        # permanently empty since it won't change on retry
        _existing_amenity_cache[facility_type] = []
        return []

    tags = FACILITY_TAG_MAP[facility_type]
    west, south, east, north = bbox
    try:
        pois = ox.features_from_bbox((west, south, east, north), tags)
    except Exception as e:
        # Do NOT cache on failure — this was the bug. A network hiccup
        # should be retried next call, not remembered as "no facilities
        # exist" forever. Spacing constraint is skipped for THIS call only.
        print(f"[get_existing_amenity_coords] query failed for '{facility_type}': {e}. "
              f"Not caching — will retry on next request.")
        return []

    coords = []
    for geom in pois.geometry:
        centroid = geom.centroid
        coords.append((centroid.y, centroid.x))

    _existing_amenity_cache[facility_type] = coords  # only cache on real success
    return coords


def filter_candidates_by_spacing(candidates, G, existing_coords, min_spacing_km):
    """
    Removes any candidate node that falls within min_spacing_km of an
    existing amenity of the same type.
    """
    if not existing_coords:
        return candidates  # no existing data to check against - allow all

    valid = []
    for c in candidates:
        c_lat, c_lon = G.nodes[c]['y'], G.nodes[c]['x']
        too_close = False
        for ex_lat, ex_lon in existing_coords:
            if haversine_km(c_lat, c_lon, ex_lat, ex_lon) < min_spacing_km:
                too_close = True
                break
        if not too_close:
            valid.append(c)

    return valid


def recommend_facility_location(G, node_population, facility_type="hospital", n_candidates=N_FACILITY_CANDIDATES):
    """
    Picks candidate nodes, computes population-weighted shortest-path
    cost to every demand node, and returns the candidate that minimizes it -
    while enforcing a minimum spacing distance from existing facilities of
    the same type (facility_type), so recommendations don't cluster right
    next to something that already exists.

    Disqualifies candidates that can't reach at least half the demand nodes
    (handles directed-graph reachability gaps rather than silently returning
    a bogus cost of 0 for unreachable candidates).
    """
    demand_nodes = node_population.index.tolist()
    demand_weights = node_population.values

    min_spacing_km = get_min_spacing_km(facility_type)
    bbox = get_graph_bbox(G)
    existing_coords = get_existing_amenity_coords(bbox, facility_type)

    # sample candidates, filtering by spacing constraint; if too few survive,
    # widen the sample rather than silently returning a poor/empty result
    all_nodes = list(G.nodes)
    attempt_size = n_candidates
    max_attempts = 3
    valid_candidates = []

    for attempt in range(max_attempts):
        sampled = random.sample(all_nodes, min(attempt_size, len(all_nodes)))
        valid_candidates = filter_candidates_by_spacing(sampled, G, existing_coords, min_spacing_km)
        if len(valid_candidates) >= min(10, n_candidates):
            break
        attempt_size *= 2  # widen the net and try again, capped to avoid runaway Dijkstra cost

    if not valid_candidates:
        return {
            "status": "no_valid_site",
            "detail": f"No candidate site found at least {min_spacing_km}km from an existing "
                      f"{facility_type.replace('_', ' ')}. Existing facilities may be too dense "
                      f"in this area, or the sample size may need to be increased."
        }

    def weighted_cost(candidate):
        lengths = nx.single_source_dijkstra_path_length(G, candidate, weight="length")
        reached = [n for n in demand_nodes if n in lengths]
        if len(reached) < 0.5 * len(demand_nodes):
            return float("inf")
        total = sum(
            lengths[n] * w for n, w in zip(demand_nodes, demand_weights) if n in lengths
        )
        return total

    costs = {c: weighted_cost(c) for c in valid_candidates}
    best_site = min(costs, key=costs.get)
    catalog_entry = FACILITY_CATALOG.get(facility_type, {})
    best_lat, best_lon = G.nodes[best_site]['y'], G.nodes[best_site]['x']

    # Display-only stats for the recommendation UI (population served, nearby
    # existing facilities, coverage improvement, confidence). Purely additive
    # — computed from data the function above already has in hand, without
    # touching the site-selection algorithm itself.
    display_stats = _compute_recommendation_display_stats(
        G, node_population, best_lat, best_lon, existing_coords, min_spacing_km, costs
    )

    return {
        "node_id": best_site,
        "lat": best_lat,
        "lon": best_lon,
        "cost": costs[best_site],
        "facility_type": facility_type,
        "label": catalog_entry.get("label", facility_type),
        "color": catalog_entry.get("color", "#a855f7"),
        "min_spacing_km": min_spacing_km,
        "existing_facilities_considered": len(existing_coords),
        "all_costs": costs,
        **display_stats,
    }


def _compute_recommendation_display_stats(G, node_population, best_lat, best_lon, existing_coords, radius_km, costs):
    """
    Additional, read-only figures for the facility-recommendation UI:

    - population_served: population within the coverage radius of the
      recommended site.
    - coverage_improvement_pct: share of that population that is NOT
      already within the same radius of an existing same-type facility
      (i.e. newly brought into coverage by this recommendation).
    - nearby_facilities: existing same-type facilities within 3x the
      coverage radius, nearest-first, for map context.
    - confidence_score: 0-1 score based on how much better the chosen
      site's access cost is than the average of the other evaluated
      candidates (higher margin = higher confidence).
    """
    population_served = 0.0
    newly_covered = 0.0
    for node_id, pop in node_population.items():
        if not pop:
            continue
        try:
            lat, lon = G.nodes[node_id]['y'], G.nodes[node_id]['x']
        except KeyError:
            continue
        if haversine_km(best_lat, best_lon, lat, lon) <= radius_km:
            population_served += pop
            already_covered = any(
                haversine_km(ex_lat, ex_lon, lat, lon) <= radius_km for ex_lat, ex_lon in existing_coords
            )
            if not already_covered:
                newly_covered += pop

    coverage_improvement_pct = (newly_covered / population_served * 100.0) if population_served > 0 else 0.0

    nearby = sorted(
        (
            {"lat": ex_lat, "lon": ex_lon, "distance_km": round(haversine_km(best_lat, best_lon, ex_lat, ex_lon), 3)}
            for ex_lat, ex_lon in existing_coords
        ),
        key=lambda item: item["distance_km"],
    )
    nearby_facilities = [n for n in nearby if n["distance_km"] <= radius_km * 3][:15]

    finite_costs = [c for c in costs.values() if c != float("inf")]
    best_cost = min(finite_costs) if finite_costs else float("inf")
    if len(finite_costs) > 1 and best_cost != float("inf"):
        avg_other = sum(finite_costs) / len(finite_costs)
        confidence_score = max(0.0, min(1.0, (avg_other - best_cost) / avg_other)) if avg_other > 0 else 0.5
    else:
        confidence_score = 0.5

    return {
        "population_served": round(population_served),
        "coverage_improvement_pct": round(coverage_improvement_pct, 1),
        "nearby_facilities": nearby_facilities,
        "confidence_score": round(confidence_score, 2),
    }


# ---------------------------------------------------------------------------
# STEP 8: New road suggestions (network augmentation)
# ---------------------------------------------------------------------------

def find_road_gap_candidates(G, node_population=None, sample_size=100,
                               min_straight_line_m=200, max_straight_line_m=2000):
    """
    Finds node pairs where straight-line distance is small but network
    (road) distance is much larger - indicating a missing direct connection.
    Also stores the actual current path (list of node IDs) so callers can
    render the real, curve-following detour route, not just the two
    endpoints.

    If node_population is provided, candidate nodes are sampled with
    probability proportional to their population weight, rather than
    uniformly at random. This means proposed roads are biased toward
    connecting areas people actually live in/near, rather than being
    equally likely to suggest a shortcut through a sparsely populated
    area — a plain uniform sample has no concept of where the road would
    actually be useful to anyone.
    """
    from itertools import combinations

    nodes = list(G.nodes)

    if node_population is not None and len(node_population) > 0:
        weights = np.array([node_population.get(n, 0) for n in nodes], dtype=float)
        if weights.sum() > 0:
            probs = weights / weights.sum()
            sampled_nodes = np.random.choice(
                nodes, size=min(sample_size, len(nodes)), replace=False, p=probs
            )
        else:
            sampled_nodes = np.random.choice(nodes, size=min(sample_size, len(nodes)), replace=False)
    else:
        sampled_nodes = np.random.choice(nodes, size=min(sample_size, len(nodes)), replace=False)

    candidates = []
    for u, v in combinations(sampled_nodes, 2):
        y1, x1 = G.nodes[u]['y'], G.nodes[u]['x']
        y2, x2 = G.nodes[v]['y'], G.nodes[v]['x']
        straight_line_m = ox.distance.great_circle(y1, x1, y2, x2)

        if not (min_straight_line_m <= straight_line_m <= max_straight_line_m):
            continue
        if G.has_edge(u, v):
            continue

        try:
            current_path = nx.shortest_path(G, u, v, weight="length")
            network_dist = nx.shortest_path_length(G, u, v, weight="length")
        except nx.NetworkXNoPath:
            continue

        gap_ratio = network_dist / straight_line_m
        candidates.append((u, v, straight_line_m, network_dist, gap_ratio, current_path))

    return candidates


def score_candidates(G, candidates, node_population, top_n=10):
    """Scores candidate road gaps by population-weighted distance saved."""
    scored = []
    for u, v, straight_line_m, network_dist, gap_ratio, current_path in candidates:
        pop_u = node_population.get(u, 0)
        pop_v = node_population.get(v, 0)
        distance_saved = network_dist - straight_line_m
        impact_score = distance_saved * (pop_u + pop_v)
        scored.append({
            "node_u": u, "node_v": v,
            "straight_line_m": straight_line_m,
            "current_network_dist_m": network_dist,
            "gap_ratio": gap_ratio,
            "distance_saved_m": distance_saved,
            "impact_score": impact_score,
            "current_path": current_path,
        })

    scored.sort(key=lambda x: x["impact_score"], reverse=True)
    return scored[:top_n]


# ---------------------------------------------------------------------------
# INITIALIZATION - run the full pipeline once, e.g. at FastAPI startup
# ---------------------------------------------------------------------------

def initialize(
    area_query: str = AREA_QUERY,
    radius_m: int = GRAPH_RADIUS_M,
    tif_path: str = WORLDPOP_TIF_PATH,
    kdtree_radius_deg: float = KDTREE_RADIUS_DEG,
    congestion_multiplier: float = CONGESTION_MULTIPLIER,
):
    """
    Runs the full pipeline once: build graph, clip population raster,
    spread population to nodes, assign edge weights. Returns everything
    downstream code (FastAPI endpoints, the AI copilot) needs.

    All parameters default to the module-level CONFIG constants, so calling
    `initialize()` with no arguments behaves exactly as before. The backend
    passes its own (env-configurable) settings through these parameters
    instead of editing this file directly.
    """
    print("Building road network graph...")
    G = build_graph(area_query=area_query, radius_m=radius_m)

    print("Clipping population raster...")
    bbox = get_graph_bbox(G)
    out_image, out_transform, nodata_val = clip_population_raster(bbox, tif_path=tif_path)
    xs, ys, pops = extract_valid_population_cells(out_image, out_transform, nodata_val)

    print("Spreading population onto graph nodes...")
    node_population = build_node_population(G, xs, ys, pops, radius_deg=kdtree_radius_deg)

    print("Assigning population-aware edge weights...")
    G = assign_edge_weights(G, node_population, congestion_multiplier=congestion_multiplier)

    print(f"Done. Graph has {len(G.nodes)} nodes, {len(G.edges)} edges.")
    print(f"Population coverage: {(node_population == 0).mean()*100:.1f}% nodes at zero.")

    return G, node_population


if __name__ == "__main__":
    # quick manual test when running this file directly
    G, node_population = initialize()

    result = recommend_facility_location(G, node_population)
    print("\nFacility recommendation:", result)

    high_pop_node = node_population.idxmax()
    low_pop_node = node_population.idxmin()
    low_coords = (G.nodes[low_pop_node]['y'], G.nodes[low_pop_node]['x'])
    high_coords = (G.nodes[high_pop_node]['y'], G.nodes[high_pop_node]['x'])

    route, length_m = get_route(G, low_coords, high_coords)
    print(f"\nPopulation-aware route: {len(route)} nodes, {length_m/1000:.2f} km")
