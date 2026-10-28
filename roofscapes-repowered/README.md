# Roofscapes RePowered — 3D roof solar prototype on the Barcelona city model

The site shows the **Ajuntament de Barcelona 3D model**. Click any roof and the page calculates, with our own model:

- how many solar panels fit on the roof (1 m free strip along edges and courtyards)
- how much sun each panel gets over a year, **including shade from the real neighbouring buildings**
- yearly energy, installed power, and how much of the building's own use it covers (demand by building use)
- who gets the energy inside the 2 km sharing circle: supplied roofs turn green
- city sun and shadow tools: any day and hour (Daylight) and hours of shadow (Shadow cast)

No account, no API key, no credit card.

## Data and tools

| Part | Source | Notes |
|---|---|---|
| 3D model on the map | Ajuntament de Barcelona, `Hosted/BCN_3DtoFeat/SceneServer` (falls back to `Hosted/3D_MTM`) | public city service, geo.bcn.cat |
| Building footprints, height, roof form, use, cadastral ref. | Ajuntament de Barcelona, `Hosted/BCN_3DtoFeat/FeatureServer/22`, queried with `multipatchOption=xyFootprint` | ~used fields: `bldgheight`, `edificacio_ajbcn_z_max_vol`, `roofform`, `uso_desc`, REFCAT |
| Background map | Ajuntament de Barcelona vector map (falls back to OpenStreetMap) | |
| 3D engine | ArcGIS Maps SDK for JavaScript 4.31 | free to use with these public layers |
| Sun position, shade, sky view, energy, sharing | our code, `solar.js` | |

## Files

| File | What it does |
|---|---|
| `index.html` | Page structure |
| `style.css` | Look of the page |
| `config.js` | Service addresses and **all assumptions** (sun, panels, demand by use, sharing radius) |
| `solar.js` | Solar engine: sun path, panel slots, shade rays, sky view, energy, demand, sharing |
| `app.js` | 3D map, click on a building, loading neighbours from the city, drawing, Roof Report |

## Run it

Open with a local web server (not by double-clicking):

- **VS Code:** right-click `index.html` → **Open with Live Server**, or
- **Terminal in this folder:** `python -m http.server 8000` → open http://localhost:8000

## Use it

1. The map opens over the Eixample. Rotate with right-click drag, zoom with the wheel.
2. Click a roof. The first click loads the neighbouring buildings from the city (a few seconds).
3. The Roof Report fills in; panels appear on the roof (dark = shade, yellow = full sun); the roof is outlined in orange; the 2 km circle appears.
4. **Keep panels with at least X% of full sun** removes shaded panels.
5. **Who gets the energy** colours the supplied roofs green.
6. Bottom-left: **Sun and shadows** (choose day and hour) and **Hours of shadow** (the city's shadow map for a day).
7. **Save roof data (JSON)** saves every panel's result for the dataset and for checks against PVGIS, Ladybug or Google Solar.
8. **open in Catastro** opens the building's cadastral record.

## How the calculation works

1. **Footprint** from the city layer (with courtyards). Height = `bldgheight`; roof level = `z_max_vol` (metres above sea level), so hills are included in the shade.
2. **Panel slots**: the roof is turned to its longest edge and filled with 1.76 × 1.13 m panels, keeping 1 m from every edge and courtyard.
3. **Sun path**: 140 sun positions (every hour of the 15th of each month), weighted by direct sun strength.
4. **Shade**: from each panel a ray goes towards the sun every 1 m; if it passes inside a higher neighbour, the panel is in shade at that hour.
5. **Sky view**: horizon measured in 16 directions → share of diffuse sky light.
6. **Energy** = sun on panel × area × efficiency × performance ratio × obstacle factor.
7. **Demand** = footprint × floors × kWh/m² for the building's use (`uso_desc`).
8. **Sharing**: own building first, then nearest buildings get their full yearly use until energy runs out (or: everyone gets the same %).

## Known limits (next steps)

- Roof obstacles are a fixed 25% assumption → **AI roof reading** on aerial photos will replace it.
- Pitched roofs (`roofform` not Flat) are treated as flat; the page says so.
- Trees are not in the shade model.
- Yearly totals → next: hour-by-hour matching (no sun at night).
- Sharing uses buildings within 350 m of the click, not the full 2 km.
- Values marked ASSUMPTION in `config.js` need sources (PVGIS, Barcelona Energy Observatory).
