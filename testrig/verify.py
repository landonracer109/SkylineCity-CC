# Compares a built building in a saved world with its build file.
#   python3 verify.py <world> <build file> <origin x> <origin y> <origin z>
import sys, struct, zlib
sys.path.insert(0, "/home/server/loadtest")
import nbt

WORLD, PLAN = sys.argv[1], sys.argv[2]
OX, OY, OZ = map(int, sys.argv[3:6])
mats, cells, y = {}, [], 0
for line in open(PLAN):
    p = line.split()
    if not p: continue
    if p[0] == "mat": mats[int(p[1])] = (int(p[2]), int(p[3]), " ".join(p[4:]))
    elif p[0] == "L": y = int(p[1])
    elif len(p) == 3 and p[0].lstrip("-").isdigit(): cells.append((int(p[0]), y, int(p[1]), int(p[2])))

cache = {}
def chunk(cx, cz):
    if (cx, cz) in cache: return cache[(cx, cz)]
    data = open("%s/region/r.%d.%d.mca" % (WORLD, cx >> 5, cz >> 5), "rb").read()
    i = (cx & 31) + (cz & 31) * 32
    off = struct.unpack(">I", data[i * 4:i * 4 + 4])[0]
    s = (off >> 8) * 4096
    ln = struct.unpack(">I", data[s:s + 4])[0]
    _, ch = nbt.read(zlib.decompress(data[s + 5:s + 4 + ln]))
    secs = {sec.v["Y"].v: sec.v for sec in ch.v["Level"].v["Sections"].v}
    cache[(cx, cz)] = secs
    return secs

def block(x, y, z):
    s = chunk(x >> 4, z >> 4).get(y >> 4)
    if not s: return (0, 0)
    j = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
    b = s["Blocks"].v[j]
    if "Add" in s: b |= ((s["Add"].v[j >> 1] >> (4 if j & 1 else 0)) & 15) << 8
    m = (s["Data"].v[j >> 1] >> (4 if j & 1 else 0)) & 15
    return (b, m)

ok, bad = 0, []
for (x, yy, z, m) in cells:
    want = mats[m]
    got = block(OX + x, OY + yy, OZ + z)
    # leaves get the no-decay flag when placed; quartz pillars keep their axis in the metadata
    if got[0] == want[0] and (got[1] == want[1] or (want[0] == 18 and got[1] & 3 == want[1])):
        ok += 1
    else:
        bad.append((x, yy, z, want, got))
print("blocks right %d of %d" % (ok, len(cells)))
for b in bad[:15]: print("  wrong at", b[:3], "want", b[3], "got", b[4])
