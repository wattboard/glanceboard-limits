# Glanceboard speed limits

Road speed-limit tiles for the Glanceboard app, built from OpenStreetMap once a month.

- `build.sh` downloads the extracts in `countries.txt` from Geofabrik, keeps the roads that
  carry a speed limit (osmium) and runs `build.py`, which writes `site/`.
- Urban streets with no limit tagged (most residential streets) get the legal default:
  Spain 30 (50 with two or more lanes each way, 20 on living streets), Portugal 50 (20 on living streets).
- `13/<x>/<y>.json` (zoom 13, about 4 km): `{"w": [[km/h, lat, lon, dlat, dlon, ...]]}`,
  coordinates in 1e-5 degrees, each point after the first relative to the previous one.
- `coverage.json`: zoom-11 areas that were built. `meta.json`: build date.
- The GitHub Action rebuilds and publishes to GitHub Pages on the 2nd of each month.

## Licence

The data is © OpenStreetMap contributors and available under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
The tiles are a derived database under the same licence. The scripts are MIT.
