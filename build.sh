#!/bin/sh
# Downloads the extracts in countries.txt, keeps the roads with a speed limit and builds ./site.
set -e
mkdir -p work
for c in $(grep -v '^#' countries.txt); do
  name=$(basename "$c")
  [ -f "work/$name.osm.pbf" ] || curl -sfL -o "work/$name.osm.pbf" "https://download.geofabrik.de/$c-latest.osm.pbf"
  osmium tags-filter -O -o "work/$name-hw.osm.pbf" "work/$name.osm.pbf" w/highway
  osmium tags-filter -O -o "work/$name-limits.osm.pbf" "work/$name-hw.osm.pbf" \
    w/maxspeed w/maxspeed:type w/source:maxspeed w/zone:maxspeed w/highway=residential,living_street
  osmium export -O -f geojsonseq --geometry-types=linestring \
    -o "work/$name.geojsonseq" "work/$name-limits.osm.pbf"
done
python3 build.py site work/*.geojsonseq
