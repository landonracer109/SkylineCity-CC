// Turns a whole Skyline City into turtle jobs: platform tiles, buildings, street furniture tiles and
// the park water, plus jobs.json listing them in build order with what each one waits for.
//   node export-all.js <city.html> <seed> <maxH> <ceiling> <out dir> [originX originY originZ]
// originY is the world y of building layer 0 (the platform goes one below it). Every job file is the
// same format citybuild reads, with two extra header lines it ignores: "job <id> <kind>" and
// "at <x> <y> <z>" (world spot of the job's local 0,0,0).
const fs = require("fs"), path = require("path");
const [html, seed, maxH, ceiling, outDir, ox = -866, oy = 63, oz = -466] = process.argv.slice(2);
const O = [+ox, +oy, +oz];
const src = fs.readFileSync(html, "utf8");
const a = src.indexOf("// ---------- textures ----------"), b = src.indexOf("// ---------- scene ----------");
if (a < 0 || b < 0) throw new Error("generator code not found in " + html);
const gen = new Function(src.slice(a, b) + "\nreturn { generate, M, GRID };")();

// item ids and damage values in the TechIt-ng pack (see export.js)
const IDS = {
  stone: [1, 0], stonebrick: [98, 0], brick: [45, 0], sand: [24, 0], sandchis: [24, 1], quartz: [155, 0], qpillar: [155, 2],
  nbrick: [112, 0], obsidian: [49, 0], glow: [89, 0], redstone: [152, 0], lapis: [22, 0], gold: [41, 0], iron: [42, 0],
  diamond: [57, 0], emerald: [133, 0], copper: [928, 0], tin: [928, 1], silver: [928, 2], lead: [928, 3], electrum: [928, 6],
  invar: [928, 7], yellorium: [1763, 0], clayW: [159, 0], clayO: [159, 1], clayY: [159, 4], clayLG: [159, 8], clayC: [159, 9],
  clayB: [159, 11], clayR: [159, 14], clayK: [159, 15], glass: [4095, 0], glassLit: [4095, 0], ironbars: [101, 0],
  grass: [2, 0], log: [17, 0], leaves: [18, 0], water: [326, 0], magnum: [2522, 0],
};
const NAMES = { water: "Water Bucket" };
const nameOf = k => NAMES[k] || gen.M[k].name;

const { list, layout: L } = gen.generate(+seed, +maxH, +ceiling);
const { spanX, spanZ } = L;

// ---------- the platform (layer -1) and the highway barriers (layer 0) ----------
const plat = Array.from({ length: spanX }, () => Array(spanZ).fill("stone"));
const top = new Map();   // "x,z" -> block at layer 0 (barriers)
const inX = (v, s, n) => v >= s && v < s + n;
const lotAt = (x, z) => L.lots.find(l => x >= l.x0 - 1 && x <= l.x0 + l.W && z >= l.z0 - 1 && z <= l.z0 + l.D);
const crossing = (x, z) => L.roadX.some((s, k) => inX(x, s, L.rx[k])) && L.roadZ.some((s, k) => inX(z, s, L.rz[k]));
for (let x = 0; x < spanX; x++) for (let z = 0; z < spanZ; z++) {
  const road = L.roadX.some((s, k) => inX(x, s, L.rx[k])) || L.roadZ.some((s, k) => inX(z, s, L.rz[k]));
  if (road && !lotAt(x, z)) plat[x][z] = "clayK";   // sidewalks and lots stay stone
}
// medians (boulevards), barriers (highways), lane lines and centre dashes; never on a crossing
// except the barrier and median, which run straight through
const hx = L.roadX[L.hx], hz = L.roadZ[L.hz], bx = L.roadX[L.bx], bz = L.roadZ[L.bz];
for (let z = 0; z < spanZ; z++) for (const x of [bx + 5, bx + 6]) if (!crossing(x, z)) plat[x][z] = "grass";
for (let x = 0; x < spanX; x++) for (const z of [bz + 5, bz + 6]) if (!crossing(x, z)) plat[x][z] = "grass";
for (let z = 0; z < spanZ; z++) for (const x of [hx + 8, hx + 9]) if (!crossing(x, z)) top.set(x + "," + z, "stonebrick");
for (let x = 0; x < spanX; x++) for (const z of [hz + 8, hz + 9]) if (!crossing(x, z)) top.set(x + "," + z, "stonebrick");
const dash = t => t % 5 === 1 || t % 5 === 2;   // 2 on, 3 off
for (let z = 0; z < spanZ; z++) for (const x of [hx + 4, hx + 13]) if (dash(z) && !crossing(x, z)) plat[x][z] = "clayW";
for (let x = 0; x < spanX; x++) for (const z of [hz + 4, hz + 13]) if (dash(x) && !crossing(x, z)) plat[x][z] = "clayW";
// Street centre dashes go in the exact middle of the black part (the sidewalks take a block off each
// side): 1 block wide where that is an odd width, 2 where it is even.
const middle = (cells) => {
  const run = cells.filter(([x, z]) => plat[x][z] === "clayK");
  if (!run.length) return [];
  const n = run.length, m = Math.floor((n - 1) / 2);
  return n % 2 ? [run[m]] : [run[m], run[m + 1]];
};
for (let k = 0; k <= gen.GRID; k++) {
  if (L.rx[k] <= 8) for (let z = 0; z < spanZ; z++) {
    if (!dash(z)) continue;
    const across = []; for (let x = L.roadX[k]; x < L.roadX[k] + L.rx[k]; x++) if (!crossing(x, z)) across.push([x, z]);
    if (across.length === L.rx[k]) for (const [x, zz] of middle(across)) plat[x][zz] = "clayY";
  }
  if (L.rz[k] <= 8) for (let x = 0; x < spanX; x++) {
    if (!dash(x)) continue;
    const across = []; for (let z = L.roadZ[k]; z < L.roadZ[k] + L.rz[k]; z++) if (!crossing(x, z)) across.push([x, z]);
    if (across.length === L.rz[k]) for (const [xx, z] of middle(across)) plat[xx][z] = "clayY";
  }
}

// ---------- writing a job ----------
// cells: [{ x, y, z, key }] in city coordinates (y 0 = building ground). Returns the job record.
const turnsTo = (f, g) => Math.min((g - f + 4) % 4, (f - g + 4) % 4);
function cost(px, pz, f, x, z) {
  let t = (Math.abs(x - px) + Math.abs(z - pz)) * 10, face = f;
  if (x !== px) { const g = x > px ? 0 : 2; t += turnsTo(face, g); face = g; }
  if (z !== pz) { const g = z > pz ? 1 : 3; t += turnsTo(face, g); face = g; }
  return [t, face];
}
// nearest-next ordering, but on a grid of buckets so big layers (a 32x32 tile, a 46x46 tower floor) stay fast
function order(layer, px, pz, pf) {
  const out = [], left = new Set(layer.map((_, i) => i));
  while (left.size) {
    let best = -1, bc = 1e9, bf = pf;
    for (const i of left) {
      const d = Math.abs(layer[i].x - px) + Math.abs(layer[i].z - pz);
      if (d * 10 >= bc) continue;
      const [c, f] = cost(px, pz, pf, layer[i].x, layer[i].z);
      if (c < bc) { bc = c; best = i; bf = f; }
    }
    left.delete(best);
    const c = layer[best]; out.push(c); px = c.x; pz = c.z; pf = bf;
  }
  return [out, px, pz, pf];
}
fs.mkdirSync(outDir, { recursive: true });
const jobs = [];
function writeJob(id, kind, name, cells) {
  if (!cells.length) return null;
  const lo = [Math.min(...cells.map(c => c.x)), Math.min(...cells.map(c => c.y)), Math.min(...cells.map(c => c.z))];
  const hi = [Math.max(...cells.map(c => c.x)), Math.max(...cells.map(c => c.y)), Math.max(...cells.map(c => c.z))];
  const W = hi[0] - lo[0] + 1, H = hi[1] - lo[1] + 1, D = hi[2] - lo[2] + 1;
  const mats = [], matNo = {}, need = {};
  for (const c of [...cells].sort((p, q) => p.y - q.y)) {
    if (!(c.key in matNo)) { matNo[c.key] = mats.length + 1; mats.push(c.key); }
    need[c.key] = (need[c.key] || 0) + 1;
  }
  const lines = ["citybuild 1", `name ${name}`, `job ${id} ${kind}`, `city ${seed} ${maxH} ${ceiling}`,
    `at ${O[0] + lo[0]} ${O[1] + lo[1]} ${O[2] + lo[2]}`, `size ${W} ${H} ${D}`];
  for (const k of mats) { const [iid, dmg] = IDS[k]; lines.push(`mat ${matNo[k]} ${iid} ${dmg} ${nameOf(k)}`); }
  const byY = new Map();
  for (const c of cells) { const y = c.y - lo[1]; if (!byY.has(y)) byY.set(y, []); byY.get(y).push({ x: c.x - lo[0], z: c.z - lo[2], m: matNo[c.key] }); }
  let px = -1, pz = -1, pf = 0;
  for (const y of [...byY.keys()].sort((p, q) => p - q)) {
    lines.push(`L ${y}`);
    let out; [out, px, pz, pf] = order(byY.get(y), px, pz, pf);
    for (const c of out) lines.push(`${c.x} ${c.z} ${c.m}`);
  }
  lines.push("end");
  const file = id + ".txt";
  fs.writeFileSync(path.join(outDir, file), lines.join("\n") + "\n");
  const job = { id, kind, name, file, at: [O[0] + lo[0], O[1] + lo[1], O[2] + lo[2]], size: [W, H, D], blocks: cells.length,
    need: Object.fromEntries(mats.map(k => [IDS[k].join(":"), need[k]])), box: [lo[0], lo[2], hi[0], hi[2]], after: [] };
  jobs.push(job);
  return job;
}

// ---------- jobs ----------
const TILE = 32, tx = Math.ceil(spanX / TILE), tz = Math.ceil(spanZ / TILE);
const tileId = (i, j) => `g${i}-${j}`;
const tilesUnder = box => { const r = []; for (let i = Math.floor(box[0] / TILE); i <= Math.floor(box[2] / TILE); i++) for (let j = Math.floor(box[1] / TILE); j <= Math.floor(box[3] / TILE); j++) if (i >= 0 && j >= 0 && i < tx && j < tz) r.push(tileId(i, j)); return r; };

// 1. platform tiles (+ barriers on top)
for (let i = 0; i < tx; i++) for (let j = 0; j < tz; j++) {
  const cells = [];
  for (let x = i * TILE; x < Math.min(spanX, (i + 1) * TILE); x++) for (let z = j * TILE; z < Math.min(spanZ, (j + 1) * TILE); z++) {
    cells.push({ x, y: -1, z, key: plat[x][z] });
    const t = top.get(x + "," + z); if (t) cells.push({ x, y: 0, z, key: t });
  }
  writeJob(tileId(i, j), "ground", `Platform ${i},${j}`, cells);
}
const SPLIT = 8000;   // blocks: bigger buildings are split into parts
// 2. buildings (water left for the bucket job), each waiting for the tiles under it (+1 round it)
const water = [], street = list.find(s => s.style === "street");
list.forEach((bld, n) => {
  if (bld === street) return;
  const cells = [];
  for (const [k, key] of bld.m) {
    const [x, y, z] = k.split(",").map(Number);
    if (key === "water") water.push({ x, y, z, key }); else cells.push({ x, y, z, key: key === "glassLit" ? "glass" : key });
  }
  // A big building is cut into side-by-side parts (along its longer side, at the middle block) so
  // several turtles can build it at once: each part is its own job with its own area.
  const parts = [cells];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.length <= SPLIT) continue;
    const ext = a => Math.max(...p.map(c => c[a])) - Math.min(...p.map(c => c[a]));
    const a = ext("x") >= ext("z") ? "x" : "z";
    const vals = p.map(c => c[a]).sort((u, v) => u - v), cut = vals[Math.floor(vals.length / 2)];
    parts.splice(i, 1, p.filter(c => c[a] < cut), p.filter(c => c[a] >= cut));
    i--;
  }
  const id = "b" + String(n).padStart(2, "0");
  parts.forEach((p, k) => {
    const job = writeJob(parts.length > 1 ? id + "abcdefgh"[k] : id, bld.style === "park" ? "park" : "tower",
      parts.length > 1 ? `${bld.name} (part ${k + 1} of ${parts.length})` : bld.name, p);
    if (job) { const b = job.box; job.after = tilesUnder([b[0] - 1, b[1] - 1, b[2] + 1, b[3] + 1]); job.height = job.size[1]; }
  });
});
// 3. street furniture by tile, after the platform under it. Street tiles go BEFORE the buildings
// they touch: a street turtle works low across its whole tile, so it must not meet finished towers.
const towers = jobs.filter(j => j.kind === "tower" || j.kind === "park");
const touches = (p, q) => p[0] <= q[2] && q[0] <= p[2] && p[1] <= q[3] && q[1] <= p[3];
for (let i = 0; i < tx; i++) for (let j = 0; j < tz; j++) {
  const cells = [];
  for (const [k, key] of street.m) {
    const [x, y, z] = k.split(",").map(Number);
    // the highway lamp posts stand on the barrier, so they start one block up
    if (Math.floor(x / TILE) === i && Math.floor(z / TILE) === j) cells.push({ x, y: top.has(x + "," + z) ? y + 1 : y, z, key });
  }
  const job = writeJob(`s${i}-${j}`, "street", `Street ${i},${j}`, cells);
  if (!job) continue;
  job.after = tilesUnder([job.box[0] - 2, job.box[1] - 2, job.box[2] + 2, job.box[3] + 2]);
  for (const t of towers) if (touches(t.box, [job.box[0] - 3, job.box[1] - 3, job.box[2] + 3, job.box[3] + 3])) t.after.push(job.id);
}
// 4. park water (bucket turtle), after the parks
const wj = writeJob("water", "water", "Park water", water);
if (wj) wj.after = towers.filter(t => t.kind === "park").map(t => t.id);

// ---------- the finished city's height per column (for routing): 1 + the highest block above the
// platform (barriers included), 0 where nothing stands. Rows of x, one string per z, base-36 digits joined by commas.
const colTop = Array.from({ length: spanZ }, () => new Array(spanX).fill(0));
for (const j of jobs) {
  let y = 0;
  for (const l of fs.readFileSync(path.join(outDir, j.file), "utf8").split("\n")) {
    let m;
    if ((m = l.match(/^L (-?\d+)/))) y = +m[1];
    else if ((m = l.match(/^(-?\d+) (-?\d+) (\d+)$/))) {
      const x = +m[1] + j.at[0] - O[0], z = +m[2] + j.at[2] - O[2], h = y + j.at[1] - O[1] + 1;
      if (h > colTop[z][x]) colTop[z][x] = h;   // the platform (h 0) doesn't count, barriers do
    }
  }
}
// ---------- summary ----------
const total = {}, kinds = {};
for (const j of jobs) { kinds[j.kind] = (kinds[j.kind] || 0) + 1; for (const [k, n] of Object.entries(j.need)) total[k] = (total[k] || 0) + n; }
fs.writeFileSync(path.join(outDir, "jobs.json"), JSON.stringify({ seed: +seed, maxH: +maxH, ceiling: +ceiling, origin: O, span: [spanX, spanZ], tile: TILE, colTop: colTop.map(r => r.map(v => v.toString(36)).join(",")), jobs }, null, 1));
const blocks = jobs.reduce((s, j) => s + j.blocks, 0);
console.log(`${jobs.length} jobs (${Object.entries(kinds).map(([k, n]) => n + " " + k).join(", ")}), ${blocks} blocks -> ${outDir}`);
for (const [k, n] of Object.entries(total).sort((p, q) => q[1] - p[1])) console.log(`  ${k.padEnd(8)} ${String(n).padStart(7)}`);
