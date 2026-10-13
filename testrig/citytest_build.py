# Puts a citybuild test site into the load-test world (server must be stopped):
#   a Narcissistic + diamond pickaxe turtle at the build's start spot, running citybuild on startup,
#   a public Ender Chest (frequency FREQ) with a Diamond Chest of supplies west of it and a feeder
#   computer south of it standing in for AE, and a ChickenChunks loader over the whole site.
#   python3 citytest_build.py <world> <build file> <citybuild> <feeder> <origin x> <origin z>
import sys, os, struct, zlib
sys.path.insert(0, "/home/server/loadtest")
import nbt

WORLD, PLAN, CITYBUILD, FEEDER = sys.argv[1:5]
OX, OZ = int(sys.argv[5]), int(sys.argv[6])
GY = 4                       # first layer above the grass of the flat test world
FREQ = 291                   # Ender Chest frequency for the test
blocks, tes = {}, {}

# what the building needs, from the build file
need, mats = {}, {}
for line in open(PLAN):
    p = line.split()
    if p and p[0] == "mat":
        mats[int(p[1])] = (int(p[2]), int(p[3]))
    elif len(p) == 3 and p[0].lstrip("-").isdigit():
        need[int(p[2])] = need.get(int(p[2]), 0) + 1

# supply chest: every block the build needs (+10%), and 6 stacks of coal
items, cfg_slots, slot = [], [], 0
def add(i, d, n, keep):
    global slot
    while n > 0:
        k = min(64, n)
        items.append(nbt.C(Slot=nbt.B(slot), id=nbt.S(i), Count=nbt.B(k), Damage=nbt.S(d)))
        cfg_slots.append((slot + 1, i, d, keep))
        slot += 1; n -= k
for m, n in need.items():
    i, d = mats[m]
    add(i, d, int(n * 1.1) + 1, 128)
add(263, 0, 6 * 64, 64)
assert slot <= 108, "too much for one Diamond Chest"

E, D, C = (OX - 7, GY, OZ + 2), (OX - 8, GY, OZ + 2), (OX - 7, GY, OZ + 3)
blocks[E] = (801, 0)
tes[E] = nbt.C(id=nbt.STR("Ender Chest"), x=nbt.I(E[0]), y=nbt.I(E[1]), z=nbt.I(E[2]), owner=nbt.STR("global"), freq=nbt.I(FREQ), rot=nbt.B(0))
blocks[D] = (831, 2)
tes[D] = nbt.C(id=nbt.STR("IronChest.DIAMOND"), x=nbt.I(D[0]), y=nbt.I(D[1]), z=nbt.I(D[2]), facing=nbt.B(3),
               Items=nbt.LIST(nbt.TAG_COMPOUND, items))
L = (OX - 6, GY, OZ + 6)
blocks[L] = (781, 0)
tes[L] = nbt.C(id=nbt.STR("ChickenChunkLoader"), x=nbt.I(L[0]), y=nbt.I(L[1]), z=nbt.I(L[2]),
               owner=nbt.STR("citytest"), powered=nbt.B(0), shape=nbt.B(0), radius=nbt.B(3))   # ChickenChunks loads radius-1 chunks around it

cdir = os.path.join(WORLD, "computer")
last = int(open(os.path.join(cdir, "lastid.txt")).read().strip())
feeder_id, turtle_id = last + 1, last + 2
open(os.path.join(cdir, "lastid.txt"), "w").write(str(turtle_id))
for cid in (feeder_id, turtle_id):
    os.makedirs(os.path.join(cdir, str(cid)), exist_ok=True)

# the feeder computer
blocks[C] = (764, 0)
tes[C] = nbt.C(id=nbt.STR("computer"), x=nbt.I(C[0]), y=nbt.I(C[1]), z=nbt.I(C[2]), computerID=nbt.I(feeder_id),
               label=nbt.STR("feeder"), on=nbt.B(1))
fd = os.path.join(cdir, str(feeder_id))
open(os.path.join(fd, "feeder"), "w").write(open(FEEDER).read())
open(os.path.join(fd, "startup"), "w").write('shell.run("feeder")\n')
lua = "{side=\"west\",slots={" + ",".join("[%d]={id=%d,dmg=%d,keep=%d}" % s for s in cfg_slots) + "}}"
open(os.path.join(fd, "supply.cfg"), "w").write(lua)

# the turtle: advanced, Narcissistic (181) left, diamond pickaxe (5) right, Ender Chest in slot 16
T = (OX - 1, GY, OZ - 1)
blocks[T] = (1230, 0)
tes[T] = nbt.C(id=nbt.STR("turtleadv"), x=nbt.I(T[0]), y=nbt.I(T[1]), z=nbt.I(T[2]), computerID=nbt.I(turtle_id),
               label=nbt.STR("citytest"), on=nbt.B(1), fuelLevel=nbt.I(400), dir=nbt.I(5), selectedSlot=nbt.I(0),
               leftUpgrade=nbt.I(181), rightUpgrade=nbt.I(5),
               Items=nbt.LIST(nbt.TAG_COMPOUND, [nbt.C(Slot=nbt.B(15), id=nbt.S(801), Count=nbt.B(1), Damage=nbt.S(FREQ))]))
td = os.path.join(cdir, str(turtle_id))
open(os.path.join(td, "citybuild"), "w").write(open(CITYBUILD).read())
open(os.path.join(td, "test.plan"), "w").write(open(PLAN).read())
open(os.path.join(td, "startup"), "w").write(
    'if fs.exists("city.state") then shell.run("citybuild") else shell.run("citybuild", "test.plan", "go") end\n')

# write it into the region files (same writer as the AE rig)
def nib_set(arr, i, v):
    if i & 1: arr[i >> 1] = (arr[i >> 1] & 0x0F) | ((v & 15) << 4)
    else: arr[i >> 1] = (arr[i >> 1] & 0xF0) | (v & 15)

chunks = {}
for (x, y, z) in blocks:
    chunks.setdefault((x >> 4, z >> 4), []).append((x, y, z))
for (cx, cz), cells in chunks.items():
    rpath = os.path.join(WORLD, "region", "r.%d.%d.mca" % (cx >> 5, cz >> 5))
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
    lv = ch.v["Level"].v
    secs = {s.v["Y"].v: s for s in lv["Sections"].v}
    for (x, y, z) in cells:
        sy = y >> 4
        if sy not in secs:
            secs[sy] = nbt.C(Y=nbt.B(sy), Blocks=nbt.BA(bytearray(4096)), Data=nbt.BA(bytearray(2048)),
                             BlockLight=nbt.BA(bytearray(2048)), SkyLight=nbt.BA(bytearray([255] * 2048)))
            lv["Sections"].v.append(secs[sy])
        s = secs[sy].v
        j = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
        b, m = blocks[(x, y, z)]
        s["Blocks"].v[j] = b & 255
        if b >> 8:
            if "Add" not in s: s["Add"] = nbt.BA(bytearray(2048))
            nib_set(s["Add"].v, j, b >> 8)
        elif "Add" in s:
            nib_set(s["Add"].v, j, 0)
        nib_set(s["Data"].v, j, m)
    keep = [t for t in lv["TileEntities"].v if (t.v["x"].v, t.v["y"].v, t.v["z"].v) not in tes]
    lv["TileEntities"].v = keep + [tes[p] for p in cells if p in tes]
    lv["TileEntities"].lt = nbt.TAG_COMPOUND
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
    print("wrote chunk", cx, cz, "blocks", len(cells))
print("feeder computer", feeder_id, "turtle", turtle_id, "supply slots", slot, "turtle at", T, "Ender Chest", E)
