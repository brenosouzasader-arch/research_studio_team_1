// Roofscapes RePowered — settings and assumptions.
// Every number marked ASSUMPTION should be replaced with a sourced value before the final review.

window.APP_CONFIG = {
  // ---- Barcelona City Council open services (geo.bcn.cat) ----
  // 3D model shown on the map. The first one that loads is used.
  SCENE_LAYERS: [
    "https://geo.bcn.cat/agisserver/rest/services/Hosted/BCN_3DtoFeat/SceneServer", // detailed LOD2 model
    "https://geo.bcn.cat/agisserver/rest/services/Hosted/3D_MTM/SceneServer/layers/0", // simple volumes
  ],
  // Same buildings as 2D footprints with height, roof form and use (used for the calculation).
  BUILDINGS_FEATURE_LAYER: "https://geo.bcn.cat/agisserver/rest/services/Hosted/BCN_3DtoFeat/FeatureServer/22",
  // City background map.
  BASEMAP_STYLE: "https://geo.bcn.cat/agisportal/sharing/rest/content/items/31ee4f5c2b52411b8d1a43b9c56ec808/resources/styles/root.json",

  // Area loaded around a clicked building (metres): neighbours that cast shade and can receive energy.
  LOAD_RADIUS_M: 350,

  // ---- Sun and climate (Barcelona) ----
  GHI_KWH_M2_YEAR: 1600,   // ASSUMPTION: yearly sun on a flat open surface. Check with PVGIS for your point.
  DIFFUSE_FRACTION: 0.38,  // ASSUMPTION: share of that sun that comes from the sky, not directly from the sun.

  // ---- Panels ----
  PANEL_LENGTH_M: 1.76,
  PANEL_WIDTH_M: 1.13,
  PANEL_GAP_M: 0.05,
  PANEL_EFFICIENCY: 0.21,          // typical 2026 monocrystalline panel
  PERFORMANCE_RATIO: 0.8,          // losses: heat, inverter, cables, dirt
  PANEL_HEIGHT_ABOVE_ROOF_M: 0.5,
  ROOF_SETBACK_M: 1.0,             // free strip along the roof edge and courtyards (parapet, safety, access)
  OBSTACLE_FACTOR: 0.75,           // ASSUMPTION: 25% of slots lost to stairs, tanks, AC units. The AI roof reading will replace this.

  // ---- Buildings and demand ----
  FLOOR_HEIGHT_M: 3.2,
  DEMAND_KWH_M2_YEAR: 30,          // ASSUMPTION: default electricity per m² of floor per year
  // ASSUMPTION: electricity per m² of floor per year by use (matched against the city's "uso_desc").
  DEMAND_BY_USE: [
    { match: "residencial", kwh: 30 },
    { match: "oficina", kwh: 90 },
    { match: "comercial", kwh: 110 },
    { match: "industrial", kwh: 60 },
    { match: "almac", kwh: 20 },
    { match: "aparcamiento", kwh: 10 },
    { match: "educa", kwh: 35 },
    { match: "cultural", kwh: 45 },
    { match: "sanid", kwh: 120 },
    { match: "deport", kwh: 50 },
    { match: "religio", kwh: 15 },
    { match: "ocio", kwh: 100 },
    { match: "hosteler", kwh: 100 },
  ],

  // ---- Sharing rule ----
  SHARE_RADIUS_M: 2000,            // Spanish collective self-consumption: up to 2 km from the panels
};
