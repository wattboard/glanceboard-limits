#!/usr/bin/env python3
"""Builds Glanceboard's speed-limit tiles from OpenStreetMap extracts.

Input: GeoJSON sequences of road ways (osmium export of the extracts, see build.sh).
Output (in ./site):
  13/<x>/<y>.json  one file per zoom-13 tile with roads that have a limit:
                   {"w": [[kmh, lat, lon, dlat, dlon, ...], ...]}
                   coordinates in 1e-5 degrees (about 1 m), each point after the first as a
                   difference from the one before.
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
    ways = 0
    inferred = 0
    for path in sys.argv[2:]:
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

    if os.path.isdir(out):
        shutil.rmtree(out)
    cover = set()
    total = 0
    for (x, y), entries in tiles.items():
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
        data = json.dumps({"w": enc}, separators=(",", ":"))
        total += len(data)
        with open(os.path.join(d, f"{y}.json"), "w") as f:
            f.write(data)
        shift = Z - COVER_Z
        cover.add(f"{x >> shift}/{y >> shift}")
    with open(os.path.join(out, "coverage.json"), "w") as f:
        json.dump({"z": COVER_Z, "t": sorted(cover)}, f, separators=(",", ":"))
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump({"v": 1, "z": Z, "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}, f)
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), out)
    print(f"{ways} ways ({inferred} with the urban default), {len(tiles)} tiles, {total / 1e6:.1f} MB, {len(cover)} coverage areas")


if __name__ == "__main__":
    main()
