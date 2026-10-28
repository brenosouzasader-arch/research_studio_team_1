# Step 1: Find the building

The first step of the rooftop solar pipeline. The input is an address, or a point tapped on a map. The output is one record per building:

| Field | From |
|---|---|
| `lat`, `lon` | ICGC geocoder, falling back to Nominatim (OpenStreetMap) |
| `refcat`: 14-character plot reference | Catastro, "Consulta_RCCOOR" service |
| `year_built`, `current_use`, `dwellings`, footprint | Catastro INSPIRE Buildings |
| `floors_above_ground`: highest building part | Catastro INSPIRE Building Parts |
| `lidar_tiles`, `roof`, `flat_roof` | Your downloaded ICGC or CNIG LiDAR tiles |
| `heritage_protected` | A protected-buildings GeoJSON, if you have one (otherwise `null` = unknown) |

All geometry is in **EPSG:25831** (ETRS89 / UTM 31N, metres), the same system as the ICGC data. Each run writes `building.json` and `building.geojson`. Drag the `.geojson` file into QGIS to check the footprint against the orthophoto.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

Run these from this folder:

```bash
# 1. Check the live services work (no LiDAR needed)
python smoke_test.py

# 2. One building from an address
python -m step1.find_building "Carrer de Girona 81, Barcelona"

# 3. With LiDAR, which adds the flat-roof check
python -m step1.find_building "Carrer de Girona 81, Barcelona" --laz-dir data/lidar

# 4. From a map tap instead of an address
python -m step1.find_building --lat 41.3935 --lon 2.1689 --laz-dir data/lidar

# Offline tests (no internet needed)
python -m pytest tests
```

### Getting LiDAR tiles

Download the tiles that cover your test buildings from the **ICGC Territorial LiDAR** download viewer (3rd coverage, 2021–2023, LAZ), or from the CNIG PNOA LiDAR viewer. Put them all in one folder, such as `data/lidar/`. The code reads each file's bounds, so the file names don't matter.

## How the flat-roof check works

1. Take the LiDAR points classed as **building** (class 6) inside the Catastro footprint.
2. Make a 0.5 m height grid from them, keeping the highest point per cell.
3. Measure the slope of each cell. Cells under 5° count as flat.
4. The roof counts as flat if at least **70%** of the cells are flat. Stair housings and tanks make small steep patches, so a flat roof with obstacles still passes.

The thresholds are at the top of `step1/lidar.py`, ready for tuning once we've seen real Barcelona roofs.

## Known gaps: check these first

- **Not yet run against the live services.** Catastro, ICGC and Nominatim were blocked from the workspace where this was written. The parsers follow each service's documented format, and the offline tests use hand-written sample replies in that format, but real replies may differ slightly. `smoke_test.py` is the first thing to run. If it fails, the error says which service and step failed.
- **Catastro's WFS parameter really is spelled `STOREDQUERIE_ID`**, in Catastro's own spelling. If the WFS returns an error, check the stored-query names (`GetBuildingByParcel`, `GetBuildingPartByParcel`) in the service's GetCapabilities.
- **Heritage:** we haven't found Barcelona's heritage catalogue as open data yet, so the flag stays `null` until a layer is passed with `--heritage`.
- **Plots with several buildings:** the code picks the building that contains the point, or else the largest one, and adds a warning.
- **Floors** is the highest value among the building's parts. For Eixample blocks with a set-back top floor, that includes the extension.

## Files

```
step1/geocode.py        address -> lat/lon
step1/catastro.py       lat/lon -> plot reference -> buildings and parts
step1/lidar.py          LiDAR tiles -> roof points -> flat-roof check
step1/heritage.py       optional heritage layer -> flag
step1/find_building.py  runs the steps, command line, JSON/GeoJSON output
tests/test_step1.py     offline tests
smoke_test.py           live test on 4 Eixample buildings
```
