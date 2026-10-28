/* Roofscapes RePowered — solar engine (no map code here, so it can be tested on its own).
 *
 *  - sun position for Barcelona through the year
 *  - panel slots on a flat roof (with an edge setback)
 *  - shade from neighbouring buildings: a ray from each panel towards the sun
 *  - sky view (how much open sky a panel sees) for diffuse light
 *  - sharing the energy with buildings inside the 2 km circle
 */
(function (root) {
  "use strict";
  const R = 6378137;
  const DEG = Math.PI / 180;

  /* ---------- geometry ---------- */
  function makeProjection(lat0, lng0) {
    const kx = Math.cos(lat0 * DEG) * R * DEG;
    const ky = R * DEG;
    return {
      toXY: (lng, lat) => [(lng - lng0) * kx, (lat - lat0) * ky],
      toLngLat: (x, y) => [lng0 + x / kx, lat0 + y / ky],
    };
  }

  function polygonArea(p) {
    let a = 0;
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) a += (p[j][0] + p[i][0]) * (p[j][1] - p[i][1]);
    return Math.abs(a / 2);
  }

  function centroid(p) {
    let a = 0, cx = 0, cy = 0;
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
      const f = p[j][0] * p[i][1] - p[i][0] * p[j][1];
      a += f; cx += (p[j][0] + p[i][0]) * f; cy += (p[j][1] + p[i][1]) * f;
    }
    if (Math.abs(a) < 1e-9) return [p[0][0], p[0][1]];
    return [cx / (3 * a), cy / (3 * a)];
  }

  function pointInPolygon(x, y, p) {
    let inside = false;
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
      const xi = p[i][0], yi = p[i][1], xj = p[j][0], yj = p[j][1];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function distToSegment(x, y, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const L = dx * dx + dy * dy;
    let t = L ? ((x - a[0]) * dx + (y - a[1]) * dy) / L : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy));
  }

  function minEdgeDistance(x, y, p) {
    let m = Infinity;
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) m = Math.min(m, distToSegment(x, y, p[j], p[i]));
    return m;
  }

  function bbox(p) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y] of p) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    return [x0, y0, x1, y1];
  }

  /* ---------- clean OSM rings so the 3D engine never gets a broken polygon ---------- */
  function cleanRing(ring) {
    const out = [];
    for (const pt of ring) {
      const last = out[out.length - 1];
      if (!last || Math.abs(last[0] - pt[0]) > 1e-9 || Math.abs(last[1] - pt[1]) > 1e-9) out.push(pt);
    }
    if (out.length > 1) {
      const a = out[0], z = out[out.length - 1];
      if (Math.abs(a[0] - z[0]) < 1e-9 && Math.abs(a[1] - z[1]) < 1e-9) out.pop();
    }
    return out.length >= 3 ? out : null;
  }

  /* ---------- spatial index: which buildings touch each 8 m cell ---------- */
  function buildIndex(buildings, cell = 8) {
    const map = new Map();
    let maxH = 0;
    buildings.forEach((b, k) => {
      maxH = Math.max(maxH, b.height);
      const [x0, y0, x1, y1] = b.bbox;
      for (let i = Math.floor(x0 / cell); i <= Math.floor(x1 / cell); i++)
        for (let j = Math.floor(y0 / cell); j <= Math.floor(y1 / cell); j++) {
          const key = i + "," + j;
          let list = map.get(key);
          if (!list) map.set(key, (list = []));
          list.push(k);
        }
    });
    return { cell, map, maxH };
  }

  // Inside the footprint and not inside a courtyard (hole).
  function insideFootprint(x, y, b) {
    if (!pointInPolygon(x, y, b.xy)) return false;
    if (b.holes) for (const h of b.holes) if (pointInPolygon(x, y, h)) return false;
    return true;
  }

  function footprintArea(b) {
    let a = polygonArea(b.xy);
    if (b.holes) for (const h of b.holes) a -= polygonArea(h);
    return Math.max(0, a);
  }

  function heightAbove(index, buildings, x, y, z) {
    // tallest building at (x, y) that is higher than z; 0 if none
    const list = index.map.get(Math.floor(x / index.cell) + "," + Math.floor(y / index.cell));
    if (!list) return 0;
    let h = 0;
    for (const k of list) {
      const b = buildings[k];
      if (b.height <= z || b.height <= h) continue;
      const [x0, y0, x1, y1] = b.bbox;
      if (x < x0 || x > x1 || y < y0 || y > y1) continue;
      if (insideFootprint(x, y, b)) h = b.height;
    }
    return h;
  }

  /* ---------- sun ---------- */
  // Sun altitude and azimuth (azimuth from north, clockwise), accurate to ~0.5° — enough for shade.
  function sunPosition(date, lat, lng) {
    const n = date.getTime() / 86400000 + 2440587.5 - 2451545.0;
    const L = (280.46 + 0.9856474 * n) % 360;
    const g = ((357.528 + 0.9856003 * n) % 360) * DEG;
    const lambda = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * DEG;
    const eps = (23.439 - 0.0000004 * n) * DEG;
    const ra = Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda));
    const dec = Math.asin(Math.sin(eps) * Math.sin(lambda));
    const gmst = (((18.697374558 + 24.06570982441908 * n) % 24) + 24) % 24;
    const H = (gmst * 15 + lng) * DEG - ra;
    const phi = lat * DEG;
    const alt = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H));
    let az = Math.atan2(-Math.sin(H), Math.tan(dec) * Math.cos(phi) - Math.sin(phi) * Math.cos(H));
    if (az < 0) az += 2 * Math.PI;
    return { alt, az };
  }

  // Clear-sky direct sun on a flat surface (W/m²), used only as a weight between hours.
  function clearSkyBeamHorizontal(alt) {
    const altDeg = alt / DEG;
    if (altDeg <= 2) return 0;
    const am = 1 / (Math.sin(alt) + 0.50572 * Math.pow(altDeg + 6.07995, -1.6364));
    const dni = 1361 * Math.pow(0.7, Math.pow(am, 0.678));
    return dni * Math.sin(alt);
  }

  // One sun position per hour on the 15th of every month, weighted by how much direct sun it brings.
  function sunSamples(lat, lng, year = 2026) {
    const days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    const out = [];
    let total = 0;
    for (let m = 0; m < 12; m++) {
      for (let h = 0; h < 24; h++) {
        const s = sunPosition(new Date(Date.UTC(year, m, 15, h, 30)), lat, lng);
        const w = clearSkyBeamHorizontal(s.alt) * days[m];
        if (w <= 0) continue;
        out.push({ dx: Math.sin(s.az), dy: Math.cos(s.az), tanAlt: Math.tan(s.alt), w });
        total += w;
      }
    }
    out.forEach((s) => (s.w /= total));
    return out;
  }

  /* ---------- shade tests ---------- */
  function isShaded(index, buildings, x, y, z, s, step = 1, maxDist = 300) {
    const reach = Math.min(maxDist, (index.maxH - z) / s.tanAlt);
    for (let d = step; d <= reach; d += step) {
      const rz = z + d * s.tanAlt;
      if (heightAbove(index, buildings, x + s.dx * d, y + s.dy * d, rz) > 0) return true;
    }
    return false;
  }

  // Share of the open sky dome a point sees (1 = fully open). 16 directions.
  function skyView(index, buildings, x, y, z, step = 1.5, maxDist = 150) {
    let sum = 0;
    const N = 16;
    for (let k = 0; k < N; k++) {
      const a = (k / N) * 2 * Math.PI, dx = Math.sin(a), dy = Math.cos(a);
      let tanMax = 0;
      for (let d = step; d <= maxDist; d += step) {
        if ((index.maxH - z) / d <= tanMax) break;
        const h = heightAbove(index, buildings, x + dx * d, y + dy * d, z);
        if (h > 0) tanMax = Math.max(tanMax, (h - z) / d);
      }
      const c = Math.cos(Math.atan(tanMax));
      sum += c * c;
    }
    return sum / N;
  }

  /* ---------- roof analysis ---------- */
  function longestEdgeAngle(p) {
    let best = 0, ang = 0;
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
      const dx = p[i][0] - p[j][0], dy = p[i][1] - p[j][1], L = Math.hypot(dx, dy);
      if (L > best) { best = L; ang = Math.atan2(dy, dx); }
    }
    return ang;
  }

  // Inside one of the usable areas from step 2 (each area = [outer ring, ...holes]).
  function insideUsable(x, y, usable) {
    return usable.some(([outer, ...holes]) => pointInPolygon(x, y, outer) && !holes.some((h) => pointInPolygon(x, y, h)));
  }

  // Panel slots on the roof, aligned to its longest edge, kept `setback` metres from every edge.
  // With `usable` (step 2 roof reading), a panel must lie fully on the usable area instead:
  // step 2 has already kept it away from edges, courtyards, stairs, tanks and AC units.
  // The usable area is irregular, so several grid shifts and both panel directions are tried
  // and the layout with the most panels is kept.
  function panelSlots(b, cfg, usable) {
    const p = b.xy;
    const th = longestEdgeAngle(p);
    const c = Math.cos(th), s = Math.sin(th);
    const toUV = ([x, y]) => [x * c + y * s, -x * s + y * c];
    const toXY = (u, v) => [u * c - v * s, u * s + v * c];
    const [u0, v0, u1, v1] = bbox(p.map(toUV));
    const gap = cfg.PANEL_GAP_M;
    const rings = [p, ...(b.holes || [])];
    const onRoof = ([x, y]) =>
      insideFootprint(x, y, b) && rings.every((r) => minEdgeDistance(x, y, r) >= cfg.ROOF_SETBACK_M - 1e-6);

    function layout(L, W, du, dv, inset) {
      const slots = [];
      for (let u = u0 + inset + du; u + L <= u1 - inset + 1e-6; u += L + gap) {
        for (let v = v0 + inset + dv; v + W <= v1 - inset + 1e-6; v += W + gap) {
          const corners = [toXY(u, v), toXY(u + L, v), toXY(u + L, v + W), toXY(u, v + W)];
          // usable: also test edge midpoints and centre, so a panel cannot straddle a small obstacle
          const ok = usable
            ? [0, 0.5, 1].every((a) => [0, 0.5, 1].every((q) => insideUsable(...toXY(u + a * L, v + q * W), usable)))
            : corners.every(onRoof);
          if (!ok) continue;
          const [cx, cy] = toXY(u + L / 2, v + W / 2);
          slots.push({ corners, cx, cy });
        }
      }
      return slots;
    }

    const L = cfg.PANEL_LENGTH_M, W = cfg.PANEL_WIDTH_M;
    if (!usable) return layout(L, W, 0, 0, cfg.ROOF_SETBACK_M);
    let best = [];
    for (const [l, w] of [[L, W], [W, L]])
      for (let du = 0; du < l + gap; du += 0.25)
        for (let dv = 0; dv < w + gap; dv += 0.25) {
          const slots = layout(l, w, du, dv, 0);
          if (slots.length > best.length) best = slots;
        }
    return best;
  }

  // Full analysis of one roof: slots, shade, sky view, yearly sun per slot.
  // `usable`: optional step 2 usable areas in local x/y (see panelSlots).
  function analyzeRoof(buildings, index, k, sun, cfg, usable) {
    const b = buildings[k];
    const slots = panelSlots(b, cfg, usable);
    const z = b.height + cfg.PANEL_HEIGHT_ABOVE_ROOF_M;
    const kd = cfg.DIFFUSE_FRACTION;
    for (const sl of slots) {
      let lit = 0;
      for (const s of sun) if (!isShaded(index, buildings, sl.cx, sl.cy, z, s)) lit += s.w;
      sl.direct = lit;                                    // share of yearly direct sun that reaches the panel
      sl.sky = skyView(index, buildings, sl.cx, sl.cy, z); // share of open sky
      sl.sunFactor = (1 - kd) * sl.direct + kd * sl.sky;  // 1 = no shade at all
      sl.kwhM2 = cfg.GHI_KWH_M2_YEAR * sl.sunFactor;      // yearly sun on the panel, kWh/m²
    }
    return { slots, roofArea: footprintArea(b) };
  }

  /* ---------- energy and demand ---------- */
  function panelYearKwh(slot, cfg) {
    return slot.kwhM2 * cfg.PANEL_LENGTH_M * cfg.PANEL_WIDTH_M * cfg.PANEL_EFFICIENCY * cfg.PERFORMANCE_RATIO;
  }

  // Yearly electricity use: floor area × intensity. b.floors / b.intensity can be set from city data.
  function buildingDemand(b, cfg) {
    const floors = b.floors || Math.max(1, Math.round(b.height / cfg.FLOOR_HEIGHT_M));
    return footprintArea(b) * floors * (b.intensity || cfg.DEMAND_KWH_M2_YEAR);
  }

  // Who gets the energy. Returns Map(buildingIndex -> {received, demand, share}).
  function allocate(buildings, k, production, cfg, policy) {
    const me = buildings[k];
    const inRing = [];
    buildings.forEach((b, i) => {
      const d = Math.hypot(b.c[0] - me.c[0], b.c[1] - me.c[1]);
      if (d <= cfg.SHARE_RADIUS_M) inRing.push({ i, d, demand: b.demand });
    });
    const res = new Map();
    if (policy === "equal") {
      const total = inRing.reduce((s, o) => s + o.demand, 0) || 1;
      const share = Math.min(1, production / total);
      for (const o of inRing) res.set(o.i, { received: o.demand * share, demand: o.demand, share });
      return res;
    }
    // "own-first": own building, then nearest neighbours get their full demand until energy runs out
    inRing.sort((a, z) => (a.i === k ? -1 : z.i === k ? 1 : a.d - z.d));
    let left = production;
    for (const o of inRing) {
      if (left <= 0) break;
      const got = Math.min(left, o.demand);
      left -= got;
      res.set(o.i, { received: got, demand: o.demand, share: o.demand ? got / o.demand : 0 });
    }
    return res;
  }

  const api = {
    makeProjection, polygonArea, footprintArea, insideFootprint, centroid, pointInPolygon, bbox, cleanRing, buildIndex,
    sunPosition, sunSamples, isShaded, skyView, insideUsable, panelSlots, analyzeRoof, panelYearKwh, buildingDemand, allocate,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Solar = api;
})(typeof window !== "undefined" ? window : globalThis);
