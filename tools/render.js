// top-down picture of the exported jobs (topmost block per column), 3 px per block
const fs = require("fs"), zlib = require("zlib"), path = require("path");
const dir = process.argv[2], J = JSON.parse(fs.readFileSync(path.join(dir, "jobs.json")));
const [CX, CZ, CW, CD] = (process.argv[5] || "0,0," + J.span.join(",")).split(",").map(Number), W = CW, D = CD, S = +(process.argv[4] || 3), img = Buffer.alloc(W * S * D * S * 3), topY = new Int16Array(W * D).fill(-99);
const C = { "1:0": "9a9a9a", "159:15": "251610", "159:0": "e8e8e8", "159:4": "d9b54a", "2:0": "5d973a", "98:0": "6d6d6d", "18:0": "3f7d2c", "17:0": "6b4f2e", "101:0": "8d9298", "89:0": "f4cf72", "4095:0": "a9dbe8", "326:0": "2f6fb5", "2522:0": "ff8800" };
for (const j of J.jobs) {
  const lines = fs.readFileSync(path.join(dir, j.file), "utf8").split("\n"), mats = {}; let y = 0;
  const ox = j.at[0] - J.origin[0], oy = j.at[1] - J.origin[1], oz = j.at[2] - J.origin[2];
  for (const l of lines) {
    let m;
    if ((m = l.match(/^mat (\d+) (\d+) (\d+)/))) mats[m[1]] = m[2] + ":" + m[3];
    else if ((m = l.match(/^L (-?\d+)/))) y = +m[1] + oy;
    else if ((m = l.match(/^(-?\d+) (-?\d+) (\d+)$/))) {
      const x = +m[1] + ox - CX, z = +m[2] + oz - CZ; if (x < 0 || z < 0 || x >= W || z >= D) continue; const i = x * D + z; if (y < topY[i]) continue; topY[i] = y;
      const hex = C[mats[m[3]]] || "c0a0d0", sh = j.kind === "ground" || j.kind === "street" ? 1 : 0.55 + 0.45 * Math.min(1, y / 160);
      for (let a = 0; a < S; a++) for (let b = 0; b < S; b++) { const p = ((z * S + b) * W * S + x * S + a) * 3; for (let c = 0; c < 3; c++) img[p + c] = parseInt(hex.substr(c * 2, 2), 16) * sh; }
    }
  }
}
const w = W * S, h = D * S, raw = Buffer.alloc((w * 3 + 1) * h);
for (let r = 0; r < h; r++) img.copy(raw, r * (w * 3 + 1) + 1, r * w * 3, (r + 1) * w * 3);
const chunk = (t, d) => { const b = Buffer.alloc(8 + d.length + 4); b.writeUInt32BE(d.length); b.write(t, 4); d.copy(b, 8); b.writeUInt32BE(zlib.crc32(Buffer.concat([Buffer.from(t), d])) >>> 0, 8 + d.length); return b; };
const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
fs.writeFileSync(process.argv[3], Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]));
