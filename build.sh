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
  # Fixed speed cameras (nodes), for the app's camera warnings.
  osmium tags-filter -O -o "work/$name-cameras.osm.pbf" "work/$name.osm.pbf" n/highway=speed_camera
  osmium export -O -f geojsonseq --geometry-types=point \
    -o "work/$name-cameras.geojsonseq" "work/$name-cameras.osm.pbf"
done
# Official fixed cameras and average-speed sections from the DGT (open data, CC BY, Spain except
# the Basque Country and Catalonia). Optional: the build goes on without it.
curl -sfL -m 120 -o work/dgt-radares.xml https://nap.dgt.es/datex2/dgt/PredefinedLocationsPublication/radares/content.xml || rm -f work/dgt-radares.xml
python3 build.py site work/*.geojsonseq $(ls work/dgt-radares.xml 2>/dev/null)
