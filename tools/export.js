// Turns one building of a Skyline City into a build file for the citybuild turtle program.
//   node export.js <city.html> <seed> <maxH> list
//   node export.js <city.html> <seed> <maxH> <building #> <out file>
// The generator code is taken straight from the viewer page, so the build matches what you clicked.
const fs = require("fs");
const [html, seed, maxH, which, out] = process.argv.slice(2);
const src = fs.readFileSync(html, "utf8");
const a = src.indexOf("// ---------- textures ----------"), b = src.indexOf("// ---------- scene ----------");
if (a < 0 || b < 0) throw new Error("generator code not found in " + html);
const gen = new Function(src.slice(a, b) + "\nreturn { generate, M, GRID };")();

// item ids and damage values in the TechIt-ng pack (TE storage 928, Big Reactors metal 1763, Extra Utilities
// decorative_2 4095). These are what the items in a chest carry: leaves are 18:0 as an item (placed by a
// player or turtle they get the no-decay flag by themselves), so never put block-only metadata here.
const IDS = {
  stone: [1, 0], stonebrick: [98, 0], brick: [45, 0], sand: [24, 0], sandchis: [24, 1], quartz: [155, 0], qpillar: [155, 2],
  nbrick: [112, 0], obsidian: [49, 0], glow: [89, 0], redstone: [152, 0], lapis: [22, 0], gold: [41, 0], iron: [42, 0],
  diamond: [57, 0], emerald: [133, 0], copper: [928, 0], tin: [928, 1], silver: [928, 2], lead: [928, 3], electrum: [928, 6],
  invar: [928, 7], yellorium: [1763, 0], clayW: [159, 0], clayO: [159, 1], clayY: [159, 4], clayLG: [159, 8], clayC: [159, 9],
  clayB: [159, 11], clayR: [159, 14], clayK: [159, 15], glass: [4095, 0], glassLit: [4095, 0], ironbars: [101, 0],
  grass: [2, 0], log: [17, 0], leaves: [18, 0], water: [9, 0], magnum: [2522, 0],
};

const city = gen.generate(+seed, +maxH).list;
if (which === "list") {
  city.forEach((c, i) => {
    if (c.style === "street") return;
    let lo = [1e9, 1e9], hi = [-1e9, -1e9], top = 0;
    for (const k of c.m.keys()) { const [x, y, z] = k.split(",").map(Number); lo = [Math.min(lo[0], x), Math.min(lo[1], z)]; hi = [Math.max(hi[0], x), Math.max(hi[1], z)]; top = Math.max(top, y); }
    console.log(`${String(i).padStart(3)}  ${c.name.padEnd(22)} ${String(hi[0] - lo[0] + 1).padStart(3)}x${String(hi[1] - lo[1] + 1).padEnd(3)} ${String(top + 1).padStart(4)} tall  ${String(c.m.size).padStart(6)} blocks  at ${lo[0]},${lo[1]}`);
  });
  process.exit(0);
}

const bld = city[+which];
const cells = [];
for (const [k, key] of bld.m) {
  if (key === "water") continue;   // water needs a bucket turtle; left out of the pilot
  const [x, y, z] = k.split(",").map(Number);
  cells.push({ x, y, z, key: key === "glassLit" ? "glass" : key });
}
const lo = [Math.min(...cells.map(c => c.x)), Math.min(...cells.map(c => c.z))];
const W = Math.max(...cells.map(c => c.x)) - lo[0] + 1, D = Math.max(...cells.map(c => c.z)) - lo[1] + 1, H = Math.max(...cells.map(c => c.y)) + 1;

// materials, numbered by first use
const mats = [], matNo = {};
for (const c of cells.sort((p, q) => p.y - q.y)) if (!(c.key in matNo)) { matNo[c.key] = mats.length + 1; mats.push(c.key); }

// Within each layer, the next block is the one quickest to reach from where the turtle is and the way
// it faces. Moves and turns both take 0.4 s, and the turtle goes along x first, then z.
const DXF = [1, 0, -1, 0], DZF = [0, 1, 0, -1];
const turnsTo = (f, g) => Math.min((g - f + 4) % 4, (f - g + 4) % 4);
function cost(px, pz, f, x, z) {
  let t = (Math.abs(x - px) + Math.abs(z - pz)) * 10, face = f;   // distance first, turns break ties
  if (x !== px) { const g = x > px ? 0 : 2; t += turnsTo(face, g); face = g; }
  if (z !== pz) { const g = z > pz ? 1 : 3; t += turnsTo(face, g); face = g; }
  return [t, face];
}
const lines = [];
lines.push("citybuild 1");
lines.push(`name ${bld.name}`);
lines.push(`city ${seed} ${maxH} ${which}`);
lines.push(`size ${W} ${H} ${D}`);
for (const k of mats) { const [id, dmg] = IDS[k]; lines.push(`mat ${matNo[k]} ${id} ${dmg} ${gen.M[k].name}`); }
let px = -1, pz = -1, pf = 0, total = 0;
for (let y = 0; y < H; y++) {
  const layer = cells.filter(c => c.y === y).map(c => ({ x: c.x - lo[0], z: c.z - lo[1], m: matNo[c.key] }));
  if (!layer.length) continue;
  lines.push(`L ${y}`);
  const left = new Set(layer.map((_, i) => i));
  while (left.size) {
    let best = -1, bc = 1e9, bf = pf;
    for (const i of left) { const [c, f] = cost(px, pz, pf, layer[i].x, layer[i].z); if (c < bc) { bc = c; best = i; bf = f; } }
    left.delete(best);
    const c = layer[best];
    lines.push(`${c.x} ${c.z} ${c.m}`); px = c.x; pz = c.z; pf = bf; total++;
  }
}
lines.push("end");
fs.writeFileSync(out, lines.join("\n") + "\n");
console.log(`${bld.name}: ${W}x${D}, ${H} tall, ${total} blocks, ${mats.length} materials -> ${out} (${fs.statSync(out).size} bytes)`);
