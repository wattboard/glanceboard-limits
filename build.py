#!/usr/bin/env python3
"""Builds Glanceboard's speed-limit tiles from OpenStreetMap extracts.

Input: GeoJSON sequences of road ways (osmium export of the extracts, see build.sh).
Output (in ./site):
  13/<x>/<y>.json  one file per zoom-13 tile with roads that have a limit and fixed speed cameras:
                   {"w": [[kmh, lat, lon, dlat, dlon, ...], ...], "c": [[lat, lon, kmh, dir], ...]}
                   coordinates in 1e-5 degrees (about 1 m), each road point after the first as a
                   difference from the one before. Cameras: kmh 0 = not tagged, dir = degrees the
                   camera faces traffic from (-1 = not tagged). "c" is left out when there are none.
                   "s": [[lat1, lon1, lat2, lon2, kmh], ...] average-speed sections (DGT), in the tiles
                   of both ends; from start (lat1, lon1) to end, in the direction of traffic (DATEX "from" → "to"); kmh 0 = unknown.
  coverage.json    {"z": 9, "t": ["x/y", ...]}: zoom-11 areas built. Inside them a missing
                   tile means "no limits known here"; outside, the app asks another source.
  meta.json        when the data was built.

Usage: build.py OUT_DIR ways.geojsonseq [more.geojsonseq ...]
"""
import json
import math
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone

Z = 13
COVER_Z = 11

# Country defaults used in maxspeed / maxspeed:type / source:maxspeed / zone:maxspeed.
DEFAULTS = {
    "ES:urban": 50, "ES:rural": 90, "ES:motorway": 120, "ES:trunk": 100, "ES:living_street": 20,
    "ES:zone30": 30, "ES:zone20": 20, "ES:zone10": 10, "ES:school": 20,
    "PT:urban": 50, "PT:rural": 90, "PT:motorway": 120, "PT:trunk": 100, "PT:living_street": 20,
    "AD:urban": 50, "AD:rural": 90,
    "FR:urban": 50, "FR:rural": 80, "FR:motorway": 130, "FR:zone30": 30, "FR:walk": 6,
    "DE:urban": 50, "DE:rural": 100, "DE:zone30": 30, "DE:living_street": 7, "DE:bicycle_road": 30,
    "FI:urban": 50, "FI:rural": 80,
    "IT:urban": 50, "IT:rural": 90, "IT:motorway": 130,
    "GB:nsl_single": 97, "GB:nsl_dual": 113, "GB:motorway": 113,
}

ROADS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "service", "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link", "road", "busway",
}


def kmh(raw):
    """km/h for an OSM speed value ("50", "30 mph", "ES:urban", "50;30"), or None."""
    if not raw:
        return None
    tag = raw.strip()
    if tag in DEFAULTS:
        return DEFAULTS[tag]
    tag = tag.split(";")[0].strip()
    if tag in DEFAULTS:
        return DEFAULTS[tag]
    parts = tag.split()
    try:
        value = float(parts[0])
    except (ValueError, IndexError):
        return None
    if not 0 < value < 200:
        return None
    if len(parts) > 1 and parts[1].lower() == "mph":
        return round(value * 1.609344)
    return int(value)


def limit_of(tags):
    for key in ("maxspeed", "maxspeed:type", "source:maxspeed", "zone:maxspeed"):
        v = kmh(tags.get(key))
        if v:
            return v
    return None


# Country of each extract (file name), for the legal default on urban streets with no limit tagged.
COUNTRIES = {"spain": "ES", "portugal": "PT", "andorra": "AD"}


def lanes_each_way(tags):
    try:
        lanes = int(str(tags.get("lanes", "")).split(";")[0])
    except ValueError:
        return 1
    return lanes if tags.get("oneway") in ("yes", "1", "-1") else lanes // 2


def default_limit(tags, country):
    """The general limit of an urban street that has none tagged (most residential streets in OSM).

    Spain (since 2021): 20 on single-level streets, 30 with one lane each way, 50 with two or more.
    Portugal: 50 in towns, 20 in shared zones. Other roads are left out: they may be rural.
    """
    road = tags.get("highway")
    if road == "living_street":
        return 20 if country in ("ES", "PT") else None
    if road != "residential":
        return None
    if country == "ES":
        return 50 if lanes_each_way(tags) >= 2 else 30
    if country == "PT":
        return 50
    return None


COMPASS = {"N": 0, "NNE": 22, "NE": 45, "ENE": 67, "E": 90, "ESE": 112, "SE": 135, "SSE": 157,
           "S": 180, "SSW": 202, "SW": 225, "WSW": 247, "W": 270, "WNW": 292, "NW": 315, "NNW": 337}


def direction(raw):
    """Degrees 0-359 from an OSM direction tag ("90", "NE", "90;270" keeps the first), or -1."""
    if not raw:
        return -1
    tag = str(raw).split(";")[0].strip().upper()
    if tag in COMPASS:
        return COMPASS[tag]
    try:
        return int(float(tag)) % 360
    except ValueError:
        return -1


def read_cameras(path, cameras):
    """Adds the speed cameras of one export to cameras[(x, y)]."""
    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            geom = feature.get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            tags = feature.get("properties") or {}
            if tags.get("highway") != "speed_camera":
                continue
            lon, lat = geom["coordinates"]
            cameras[tile(lat, lon)].append([int(round(lat * 1e5)), int(round(lon * 1e5)),
                                            kmh(tags.get("maxspeed")) or 0, direction(tags.get("direction"))])
            count += 1
    return count


def read_dgt(path):
    """Fixed cameras (one point) and average-speed sections (two points) from the DGT's DATEX II file."""
    import re
    text = open(path, encoding="utf-8").read()
    points, sections = [], []
    for block in re.split(r"<_0:predefinedLocation id=", text)[1:]:
        pts = [(float(a), float(b)) for a, b in
               re.findall(r"<_0:latitude>([-\d.]+)</_0:latitude>\s*<_0:longitude>([-\d.]+)", block)]
        if len(pts) == 1:
            points.append(pts[0])
        elif len(pts) >= 2:
            sections.append((pts[1], pts[0]))   # listed as "to" then "from"
    return points, sections


def metres(a, b):
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0])))


def seg_distance(p, a, b):
    """Metres from p to segment a-b (flat approximation)."""
    k = 111_320 * math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * k, (a[0] - p[0]) * 111_320
    bx, by = (b[1] - p[1]) * k, (b[0] - p[0]) * 111_320
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0 if l2 == 0 else max(0, min(1, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy)


def road_limit(p, tiles, within=40):
    """Limit of the nearest road with one, for DGT entries (they carry none)."""
    best = None
    for limit, run in tiles.get(tile(*p), []):
        for a, b in zip(run, run[1:]):
            d = seg_distance(p, a, b)
            if d <= within and (best is None or d < best[0]):
                best = (d, limit)
    return best[1] if best else 0


def tile(lat, lon, z=Z):
    n = 1 << z
    lat = max(min(lat, 85.0511), -85.0511)
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def segment_tiles(a, b):
    """Tiles a segment touches, sampling about every 200 m so long ones are not missed."""
    (alat, alon), (blat, blon) = a, b
    dist = math.hypot((blat - alat) * 111_320, (blon - alon) * 111_320 * math.cos(math.radians(alat)))
    steps = max(1, int(dist / 200))
    return {tile(alat + (blat - alat) * i / steps, alon + (blon - alon) * i / steps) for i in range(steps + 1)}


def main():
    out = sys.argv[1]
    tiles = defaultdict(list)   # (x, y) -> list of [kmh, points]
    cameras = defaultdict(list)  # (x, y) -> list of [lat, lon, kmh, dir]
    ways = 0
    inferred = 0
    camera_count = 0
    dgt = None
    for path in sys.argv[2:]:
        if path.endswith(".xml"):
            dgt = read_dgt(path)
            continue
        if os.path.basename(path).endswith("-cameras.geojsonseq"):
            camera_count += read_cameras(path, cameras)
            continue
        country = COUNTRIES.get(os.path.basename(path).split(".")[0])
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                feature = json.loads(line)
                geom = feature.get("geometry") or {}
                if geom.get("type") != "LineString":
                    continue
                tags = feature.get("properties") or {}
                if tags.get("highway") not in ROADS:
                    continue
                limit = limit_of(tags)
                if not limit:
                    limit = default_limit(tags, country)
                    if not limit:
                        continue
                    inferred += 1
                pts = [(round(lat, 5), round(lon, 5)) for lon, lat in geom["coordinates"]]
                if len(pts) < 2:
                    continue
                ways += 1
                # Cut the way into one run per tile, so long roads are not copied whole everywhere.
                runs = {}
                for a, b in zip(pts, pts[1:]):
                    for t in segment_tiles(a, b):
                        run = runs.get(t)
                        if run and run[-1] == a:
                            run.append(b)
                        else:
                            if run and len(run) > 1:
                                tiles[t].append([limit, run])
                            runs[t] = [a, b]
                for t, run in runs.items():
                    if len(run) > 1:
                        tiles[t].append([limit, run])

    sections = defaultdict(list)
    dgt_added = 0
    if dgt:
        points, secs = dgt
        known = [(c[0] / 1e5, c[1] / 1e5) for cams in cameras.values() for c in cams]
        grid = defaultdict(list)
        for k in known:
            grid[tile(*k)].append(k)
        for p in points:
            # Already in OpenStreetMap (which also knows the direction and limit): keep that one.
            if any(metres(p, q) < 150 for q in grid.get(tile(*p), [])):
                continue
            cameras[tile(*p)].append([int(round(p[0] * 1e5)), int(round(p[1] * 1e5)), road_limit(p, tiles), -1])
            dgt_added += 1
        for a, b in secs:
            limit = road_limit(a, tiles) or road_limit(b, tiles)
            row = [int(round(a[0] * 1e5)), int(round(a[1] * 1e5)), int(round(b[0] * 1e5)), int(round(b[1] * 1e5)), limit]
            for t in {tile(*a), tile(*b)}:
                sections[t].append(row)
        print(f"DGT: {len(points)} fixed ({dgt_added} not in OpenStreetMap), {len(secs)} sections")

    if os.path.isdir(out):
        shutil.rmtree(out)
    cover = set()
    total = 0
    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for (x, y) in set(tiles) | set(cameras) | set(sections):
        entries = tiles.get((x, y), [])
        enc = []
        for limit, run in entries:
            row = [limit, int(round(run[0][0] * 1e5)), int(round(run[0][1] * 1e5))]
            plat, plon = row[1], row[2]
            for lat, lon in run[1:]:
                ilat, ilon = int(round(lat * 1e5)), int(round(lon * 1e5))
                row += [ilat - plat, ilon - plon]
                plat, plon = ilat, ilon
            enc.append(row)
        d = os.path.join(out, str(Z), str(x))
        os.makedirs(d, exist_ok=True)
        body = {"w": enc}
        if cameras.get((x, y)):
            body["c"] = cameras[(x, y)]
        if sections.get((x, y)):
            body["s"] = sections[(x, y)]
        data = json.dumps(body, separators=(",", ":"))
        total += len(data)
        with open(os.path.join(d, f"{y}.json"), "w") as f:
            f.write(data)
        shift = Z - COVER_Z
        cover.add(f"{x >> shift}/{y >> shift}")
    with open(os.path.join(out, "coverage.json"), "w") as f:
        # "b": build date; the app drops its saved tiles when it changes.
        json.dump({"z": COVER_Z, "b": built, "t": sorted(cover)}, f, separators=(",", ":"))
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump({"v": 1, "z": Z, "built": built}, f)
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), out)
    print(f"{ways} ways ({inferred} with the urban default), {camera_count} cameras, {len(set(tiles) | set(cameras) | set(sections))} tiles, {total / 1e6:.1f} MB, {len(cover)} coverage areas")


if __name__ == "__main__":
    main()
