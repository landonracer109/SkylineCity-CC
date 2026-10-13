# Puts a cityfleet test into the load-test world (server must be stopped):
#   N turtles (Narcissistic + diamond pickaxe) on their depot spots facing north, running cityfleet on
#   startup; one public Ender Chest frequency shared by all of them, with a feeder computer and two
#   Diamond Chests of supplies standing in for AE; a ChickenChunks loader over the whole site.
#   python3 citytest_fleet.py <world> <jobs dir> <job ids (comma list)> <cityfleet> <feeder> <server url> <turtles>
import sys, os, struct, zlib, json
sys.path.insert(0, "/home/server/loadtest")
import nbt

WORLD, JOBS, IDS, CITYFLEET, FEEDER, SERVER, NT = sys.argv[1:8]
IDS, NT = IDS.split(","), int(NT)
J = json.load(open(os.path.join(JOBS, "jobs.json")))
OX, OY, OZ = J["origin"]
DEPOT_X0, DEPOT_Z, STEP = OX + 4, OZ - 2, 8     # must match citysrv's DEPOT_* settings
FREQ = 292
blocks, tes = {}, {}

need = {}
for j in J["jobs"]:
    if j["id"] in IDS:
        for k, n in j["need"].items():
            need[k] = need.get(k, 0) + n

# supply: every block the jobs need (+8% and half a stack), and 2 stacks of coal blocks, over two chests
entries = []          # (chest, slot, id, dmg, count)
def add(i, d, n):
    while n > 0:
        k = min(64, n)
        entries.append((i, d, k)); n -= k
for k, n in sorted(need.items()):
    i, d = map(int, k.split(":"))
    add(i, d, int(n * 1.08) + 32)
add(173, 0, 128)
FREE = 20             # slots kept empty in chest 1 for hand-backs
assert len(entries) <= 216 - FREE, "too much for two Diamond Chests: %d stacks" % len(entries)
E = (OX + 50, OY, OZ - 8)
A, B, C = (E[0] - 1, OY, E[2]), (E[0] + 1, OY, E[2]), (E[0], OY, E[2] + 1)
chestItems = {A: [], B: []}
cfg_list = []
for n, (i, d, k) in enumerate(entries):
    chest, side = (A, "west") if n < 108 - FREE else (B, "east")
    slot = len(chestItems[chest])
    chestItems[chest].append(nbt.C(Slot=nbt.B(slot), id=nbt.S(i), Count=nbt.B(k), Damage=nbt.S(d)))
    cfg_list.append((slot + 1, i, d, 64, side))
blocks[E] = (801, 0)
tes[E] = nbt.C(id=nbt.STR("Ender Chest"), x=nbt.I(E[0]), y=nbt.I(E[1]), z=nbt.I(E[2]), owner=nbt.STR("global"), freq=nbt.I(FREQ), rot=nbt.B(0))
for P in (A, B):
    blocks[P] = (831, 2)
    tes[P] = nbt.C(id=nbt.STR("IronChest.DIAMOND"), x=nbt.I(P[0]), y=nbt.I(P[1]), z=nbt.I(P[2]), facing=nbt.B(3),
                   Items=nbt.LIST(nbt.TAG_COMPOUND, chestItems[P]))
L = (OX + 32, 2, OZ + 30)
blocks[L] = (781, 0)
tes[L] = nbt.C(id=nbt.STR("ChickenChunkLoader"), x=nbt.I(L[0]), y=nbt.I(L[1]), z=nbt.I(L[2]),
               owner=nbt.STR("citytest"), powered=nbt.B(0), shape=nbt.B(0), radius=nbt.B(5))   # loads 4 chunks round it

cdir = os.path.join(WORLD, "computer")
last = int(open(os.path.join(cdir, "lastid.txt")).read().strip())
ids = list(range(last + 1, last + 2 + NT))
open(os.path.join(cdir, "lastid.txt"), "w").write(str(ids[-1]))
for cid in ids:
    os.makedirs(os.path.join(cdir, str(cid)), exist_ok=True)

feeder_id = ids[0]
blocks[C] = (764, 0)
tes[C] = nbt.C(id=nbt.STR("computer"), x=nbt.I(C[0]), y=nbt.I(C[1]), z=nbt.I(C[2]), computerID=nbt.I(feeder_id),
               label=nbt.STR("feeder"), on=nbt.B(1))
fd = os.path.join(cdir, str(feeder_id))
open(os.path.join(fd, "feeder"), "w").write(open(FEEDER).read())
open(os.path.join(fd, "startup"), "w").write('shell.run("feeder")\n')
lua = '{side="west",free={' + ",".join(str(s) for s in range(108 - FREE + 1, 109)) + '},list={' + \
      ",".join('{%d,%d,%d,%d,"%s"}' % e for e in cfg_list) + "}}"
open(os.path.join(fd, "supply.cfg"), "w").write(lua)

turtles = []
for n in range(1, NT + 1):
    tid = ids[n]
    T = (DEPOT_X0 + STEP * (n - 1), OY, DEPOT_Z)
    blocks[T] = (1230, 0)
    tes[T] = nbt.C(id=nbt.STR("turtleadv"), x=nbt.I(T[0]), y=nbt.I(T[1]), z=nbt.I(T[2]), computerID=nbt.I(tid),
                   label=nbt.STR("city%d" % n), on=nbt.B(1), fuelLevel=nbt.I(3000), dir=nbt.I(2), selectedSlot=nbt.I(0),
                   leftUpgrade=nbt.I(181), rightUpgrade=nbt.I(5),
                   Items=nbt.LIST(nbt.TAG_COMPOUND, [nbt.C(Slot=nbt.B(15), id=nbt.S(801), Count=nbt.B(1), Damage=nbt.S(FREQ))]))
    td = os.path.join(cdir, str(tid))
    open(os.path.join(td, "cityfleet"), "w").write(open(CITYFLEET).read())
    open(os.path.join(td, "startup"), "w").write(
        'if fs.exists("fleet.state") then shell.run("cityfleet") else shell.run("cityfleet", "%d", "%s", "go") end\n' % (n, SERVER))
    turtles.append((n, tid, T))

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
print("feeder computer %d, supply stacks %d, Ender Chest %s freq %d, loader %s" % (feeder_id, len(entries), E, FREQ, L))
for n, tid, T in turtles:
    print("turtle city%d = computer %d at %s" % (n, tid, T))
