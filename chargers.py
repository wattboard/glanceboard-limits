#!/usr/bin/env python3
"""Fast chargers for Glanceboard's trip planner, from OpenStreetMap (amenity=charging_station).

Usage: chargers.py OUT.json INPUT.geojsonseq...  (osmium export of the charging stations, see build.sh)

Writes {"built": "YYYY-MM-DD", "c": [[lat, lon, kw, stalls, flags, name], ...]} with lat/lon in 1e-5
degrees, only stations with DC fast charging (40 kW or more, or a CCS/CHAdeMO/Supercharger socket
with no power tagged, taken as 50 kW or 150 kW for a Supercharger). flags: 1 Tesla Supercharger.
"""
import json
import re
import sys
import datetime

FAST_KW = 40
DC_SOCKETS = ("type2_combo", "ccs", "chademo", "tesla_supercharger", "tesla_supercharger_ccs", "nacs", "type1_combo")


def kw(value):
    """'150 kW', '150kW;50 kW', '22000 W', '0.15 MW' → the highest, in kW; None when unreadable."""
    best = None
    for part in re.split(r"[;,]", str(value)):
        m = re.search(r"([\d.]+)\s*(mw|kw|kva|w)?", part.strip().lower())
        if not m:
            continue
        try:
            n = float(m.group(1))
        except ValueError:
            continue
        unit = m.group(2) or ("w" if n > 1000 else "kw")
        n = n * 1000 if unit == "mw" else n / 1000 if unit == "w" else n
        if 0 < n <= 1000:
            best = n if best is None else max(best, n)
    return best


def is_supercharger(t):
    text = " ".join(str(t.get(k, "")) for k in ("brand", "operator", "name", "network")).lower()
    return ("tesla" in text and ("supercharger" in text or "supercargador" in text or "superchargeur" in text
                                 or t.get("brand", "").lower() == "tesla" or t.get("operator", "").lower().startswith("tesla"))) \
        or "ref:supercharge_info" in t or any(k.startswith("socket:tesla_supercharger") for k in t)


def power(t):
    best = None
    for k, v in t.items():
        if k in ("charging_station:output", "maxpower", "output", "charging_station:maxpower") or \
                (k.startswith("socket:") and (k.endswith(":output") or k.endswith(":maxpower"))):
            p = kw(v)
            if p is not None:
                best = p if best is None else max(best, p)
    return best


def has_dc_socket(t):
    for k, v in t.items():
        if k.startswith("socket:"):
            name = k.split(":")[1]
            if name in DC_SOCKETS and str(v).strip() not in ("0", "no"):
                return True
    return False


def stalls(t):
    try:
        return max(1, min(99, int(str(t.get("capacity", "")).split(";")[0])))
    except ValueError:
        pass
    total = 0
    for k, v in t.items():
        parts = k.split(":")
        if len(parts) == 2 and parts[0] == "socket" and parts[1] in DC_SOCKETS:
            try:
                total += int(v)
            except ValueError:
                pass
    return max(1, min(99, total))


def nice_name(t):
    """The station's name, or its operator when the name is only a reference code ("LLE-00054")."""
    name = (t.get("name") or "").strip()
    op = (t.get("operator") or t.get("brand") or t.get("network") or "").strip()
    if not name or (" " not in name and any(ch.isdigit() for ch in name)):
        name = op or name
    elif op and op.lower() not in name.lower() and len(name) < 18 and not is_supercharger(t):
        name = f"{op} · {name}"
    return name[:48]


def centre(geom):
    t = geom["type"]
    if t == "Point":
        return geom["coordinates"]
    pts = geom["coordinates"][0] if t == "Polygon" else geom["coordinates"][0][0] if t == "MultiPolygon" else geom["coordinates"]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return [sum(xs) / len(xs), sum(ys) / len(ys)]


def main():
    out, paths = sys.argv[1], sys.argv[2:]
    rows, seen = [], set()
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                feat = json.loads(line)
                t = feat.get("properties", {})
                if t.get("amenity") != "charging_station" or t.get("disused") == "yes" or t.get("access") in ("private", "no"):
                    continue
                sc = is_supercharger(t)
                p = power(t)
                if p is None and (sc or has_dc_socket(t)):
                    p = 150 if sc else 50
                if p is None or p < FAST_KW:
                    continue
                lon, lat = centre(feat["geometry"])
                key = (round(lat, 4), round(lon, 4))
                if key in seen:
                    continue
                seen.add(key)
                name = nice_name(t)
                rows.append([round(lat * 1e5), round(lon * 1e5), int(round(p)), stalls(t), 1 if sc else 0, name])
    rows.sort()
    body = {"built": datetime.date.today().isoformat(), "c": rows}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, separators=(",", ":"))
    print(f"chargers: {len(rows)} fast stations ({sum(r[4] for r in rows)} Superchargers)")


if __name__ == "__main__":
    main()
