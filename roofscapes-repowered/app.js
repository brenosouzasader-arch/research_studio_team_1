/* Roofscapes RePowered — the map and the screen.
 * 3D model and building data: Ajuntament de Barcelona open services (geo.bcn.cat), shown with the ArcGIS Maps SDK.
 * Sun and shade: our own calculation in solar.js.
 */
require([
  "esri/Map",
  "esri/Basemap",
  "esri/views/SceneView",
  "esri/layers/SceneLayer",
  "esri/layers/VectorTileLayer",
  "esri/layers/OpenStreetMapLayer",
  "esri/layers/GraphicsLayer",
  "esri/Graphic",
  "esri/widgets/Daylight",
  "esri/widgets/ShadowCast",
  "esri/widgets/Expand",
], function (Map, Basemap, SceneView, SceneLayer, VectorTileLayer, OpenStreetMapLayer, GraphicsLayer, Graphic, Daylight, ShadowCast, Expand) {
  "use strict";
  const CFG = window.APP_CONFIG;
  const S = window.Solar;
  const $ = (id) => document.getElementById(id);
  const fmt = (n, d = 0) => Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });

  const PLACES = {
    eixample: { lat: 41.3917, lng: 2.1649 },
    gracia: { lat: 41.4025, lng: 2.1567 },
    poblenou: { lat: 41.3995, lng: 2.1985 },
  };
  // Field names in the city's building layer (BCN_3DtoFeat).
  const F = {
    id: "objectid",
    zMin: "edificacio_ajbcn_z_min_vol",
    zMax: "edificacio_ajbcn_z_max_vol",
    height: "bldgheight",
    base: "baseelev",
    roof: "roofform",
    use: "uso_desc",
    refcat: "edificacio_ajbcn_addspatialj_15",
    deleg: "edificacio_ajbcn_addspatialjo_2",
    muni: "edificacio_ajbcn_addspatialjo_3",
    number: "edificacio_ajbcn_addspatialjo_9",
    barri: "edificacio_ajbcn_barri",
    district: "edificacio_ajbcn_districte",
  };
  const OUT_FIELDS = Object.values(F).join(",");
  const SR = { wkid: 4326 };

  const COLORS = {
    sel: [242, 165, 22],
    gotPart: [205, 232, 194],
    gotFull: [47, 125, 58],
    panelLow: [61, 79, 99],
    panelMid: [242, 165, 22],
    panelHigh: [255, 224, 102],
  };

  let view, sceneLayer;
  let area = null;          // { center, proj, buildings, index, sun }
  let sel = null;           // { k, slots, roofArea, summary }
  let panelGraphics = [];
  const roofLayer = new GraphicsLayer({ elevationInfo: { mode: "absolute-height" }, title: "Results" });
  const ringLayer = new GraphicsLayer({ elevationInfo: { mode: "on-the-ground" }, title: "2 km circle" });

  /* ---------- status ---------- */
  function setStatus(kind, text) {
    const el = $("status");
    el.dataset.kind = kind;
    el.textContent = text;
  }
  const nextFrame = () => new Promise((r) => setTimeout(r, 30));

  /* ---------- map ---------- */
  function makeBasemap() {
    const vt = new VectorTileLayer({ url: CFG.BASEMAP_STYLE });
    const bm = new Basemap({ baseLayers: [vt], title: "Barcelona" });
    vt.load().catch(() => {
      console.warn("City basemap did not load; using OpenStreetMap.");
      view.map.basemap = new Basemap({ baseLayers: [new OpenStreetMapLayer()], title: "OpenStreetMap" });
    });
    return bm;
  }

  async function addSceneLayer(map) {
    for (const url of CFG.SCENE_LAYERS) {
      const layer = new SceneLayer({ url, popupEnabled: false, title: "Barcelona buildings" });
      try {
        await layer.load();
        const hField = (layer.fields || []).find((f) => /^bldgheight$/i.test(f.name));
        layer.renderer = {
          type: "simple",
          symbol: {
            type: "mesh-3d",
            symbolLayers: [{
              type: "fill",
              material: { color: [214, 160, 145], colorMixMode: "replace" },
              edges: { type: "solid", color: [70, 25, 20, 0.35], size: 0.4 },
            }],
          },
          visualVariables: hField ? [{
            type: "color",
            field: hField.name,
            stops: [
              { value: 4, color: [241, 221, 213] },
              { value: 18, color: [214, 140, 120] },
              { value: 35, color: [160, 62, 48] },
              { value: 60, color: [112, 30, 24] },
            ],
          }] : [],
        };
        map.add(layer, 0);
        return layer;
      } catch (err) {
        console.warn("3D layer did not load:", url, err);
      }
    }
    return null;
  }

  function goToPlace(p, zoom = 17) {
    return view.goTo({ center: [p.lng, p.lat], zoom, tilt: 60, heading: 30 }, { duration: 1500 }).catch(() => {});
  }

  /* ---------- city building data (FeatureServer) ---------- */
  async function query(params) {
    const body = new URLSearchParams({
      f: "json", where: "1=1", outFields: OUT_FIELDS, returnGeometry: "true",
      multipatchOption: "xyFootprint", inSR: "4326", outSR: "4326", ...params,
    });
    let res;
    try {
      res = await fetch(CFG.BUILDINGS_FEATURE_LAYER + "/query", { method: "POST", body });
    } catch (err) {
      throw new Error("Could not reach the city's building data (geo.bcn.cat). Check the internet connection; if it keeps failing, the server may block requests from this address.");
    }
    if (!res.ok) throw new Error(`City building data answered ${res.status}.`);
    const json = await res.json();
    if (json.error) throw new Error("City building data: " + (json.error.message || "error") + ".");
    return json;
  }

  function queryAtPoint(lng, lat) {
    return query({
      geometry: JSON.stringify({ x: lng, y: lat, spatialReference: SR }),
      geometryType: "esriGeometryPoint",
      spatialRel: "esriSpatialRelIntersects",
    });
  }

  async function queryArea(lng, lat, radius) {
    const dLat = radius / 111320, dLng = radius / (111320 * Math.cos((lat * Math.PI) / 180));
    const env = { xmin: lng - dLng, ymin: lat - dLat, xmax: lng + dLng, ymax: lat + dLat, spatialReference: SR };
    const base = { geometry: JSON.stringify(env), geometryType: "esriGeometryEnvelope", spatialRel: "esriSpatialRelIntersects" };
    const features = [];
    let offset = 0;
    for (let page = 0; page < 20; page++) {
      let j;
      try {
        j = await query({ ...base, resultOffset: String(offset), resultRecordCount: "2000", orderByFields: F.id });
      } catch (err) {
        if (page === 0 && /pagina/i.test(err.message)) j = await query(base); // server without paging
        else throw err;
      }
      const got = j.features || [];
      features.push(...got);
      setStatus("loading", `Loading neighbouring buildings… ${fmt(features.length)}`);
      if (!j.exceededTransferLimit || !got.length) break;
      offset += got.length;
    }
    return features;
  }

  function intensityFor(use) {
    const u = String(use || "").toLowerCase();
    const hit = CFG.DEMAND_BY_USE.find((d) => u.includes(d.match));
    return hit ? hit.kwh : CFG.DEMAND_KWH_M2_YEAR;
  }

  // City features -> buildings for the solar engine (outer ring + courtyards; multi-part = several buildings).
  function toBuildings(features, proj) {
    const out = [];
    for (const f of features) {
      const a = f.attributes || {}, g = f.geometry;
      if (!g || !g.rings) continue;
      const rings = g.rings.map((r) => S.cleanRing(r)).filter(Boolean)
        .map((ll) => ({ ll, xy: ll.map(([lng, lat]) => proj.toXY(lng, lat)) }))
        .sort((p, q) => S.polygonArea(q.xy) - S.polygonArea(p.xy));
      const outers = [];
      for (const r of rings) {
        const host = outers.find((o) => S.pointInPolygon(r.xy[0][0], r.xy[0][1], o.xy));
        if (host) host.holes.push(r);
        else outers.push({ ...r, holes: [] });
      }
      const top = a[F.zMax] != null ? a[F.zMax] : (a[F.base] || 0) + (a[F.height] || 0);
      const rel = a[F.height] != null ? a[F.height] : top - (a[F.zMin] || 0);
      if (!(rel > 0.5)) continue;
      for (const o of outers) {
        const b = {
          id: a[F.id], attrs: a, ll: o.ll, xy: o.xy,
          holes: o.holes.map((h) => h.xy), holesLL: o.holes.map((h) => h.ll),
          height: top, relHeight: rel,
        };
        b.bbox = S.bbox(b.xy);
        b.c = S.centroid(b.xy);
        b.footprint = S.footprintArea(b);
        if (b.footprint < 4) continue;
        b.floors = Math.max(1, Math.round(rel / CFG.FLOOR_HEIGHT_M));
        b.intensity = intensityFor(a[F.use]);
        b.demand = S.buildingDemand(b, CFG);
        out.push(b);
      }
    }
    return out;
  }

  async function loadArea(lng, lat) {
    const features = await queryArea(lng, lat, CFG.LOAD_RADIUS_M);
    const proj = S.makeProjection(lat, lng);
    const buildings = toBuildings(features, proj);
    area = { center: { lng, lat }, proj, buildings, index: S.buildIndex(buildings), sun: S.sunSamples(lat, lng) };
  }

  /* ---------- click -> building ---------- */
  async function onClick(event) {
    let pt = event.mapPoint;
    try {
      const hit = await view.hitTest(event, sceneLayer ? { include: [sceneLayer] } : undefined);
      const r = hit.results.find((x) => x.mapPoint);
      if (r) pt = r.mapPoint;
    } catch (_) { /* use the ground point */ }
    if (!pt) return;
    try {
      await selectAt(pt.longitude, pt.latitude);
    } catch (err) {
      setStatus("error", err.message);
      console.error(err);
    }
  }

  async function selectAt(lng, lat) {
    setStatus("loading", "Finding the building…");
    const j = await queryAtPoint(lng, lat);
    const feats = (j.features || []).filter((f) => f.geometry);
    if (!feats.length) { setStatus("idle", "No building at that point. Click on a roof."); return; }
    const top = (f) => f.attributes[F.zMax] ?? 0;
    const f = feats.reduce((a, b) => (top(b) > top(a) ? b : a));

    const far = !area || Math.hypot(...area.proj.toXY(lng, lat)) > CFG.LOAD_RADIUS_M - 120;
    if (far) {
      setStatus("loading", "Loading neighbouring buildings…");
      await loadArea(lng, lat);
    }
    const [px, py] = area.proj.toXY(lng, lat);
    let k = area.buildings.findIndex((b) => b.id === f.attributes[F.id] && S.insideFootprint(px, py, b));
    if (k < 0) k = area.buildings.findIndex((b) => b.id === f.attributes[F.id]);
    if (k < 0) {
      const extra = toBuildings([f], area.proj);
      if (!extra.length) { setStatus("error", "This building has no usable outline in the city data."); return; }
      area.buildings.push(...extra);
      area.index = S.buildIndex(area.buildings);
      k = area.buildings.length - extra.length;
    }
    await analyze(k);
  }

  /* ---------- analysis + drawing ---------- */
  async function analyze(k) {
    const b = area.buildings[k];
    setStatus("loading", "Calculating sun and shade on the roof…");
    await nextFrame();
    const t0 = performance.now();
    const res = S.analyzeRoof(area.buildings, area.index, k, area.sun, CFG);
    sel = { k, ...res };
    drawSelection();
    updateResults();
    $("report").hidden = false;
    const flat = !b.attrs[F.roof] || /flat/i.test(b.attrs[F.roof]);
    setStatus("idle", (res.slots.length
      ? `Calculated in ${fmt((performance.now() - t0) / 1000, 1)} s with ${fmt(area.buildings.length)} neighbouring buildings.`
      : "No panel fits on this roof after the edge setback.") +
      (flat ? "" : ` Roof form "${b.attrs[F.roof]}": panels are placed as if the roof were flat, so the result is approximate.`));
  }

  function ringToZ(ll, z) {
    const r = ll.map(([lng, lat]) => [lng, lat, z]);
    r.push(r[0]);
    return r;
  }

  function slab(b, z, size, color) {
    return new Graphic({
      geometry: { type: "polygon", hasZ: true, spatialReference: SR, rings: [ringToZ(b.ll, z), ...(b.holesLL || []).map((h) => ringToZ(h, z))] },
      symbol: {
        type: "polygon-3d",
        symbolLayers: [{ type: "extrude", size, material: { color }, edges: { type: "solid", color: [0, 0, 0, 0.25], size: 0.5 } }],
      },
    });
  }

  function mix(a, b, t) { return a.map((v, i) => Math.round(v + (b[i] - v) * t)); }
  function panelColor(f) {
    const t = Math.min(1, Math.max(0, (f - 0.5) / 0.5));
    return t < 0.6 ? mix(COLORS.panelLow, COLORS.panelMid, t / 0.6) : mix(COLORS.panelMid, COLORS.panelHigh, (t - 0.6) / 0.4);
  }

  function drawSelection() {
    roofLayer.removeAll();
    ringLayer.removeAll();
    const b = area.buildings[sel.k];
    roofLayer.add(slab(b, b.height + 0.02, 0.12, [...COLORS.sel, 0.9]));
    panelGraphics = sel.slots.map((s) => new Graphic({
      geometry: {
        type: "polygon", hasZ: true, spatialReference: SR,
        rings: [ringToZ(s.corners.map(([x, y]) => area.proj.toLngLat(x, y)), b.height + 0.3)],
      },
      symbol: { type: "polygon-3d", symbolLayers: [{ type: "extrude", size: 0.12, material: { color: panelColor(s.sunFactor) } }] },
    }));
    roofLayer.addMany(panelGraphics);

    const path = [];
    for (let a = 0; a <= 360; a += 3) {
      const t = (a * Math.PI) / 180, r = CFG.SHARE_RADIUS_M;
      path.push(area.proj.toLngLat(b.c[0] + r * Math.sin(t), b.c[1] + r * Math.cos(t)));
    }
    ringLayer.add(new Graphic({
      geometry: { type: "polyline", paths: [path], spatialReference: SR },
      symbol: { type: "line-3d", symbolLayers: [{ type: "line", size: 3, material: { color: COLORS.sel }, pattern: { type: "style", style: "dash" } }] },
    }));

    const [lng, lat] = area.proj.toLngLat(b.c[0], b.c[1]);
    view.goTo({ center: [lng, lat], zoom: 19, tilt: 55 }, { duration: 1200 }).catch(() => {});
  }

  let neighbourGraphics = [];
  function drawNeighbours(alloc) {
    roofLayer.removeMany(neighbourGraphics);
    neighbourGraphics = [];
    alloc.forEach((v, i) => {
      if (i === sel.k || v.received <= 0) return;
      const b = area.buildings[i];
      neighbourGraphics.push(slab(b, b.height + 0.05, 0.6, [...mix(COLORS.gotPart, COLORS.gotFull, Math.min(1, v.share)), 0.95]));
    });
    roofLayer.addMany(neighbourGraphics);
  }

  function updateResults() {
    if (!sel) return;
    const min = Number($("sun-range").value) / 100;
    $("sun-out").textContent = `${Math.round(min * 100)}%`;
    const b = area.buildings[sel.k], a = b.attrs;
    const kept = sel.slots.filter((s) => s.sunFactor >= min);
    sel.slots.forEach((s, j) => { if (panelGraphics[j]) panelGraphics[j].visible = s.sunFactor >= min; });

    const panelArea = CFG.PANEL_LENGTH_M * CFG.PANEL_WIDTH_M;
    const energy = kept.reduce((sum, s) => sum + S.panelYearKwh(s, CFG), 0) * CFG.OBSTACLE_FACTOR;
    const panels = Math.round(kept.length * CFG.OBSTACLE_FACTOR);
    const kwp = panels * panelArea * CFG.PANEL_EFFICIENCY;
    const avgSun = kept.length ? kept.reduce((s, x) => s + x.sunFactor, 0) / kept.length : 0;

    $("r-use").textContent = `${a[F.use] || "Use not given"} · roof: ${a[F.roof] || "unknown"}`;
    $("r-building").textContent = `${fmt(b.relHeight, 1)} m high · about ${b.floors} floors · roof at ${fmt(b.height, 1)} m above sea level · city ID ${b.id}`;
    const ref = a[F.refcat];
    if (ref && ref.trim().length >= 14) {
      const url = `https://www1.sedecatastro.gob.es/CYCBienInmueble/OVCListaBienes.aspx?del=${a[F.deleg] || 8}&muni=${a[F.muni] || 900}&rc1=${ref.slice(0, 7)}&rc2=${ref.slice(7, 14)}`;
      $("r-catastro").innerHTML = `Cadastral ref. ${ref} · <a href="${url}" target="_blank" rel="noopener">open in Catastro</a>`;
    } else {
      $("r-catastro").textContent = "";
    }
    $("r-energy").textContent = fmt(energy);
    $("r-roof").textContent = `${fmt(sel.roofArea)} m²`;
    $("r-slots").textContent = fmt(sel.slots.length);
    $("r-panels").textContent = fmt(panels);
    $("r-parea").textContent = `${fmt(panels * panelArea)} m²`;
    $("r-kwp").textContent = `${fmt(kwp, 1)} kWp`;
    $("r-shade").textContent = kept.length ? `${fmt((1 - avgSun) * 100)}%` : "–";
    $("r-own").textContent = `Covers ${fmt(Math.min(999, (100 * energy) / b.demand))}% of this building's own use (≈ ${fmt(b.demand)} kWh/yr at ${b.intensity} kWh/m², assumed).`;

    shareEnergy(energy);
    fillAssumptions();
    sel.summary = { energy_kwh_year: energy, panels, kwp, avg_sun_factor: avgSun, min_sun_kept: min };
  }

  function shareEnergy(energy) {
    const policy = $("policy-select").value;
    const alloc = S.allocate(area.buildings, sel.k, energy, CFG, policy);
    drawNeighbours(alloc);
    const own = alloc.get(sel.k);
    const others = [...alloc.entries()].filter(([i, v]) => i !== sel.k && v.received > 0);
    const full = others.filter(([, v]) => v.share >= 0.999).length;
    const shared = others.reduce((s, [, v]) => s + v.received, 0);
    let text;
    if (policy === "equal") {
      text = `Every building in the loaded area (${fmt(alloc.size)}) gets ${fmt((own ? own.share : 0) * 100, 2)}% of its yearly use.`;
    } else if (!others.length) {
      text = "All the energy stays in this building: it uses more than the roof makes. Nothing is left for neighbours.";
    } else {
      text = `${fmt(shared)} kWh/yr go to ${fmt(others.length)} neighbours: ${fmt(full)} fully supplied${others.length > full ? ", 1 partly" : ""}.`;
    }
    $("r-share").textContent = text;
  }

  function fillAssumptions() {
    const items = [
      `Buildings, heights, roof form and use: Ajuntament de Barcelona 3D model (BCN_3DtoFeat).`,
      `Sun: ${fmt(CFG.GHI_KWH_M2_YEAR)} kWh/m² per year on open flat ground, ${Math.round(CFG.DIFFUSE_FRACTION * 100)}% of it diffuse sky light.`,
      `Panels: ${CFG.PANEL_LENGTH_M} × ${CFG.PANEL_WIDTH_M} m, ${Math.round(CFG.PANEL_EFFICIENCY * 100)}% efficient, ${Math.round(CFG.PERFORMANCE_RATIO * 100)}% system performance, laid flat.`,
      `${CFG.ROOF_SETBACK_M} m free strip along roof edges and courtyards; ${Math.round((1 - CFG.OBSTACLE_FACTOR) * 100)}% of slots lost to stairs, tanks and AC units (until the AI reads the roof).`,
      `Demand: floor area × kWh per m² by use (residential ${intensityFor("residencial")}, offices ${intensityFor("oficina")}, retail ${intensityFor("comercial")}…).`,
      `Shade: neighbours as flat-topped blocks at their roof height; trees are not included.`,
      `Sharing: yearly totals, buildings within ${fmt(CFG.LOAD_RADIUS_M)} m loaded (the circle shows the full ${fmt(CFG.SHARE_RADIUS_M / 1000)} km). Hour-by-hour matching comes next.`,
    ];
    $("r-assumptions").innerHTML = items.map((t) => `<li>${t}</li>`).join("");
  }

  function saveJson() {
    if (!sel) return;
    const b = area.buildings[sel.k];
    const [lng, lat] = area.proj.toLngLat(b.c[0], b.c[1]);
    const data = {
      source: "Ajuntament de Barcelona, BCN_3DtoFeat",
      building: { city_id: b.id, lat, lng, height_m: b.relHeight, roof_m_asl: b.height, floors: b.floors, footprint_m2: b.footprint, demand_kwh_year: b.demand, attributes: b.attrs },
      result: { roof_area_m2: sel.roofArea, panel_slots: sel.slots.length, ...sel.summary },
      panels: sel.slots.map((s) => {
        const [plng, plat] = area.proj.toLngLat(s.cx, s.cy);
        return { lat: plat, lng: plng, sun_factor: +s.sunFactor.toFixed(3), direct: +s.direct.toFixed(3), sky: +s.sky.toFixed(3), kwh_year: +S.panelYearKwh(s, CFG).toFixed(1) };
      }),
      assumptions: CFG,
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `roof_${b.id}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  /* ---------- start ---------- */
  async function main() {
    const map = new Map({ basemap: makeBasemap(), ground: "world-elevation" });
    view = new SceneView({
      container: "map",
      map,
      qualityProfile: "high",
      popupEnabled: false,
      environment: {
        lighting: { type: "sun", date: new Date(Date.UTC(2026, 5, 21, 8, 0)), directShadowsEnabled: true },
        atmosphereEnabled: true,
      },
      camera: { position: { longitude: 2.1598, latitude: 41.3858, z: 650 }, heading: 30, tilt: 60 },
    });
    map.addMany([roofLayer, ringLayer]);

    view.ui.add(new Expand({ view, content: new Daylight({ view, dateOrSeason: "date" }), expandTooltip: "Sun and shadows", expanded: window.innerWidth > 900 }), "bottom-left");
    view.ui.add(new Expand({ view, content: new ShadowCast({ view }), expandTooltip: "Hours of shadow" }), "bottom-left");

    $("sun-range").addEventListener("input", updateResults);
    $("policy-select").addEventListener("change", updateResults);
    $("save-btn").addEventListener("click", saveJson);
    document.querySelectorAll("[data-place]").forEach((btn) =>
      btn.addEventListener("click", () => goToPlace(PLACES[btn.dataset.place])));

    try {
      await view.when();
    } catch (err) {
      setStatus("error", "The 3D map could not start. This browser may not support WebGL2.");
      console.error(err);
      return;
    }
    sceneLayer = await addSceneLayer(map);
    if (!sceneLayer) setStatus("error", "The city's 3D model did not load. The calculation still works: click where a building is.");
    else setStatus("idle", "Click any roof to calculate its solar potential.");
    view.on("click", onClick);
  }

  main();
});
