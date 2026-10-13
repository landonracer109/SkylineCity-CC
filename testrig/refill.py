# Sets slots of a chest in a saved world (server stopped): python3 refill.py <world> <x> <y> <z> <slot>:<id>:<dmg>:<count> ...
# Slots are 1-based like CC's. Count 0 empties the slot.
import sys, os, struct, zlib
sys.path.insert(0, "/home/server/loadtest")
import nbt
W, X, Y, Z = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
sets = [tuple(map(int, a.split(":"))) for a in sys.argv[5:]]
cx, cz = X >> 4, Z >> 4
rpath = os.path.join(W, "region", "r.%d.%d.mca" % (cx >> 5, cz >> 5))
data = bytearray(open(rpath, "rb").read())
allc = {}
for i in range(1024):
    off = struct.unpack(">I", data[i * 4:i * 4 + 4])[0]
    if not off: continue
    s = (off >> 8) * 4096
    ln = struct.unpack(">I", data[s:s + 4])[0]
    allc[i] = bytes(data[s + 5:s + 4 + ln])
idx = (cx & 31) + (cz & 31) * 32
name, ch = nbt.read(zlib.decompress(allc[idx]))
te = next(t for t in ch.v["Level"].v["TileEntities"].v if (t.v["x"].v, t.v["y"].v, t.v["z"].v) == (X, Y, Z))
items = [it for it in te.v["Items"].v if it.v["Slot"].v + 1 not in {s[0] for s in sets}]
for slot, i, d, n in sets:
    if n > 0: items.append(nbt.C(Slot=nbt.B(slot - 1), id=nbt.S(i), Count=nbt.B(n), Damage=nbt.S(d)))
te.v["Items"].v = items
allc[idx] = zlib.compress(nbt.write(name, ch))
out = bytearray(8192); body = bytearray(); sector = 2
for i, comp in sorted(allc.items()):
    payload = struct.pack(">IB", len(comp) + 1, 2) + comp
    n = (len(payload) + 4095) // 4096
    payload += b"\0" * (n * 4096 - len(payload))
    out[i * 4:i * 4 + 4] = struct.pack(">I", (sector << 8) | n)
    out[4096 + i * 4:4096 + i * 4 + 4] = data[4096 + i * 4:4096 + i * 4 + 4]
    body += payload; sector += n
open(rpath, "wb").write(out + body)
print("chest", (X, Y, Z), "now", len(items), "stacks")
