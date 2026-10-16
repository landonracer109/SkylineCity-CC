"""citysrv: hands out Skyline City jobs to cityfleet turtles and plans their flights.

Plain-text answers so CC 1.63 can read them line by line. Only the standard library.
  GET  /city/hello?t=<label>&n=<slot>       -> "home x y z"  (the turtle's depot spot)
  GET  /city/next?t=<label>&x=&y=&z=        -> a job + route, or "wait <s> <why>", or "home ..." + route, or "none",
                                               or "stop <why>" (that turtle never said hello to this build)
  GET  /city/plan/<job id>                  -> the job's build file
  GET  /city/done?t=<label>&job=<id>        -> "ok"
  POST /city/report?t=<label>               body: "key value" lines (job, cell, total, x, y, z, fuel, msg, need)
  GET  /city/needs                          -> what each turtle's Ender Chest should hold next
                                               ("t chest <frequency>" and "t id:dmg n" lines)
  GET  /city/                              -> progress page (city.html, next to this file)
  GET  /city/status.json                    -> everything the page shows, as JSON
  GET  /city/materials                      -> every block the city uses ("id:dmg" lines)
  POST /city/stock                          body: "id:dmg n" lines, AE's counts (from citysupply);
                                               jobs needing blocks AE is short of are held back
  POST /city/release  (X-Key)  body "v1.11 <commit>" -> turtles on another version update themselves
                                               (reports are answered "ok\nupdate v1.11 <commit>")
  POST /city/pause, /city/resume  (X-Key)   -> every turtle stops at a safe point / carries on
                                               (reports are answered "pause" while paused)
  POST /city/reset  (X-Key)                 -> forget all progress (for tests)
  POST /city/restart  (X-Key)  body [minutes] -> the Restart turtles button: every turtle has to start again
                                               from its depot spot (claims dropped, finished jobs kept). For
                                               that many minutes (default 30) a turtle that boots, or waits at
                                               home, gets "fresh" from /boot and starts over by itself
  GET  /city/boot?t=<label>                 -> "fresh" (start over from my depot spot) or "go" (carry on)
A turtle that never said hello to the build the server has now (left from an earlier build, or from
before Restart turtles) gets "pause" to its reports, plus "restart" while a restart is on.
  GET  /city/check?t=<label>&x=<x>          -> "check <steps>" (start the position check), "wait <s> <why>",
                                               or "none" (no check configured)
  GET  /city/check?t=<label>&x=&z=&e=       -> "ok" or "bad <why>": what the turtle found flying north
                                               from its depot spot (e = down / front / none, z where)
Coordinates on the wire are world coordinates. Inside, columns are city-local (origin = NW corner).

  POST /city/zones   (X-Key)  body "east north" -> the zones (of a zoned build) turtles may work in, in order

A zoned build (Skyline Metro: jobs.json with "zones", "lanes" and "topAbs"): every job has a zone, and
only zones that are open get turtles (open the next one once its chunks are loaded); a turtle gets
work from the first open zone that has a job ready, so nobody idles while another open zone has work.
Jobs list their own docks (checked by the exporter), the jobs they wait for ("after"), the jobs never
to be built at the same time ("excl": their turtles' paths cross) and "prio" (blocks on the longest
chain of jobs that wait on it: those go first). The lanes are given as world heights above everything
the build places; columns taller than the lowest lane (downtown's towers) are flown round. Routes are
straight runs: an L if one is clear, else a search that avoids turns.
Depot: DEPOT_ROW slots per row (0 = one row), rows DEPOT_ROWSTEP blocks apart in z.

Position check (for a repair of a pasted city, where a turtle that is a block off would dig the paste):
env CHECK='{"y": 63, "rows": [[x0, x1, lastZ], ...]}' = the ground layer's y and, for x0..x1, the last z
the paste reaches. On a first start a turtle flies north from its depot spot at home height, digging
nothing, until there's ground under it, a block in front of it, or it has gone `steps`; this server
says whether that is where it should have happened. It knows the paste edge and the blocks of finished
jobs; while a job in front of the turtle is being built it says wait, and while a turtle checks, no
job in front of it is handed out.
"""

import heapq
import json
import re
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8097"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
JOBS_DIR = os.environ.get("JOBS_DIR", "/jobs")
KEY = os.environ.get("CITY_KEY", "")
# Travel lanes, in blocks above building ground (city y 0): one height per direction so turtles
# going opposite ways or crossing never meet. Columns whose finished height is above LANE_LOW can't
# be flown over.
LANES = {"+x": 12, "-x": 13, "+z": 14, "-z": 15}
LANE_LOW, LANE_HIGH = 12, 15
WAIT_ABOVE = LANE_HIGH + 1      # a turtle that has to wait gets out of the lanes first
DEPOT_Z = int(os.environ.get("DEPOT_Z", "-200"))   # world z of the depot row
DEPOT_X0 = int(os.environ.get("DEPOT_X0", "-858"))  # world x of slot 1
DEPOT_STEP = int(os.environ.get("DEPOT_STEP", "8"))
DEPOT_ROW = int(os.environ.get("DEPOT_ROW", "0"))           # slots per row (0: all in one row)
DEPOT_ROWSTEP = int(os.environ.get("DEPOT_ROWSTEP", "8"))   # z from one row to the next
# The flying area beyond the city: a margin round it, and the depot strip along the south edge.
MARGIN, SOUTH = 2, 24
CHECK = json.loads(os.environ["CHECK"]) if os.environ.get("CHECK") else None   # the position check (see above)
CHECK_HOLD = 900    # seconds a position check keeps jobs in front of the turtle from being handed out

lock = threading.Lock()


class City:
    def __init__(self):
        with open(os.path.join(JOBS_DIR, "jobs.json")) as f:
            J = json.load(f)
        self.O = J["origin"]
        self.spanX, self.spanZ = J["span"]
        self.jobs = {j["id"]: j for j in J["jobs"]}
        self.order = [j["id"] for j in J["jobs"]]
        top = [[int(v, 36) for v in row.split(",")] for row in J["colTop"]]
        # zoned build (the metro): world heights for the lanes, column tops as world y + 1
        self.metro = bool(J.get("topAbs"))
        if self.metro:
            L = J["lanes"]
            self.laneY = {k: L[k] for k in ("+x", "-x", "+z", "-z")}
            self.waitY, self.flyMax = L["wait"], min(self.laneY.values())
            south = MARGIN
        else:
            self.laneY = {k: self.O[1] + v for k, v in LANES.items()}
            self.waitY, self.flyMax = self.O[1] + WAIT_ABOVE, LANE_LOW
            south = SOUTH
        self.zones = [z["key"] for z in J.get("zones", [])]
        self.zoneNames = {z["key"]: z["name"] for z in J.get("zones", [])}
        # the flying grid: x -MARGIN..spanX-1+MARGIN, z -MARGIN..spanZ-1+south
        self.gx0, self.gz0 = -MARGIN, -MARGIN
        self.gw, self.gd = self.spanX + 2 * MARGIN, self.spanZ + MARGIN + south
        self.top = bytearray(self.gw * self.gd)      # 0 outside the city
        for z in range(self.spanZ):
            for x in range(self.spanX):
                self.top[self.idx(x, z)] = min(255, top[z][x])
        # readable block names, from the build files ("id:dmg" -> name), for the web page
        self.names = {}
        if J.get("names"):
            used = {k for j in J["jobs"] for k in j["need"]}
            self.names = {k: J["names"].get(k, k) for k in used}
        for j in ([] if self.names else J["jobs"]):
            with open(os.path.join(JOBS_DIR, j["file"])) as f:
                for line in f:
                    if line.startswith("mat "):
                        p = line.split(None, 4)
                        self.names["%s:%s" % (p[2], p[3])] = p[4].strip()
                    elif line.startswith("L "):
                        break
        self.samples = []      # (time, blocks placed) once a minute, for the build rate
        # for the position check: each job's blocks in the ground layer and the one above it, by column
        self.checking = {}     # label -> (world x, since): turtles doing their position check
        self.low = {}          # (world x, z) -> [(y, job)]
        if CHECK:
            gy = CHECK["y"]
            for j in J["jobs"]:
                for x, y, z in self.cells(j):
                    if y in (gy, gy + 1):
                        self.low.setdefault((x, z), []).append((y, j["id"]))
        self.load()

    @staticmethod
    def cells(j):
        """A job's blocks, in world coordinates."""
        y = None
        with open(os.path.join(JOBS_DIR, j["file"])) as f:
            for line in f:
                p = line.split()
                if len(p) == 2 and p[0] == "L":
                    y = int(p[1])
                elif y is not None and len(p) == 3:
                    yield j["at"][0] + int(p[0]), j["at"][1] + y, j["at"][2] + int(p[1])

    # ---- grid helpers (city-local columns) ----
    def idx(self, x, z):
        return (z - self.gz0) * self.gw + (x - self.gx0)

    def inside(self, x, z):
        return self.gx0 <= x < self.gx0 + self.gw and self.gz0 <= z < self.gz0 + self.gd

    def flyable(self, x, z):
        return self.inside(x, z) and self.top[self.idx(x, z)] <= self.flyMax

    def empty(self, x, z):
        return self.inside(x, z) and self.top[self.idx(x, z)] == 0

    def local(self, wx, wz):
        return wx - self.O[0], wz - self.O[2]

    def world(self, x, z):
        return x + self.O[0], z + self.O[2]

    # ---- saved state ----
    def path(self):
        return os.path.join(DATA_DIR, "city-state.json")

    def load(self):
        try:
            with open(self.path()) as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        self.done = set(s.get("done", []))
        self.claims = s.get("claims", {})        # label -> {job, dock: [x, z] local}
        self.homes = s.get("homes", {})          # label -> [x, y, z] world
        self.reports = s.get("reports", {})      # label -> last report (dict)
        self.doneAt = s.get("doneAt", {})        # job -> unix time
        self.paused = s.get("paused", False)     # Pause button: every turtle stops at a safe point
        self.stock = s.get("stock", {})          # "id:dmg" -> how many AE has (from the supply computer)
        self.stockAt = s.get("stockAt", 0)
        self.release = s.get("release")          # {"v": "v1.11", "commit": sha}: turtles update to it
        self.restartUntil = s.get("restartUntil", 0)   # Restart turtles button: fresh starts allowed until then
        dflt = [z for z in os.environ.get("ZONES", "").replace(",", " ").split() if z in self.zones] or self.zones[:1]
        self.zonesOpen = [z for z in s.get("zonesOpen", dflt) if z in self.zones]   # zoned build: where turtles may work

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = self.path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"done": sorted(self.done), "claims": self.claims, "homes": self.homes,
                       "reports": self.reports, "doneAt": self.doneAt, "paused": self.paused,
                       "stock": self.stock, "stockAt": self.stockAt, "release": self.release,
                       "zonesOpen": self.zonesOpen, "restartUntil": self.restartUntil}, f)
        os.replace(tmp, self.path())

    # ---- routing ----
    def bfs(self, sx, sz):
        """Distances (and parents) over flyable columns from (sx, sz). The start may be unflyable."""
        dist = {(sx, sz): 0}
        par = {}
        q = deque([(sx, sz)])
        while q:
            x, z = q.popleft()
            d = dist[(x, z)] + 1
            for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if (nx, nz) not in dist and self.flyable(nx, nz):
                    dist[(nx, nz)] = d
                    par[(nx, nz)] = (x, z)
                    q.append((nx, nz))
        return dist, par

    @staticmethod
    def corners(path):
        """Straight runs of a column path -> the columns where it turns, plus the end."""
        out = []
        for i in range(1, len(path)):
            if i == len(path) - 1:
                out.append(path[i])
                break
            a, b, c = path[i - 1], path[i], path[i + 1]
            if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
                out.append(b)
        return out

    def route_to(self, par, sx, sz, tx, tz):
        path = [(tx, tz)]
        while path[-1] != (sx, sz):
            path.append(par[path[-1]])
        path.reverse()
        return self.corners(path)

    def clear_run(self, x0, z0, x1, z1, xfirst=True):
        """Every column from (x0, z0) to (x1, z1), along x then z (or z then x), the start left out,
        can be flown over."""
        x, z = x0, z0
        for axis in ("xz" if xfirst else "zx"):
            if axis == "x":
                while x != x1:
                    x += 1 if x1 > x else -1
                    if not self.flyable(x, z):
                        return False
            else:
                while z != z1:
                    z += 1 if z1 > z else -1
                    if not self.flyable(x, z):
                        return False
        return True

    def find_route(self, sx, sz, tx, tz, turn=12, limit=600000):
        """Corner columns of a way over flyable columns: an L if one is clear, else the cheapest path
        with a cost for every turn (each turn is a change of lane). None if there is none."""
        if (sx, sz) == (tx, tz):
            return []
        straight = sx == tx or sz == tz
        if self.clear_run(sx, sz, tx, tz, True):
            return [(tx, tz)] if straight else [(tx, sz), (tx, tz)]
        if not straight and self.clear_run(sx, sz, tx, tz, False):
            return [(sx, tz), (tx, tz)]
        if not self.flyable(tx, tz):
            return None
        D = ((1, 0), (0, 1), (-1, 0), (0, -1))
        best, par, q = {}, {}, []
        for d in range(4):
            best[(sx, sz, d)] = 0
            heapq.heappush(q, (abs(sx - tx) + abs(sz - tz), 0, sx, sz, d))
        n, end = 0, None
        while q and n < limit:
            f, g, x, z, d = heapq.heappop(q)
            if g > best.get((x, z, d), 1 << 30):
                continue
            if (x, z) == (tx, tz):
                end = (x, z, d)
                break
            n += 1
            for nd in range(4):
                if nd == (d + 2) % 4:
                    continue
                nx, nz = x + D[nd][0], z + D[nd][1]
                if not self.flyable(nx, nz):
                    continue
                ng = g + 1 + (turn if nd != d and (x, z) != (sx, sz) else 0)
                k = (nx, nz, nd)
                if ng < best.get(k, 1 << 30):
                    best[k], par[k] = ng, (x, z, d)
                    heapq.heappush(q, (ng + abs(nx - tx) + abs(nz - tz), ng, nx, nz, nd))
        if not end:
            return None
        path = [end]
        while (path[-1][0], path[-1][1]) != (sx, sz):
            path.append(par[path[-1]])
        return self.corners([(x, z) for x, z, _ in reversed(path)])

    def ways(self, sx, sz):
        """(distance to a column, or None if it can't be reached; route to a column, or None) from (sx, sz)."""
        if self.metro:
            return (lambda c: abs(c[0] - sx) + abs(c[1] - sz)), (lambda c: self.find_route(sx, sz, *c))
        dist, par = self.bfs(sx, sz)
        return dist.get, (lambda c: (self.route_to(par, sx, sz, *c) if c != (sx, sz) else []) if c in dist else None)

    def box(self, j):
        x0, z0 = self.local(j["at"][0], j["at"][2])
        return x0, z0, x0 + j["size"][0] - 1, z0 + j["size"][2] - 1

    def docks(self, j):
        """Empty columns next to the job's box (not corners), with the box column each one faces."""
        if "docks" in j:
            taken = {tuple(c["dock"]) for c in self.claims.values() if c.get("dock")}
            homes = {self.local(h[0], h[2]) for h in self.homes.values()}
            return [((dx, dz), (nx, nz)) for dx, dz, nx, nz in j["docks"] if (dx, dz) not in taken and (dx, dz) not in homes]
        x0, z0, x1, z1 = self.box(j)
        out = []
        for x in range(x0, x1 + 1):
            out.append(((x, z0 - 1), (x, z0)))
            out.append(((x, z1 + 1), (x, z1)))
        for z in range(z0, z1 + 1):
            out.append(((x0 - 1, z), (x0, z)))
            out.append(((x1 + 1, z), (x1, z)))
        taken = {tuple(c["dock"]) for c in self.claims.values() if c.get("dock")}
        homes = {self.local(h[0], h[2]) for h in self.homes.values()}
        return [(d, e) for d, e in out if self.empty(*d) and d not in taken and d not in homes]

    # ---- jobs ----
    def ready(self, jid):
        j = self.jobs[jid]
        return all(a in self.done for a in j.get("after", []))

    def reserved(self):
        """Blocks still to come out of AE for the jobs being built now (their unbuilt share)."""
        out = {}
        for label, c in self.claims.items():
            jid = c["job"]
            if jid in self.done:
                continue
            j, r = self.jobs[jid], self.reports.get(label, {})
            left = 1.0
            if r.get("job") == jid and j["blocks"]:
                left = max(0.0, 1 - (int(r.get("cell", 1)) - 1) / j["blocks"])
            for k, n in j["need"].items():
                out[k] = out.get(k, 0) + n * left
        return out

    def missing(self, jid, reserved=None):
        """Blocks this job needs that AE doesn't have enough of, after what the jobs under way still
        need (going by the supply computer's last count, if it is under an hour old; with no count,
        nothing counts as missing)."""
        if not self.stock or time.time() - self.stockAt > 3600:
            return []
        if reserved is None:
            reserved = self.reserved()
        return [k for k, n in self.jobs[jid]["need"].items() if k in self.stock and self.stock[k] - reserved.get(k, 0) < n]

    def zone_ok(self, j):
        return not self.zones or j.get("zone") in self.zonesOpen

    def blocked(self, j, held):
        """A job whose turtle's path crosses one being built now waits."""
        return any(e in held and e not in self.done for e in j.get("excl", ()))

    def pick(self, label, sx, sz, dist):
        """The job to give this turtle: platform tiles first (everything waits on them), then street
        tiles (towers wait on them), then the biggest ready building, so the last hours aren't one
        turtle finishing a giant alone. Nearest breaks ties. Jobs needing blocks AE is short of wait.
        Water needs a bucket turtle, so cityfleet never gets it."""
        held = {c["job"] for c in self.claims.values()}
        res = self.reserved()
        best, bestKey = None, None
        for jid in self.order:
            j = self.jobs[jid]
            if jid in self.done or jid in held or j["kind"] == "water" or not self.ready(jid) or self.missing(jid, res):
                continue
            if self.in_check_strip(j) or not self.zone_ok(j) or self.blocked(j, held):
                continue
            ds = [x for x in (dist(d) for d, _ in self.docks(j)) if x is not None]
            if not ds:
                continue
            if self.zones:
                # the first open zone with work, then the longest chain of waiting jobs, then the nearest
                key = (self.zonesOpen.index(j["zone"]), -j.get("prio", j["blocks"]), min(ds))
            else:
                rank = {"ground": 0, "street": 1}.get(j["kind"], 2)
                key = (rank, -j["blocks"] if rank == 2 else 0, min(ds))
            if bestKey is None or key < bestKey:
                best, bestKey = jid, key
        return best

    def lanes(self):
        L = self.laneY
        return "lanes %d %d %d %d %d" % (L["+x"], L["-x"], L["+z"], L["-z"], self.waitY)

    def lane_route(self, corners):
        return " ".join("%d,%d" % self.world(x, z) for x, z in corners)

    def next(self, label, wx, wy, wz):
        if self.paused:
            return "wait 20 paused"
        # Only turtles started for this build (they said hello to it) get work: one still running from
        # an earlier build is told to stop (cityfleet v1.13; older ones just wait).
        if label not in self.homes:
            return "stop This job server has a new build that I wasn't started for."
        sx, sz = self.local(wx, wz)
        dist, route_to = self.ways(sx, sz)
        c = self.claims.get(label)
        if c and c["job"] not in self.done:
            jid = c["job"]
            dock, enter = tuple(c["dock"]), tuple(c["enter"])
        else:
            if c:
                del self.claims[label]
            jid = self.pick(label, sx, sz, dist)
            if not jid:
                left = [k for k in self.order if k not in self.done and self.jobs[k]["kind"] != "water"]
                hint = ""
                if self.zones:
                    closed = [k for k in left if not self.zone_ok(self.jobs[k])]
                    left = [k for k in left if self.zone_ok(self.jobs[k])]
                    if closed:
                        nxt = next(z for z in self.zones if z not in self.zonesOpen and any(self.jobs[k]["zone"] == z for k in closed))
                        hint = "; open the next zone (%s) once its chunks are loaded" % self.zoneNames.get(nxt, nxt)
                        if not left:
                            return "wait 60 the open zones are done" + hint
                if left:
                    busy = sum(1 for k in left if k in {c["job"] for c in self.claims.values()})
                    res = self.reserved()
                    short = sorted({k for jid in left if jid not in {c["job"] for c in self.claims.values()} for k in self.missing(jid, res)})
                    if short:
                        return "wait 60 %d jobs left, %d being built; the rest wait for blocks AE is short of: %s" % (
                            len(left), busy, ", ".join(self.names.get(k, k) for k in short))
                    return "wait 30 %d jobs left, %d being built, the rest wait for them%s" % (len(left), busy, hint)
                home = self.homes.get(label)
                if not home:
                    return "none"
                hx, hz = self.local(home[0], home[2])
                if (sx, sz) == (hx, hz):
                    return "none"
                r = route_to((hx, hz))
                if r is None:
                    return "wait 60 no way home from here"
                return "home %d %d %d\n%s\nroute %s\nend" % (home[0], home[1], home[2], self.lanes(), self.lane_route(r))
            j = self.jobs[jid]
            dock, enter = min(((d, e) for d, e in self.docks(j) if dist(d) is not None), key=lambda de: dist(de[0]))
            self.claims[label] = {"job": jid, "dock": list(dock), "enter": list(enter), "since": time.time()}
            self.save()
        j = self.jobs[jid]
        route = route_to(dock)
        if route is None:
            return "wait 60 no way to %s from here" % jid
        dwx, dwz = self.world(*dock)
        ewx, ewz = self.world(*enter)
        return "\n".join([
            "job %s %s %d %s" % (jid, j["kind"], j["blocks"], j["name"]),
            "at %d %d %d" % tuple(j["at"]),
            "dock %d %d" % (dwx, dwz),
            "enter %d %d" % (ewx, ewz),
            self.lanes(),
            "route %s" % self.lane_route(route),
            "end"])

    # ---- the position check ----
    @staticmethod
    def last_z(wx):
        for x0, x1, z in CHECK["rows"]:
            if x0 <= wx <= x1:
                return z
        return None

    def strip(self, label, wx):
        """The columns a checking turtle flies over (world x; z from the paste edge to its spot)."""
        home, lz = self.homes.get(label), self.last_z(wx)
        if not home or lz is None:
            return None
        return wx, lz + 1, home[2]

    def hits(self, j, strip):
        x0, z0, x1, z1 = self.box(j)
        lx, lz0 = self.local(strip[0], strip[1])
        _, lz1 = self.local(strip[0], strip[2])
        return x0 <= lx <= x1 and z0 <= lz1 and z1 >= lz0

    def in_check_strip(self, j):
        now = time.time()
        for label, (wx, since) in list(self.checking.items()):
            if now - since > CHECK_HOLD:
                del self.checking[label]
                continue
            s = self.strip(label, wx)
            if s and self.hits(j, s):
                return True
        return False

    def check_start(self, label, wx):
        if not CHECK:
            return "none"
        home, s = self.homes.get(label), self.strip(label, wx)
        if not home or not s or home[0] != wx:
            return "wait 300 no position check for x %d (say hello first; is CHECK right?)" % wx
        if home[1] != CHECK["y"] + 1:
            return "wait 300 the depot is at y %d but CHECK says the ground is at y %d" % (home[1], CHECK["y"])
        for c in self.claims.values():
            if c["job"] not in self.done and self.hits(self.jobs[c["job"]], s):
                return "wait 30 %s is being built in front of me; I'll check my position after that" % c["job"]
        self.checking[label] = (wx, time.time())
        return "check %d" % (home[2] - self.last_z(wx) + 2)

    def expected(self, label, wx):
        """What a turtle in the right spot finds flying north: ("down"|"front"|"none", z)."""
        hx, hy, hz = self.homes[label]
        lz = self.last_z(wx)
        steps = hz - lz + 2

        def solid(y, z):
            if z <= lz:
                return y == hy - 1      # the paste's ground layer (the air right above its edge is clear)
            return any(yy == y and jid in self.done for yy, jid in self.low.get((wx, z), ()))
        z, n = hz, 0
        while True:
            if n > 0 and solid(hy - 1, z):
                return "down", z
            if n >= steps:
                return "none", z
            if solid(hy, z - 1):
                return "front", z
            z, n = z - 1, n + 1

    def check_result(self, label, wx, wz, e):
        self.checking.pop(label, None)
        if not CHECK or not self.strip(label, wx):
            return "bad no position check is set up for me"
        want = self.expected(label, wx)
        if (e, wz) == want:
            return "ok"
        words = {"down": "ground under me at z %d", "front": "a block in front of me at z %d",
                 "none": "nothing by z %d"}
        return "bad I found %s, not %s." % (words.get(e, e + " at z %d") % wz, words[want[0]] % want[1])

    def finish(self, label, jid):
        c = self.claims.get(label)
        if jid in self.jobs:
            self.done.add(jid)
            self.doneAt[jid] = time.time()
        if c and c["job"] == jid:
            # keep the dock reserved: the turtle is still in it until it flies on
            c["finished"] = True
        self.save()

    def status(self):
        kinds = {}
        for jid in self.order:
            k = self.jobs[jid]["kind"]
            e = kinds.setdefault(k, {"jobs": 0, "done": 0, "blocks": 0, "placed": 0})
            e["jobs"] += 1
            e["blocks"] += self.jobs[jid]["blocks"]
            if jid in self.done:
                e["done"] += 1
                e["placed"] += self.jobs[jid]["blocks"]
        placing = {}
        for label, r in self.reports.items():
            c = self.claims.get(label)
            if c and c["job"] not in self.done and r.get("job") == c["job"]:
                placing[c["job"]] = max(0, int(r.get("cell", 1)) - 1)
                kinds[self.jobs[c["job"]]["kind"]]["placed"] += placing[c["job"]]
        now = time.time()
        reports = {k: dict(r, age=int(now - r.get("at", now))) for k, r in self.reports.items()}
        # each job: id, kind, name, box (city-local x0 z0 x1 z1), blocks, height, state, placed
        building = {c["job"]: label for label, c in self.claims.items() if c["job"] not in self.done}
        res = self.reserved()
        jobs = []
        for jid in self.order:
            j = self.jobs[jid]
            if jid in self.done:
                state, placed = "done", j["blocks"]
            elif jid in building:
                r = self.reports.get(building[jid], {})
                state, placed = "building", max(0, int(r.get("cell", 1)) - 1) if r.get("job") == jid else 0
            elif self.missing(jid, res):
                state, placed = "nostock", 0
            else:
                state, placed = ("ready" if self.ready(jid) else "waiting"), 0
            jobs.append([jid, j["kind"], j["name"], list(self.box(j)), j["blocks"], j["size"][1], state, placed, building.get(jid),
                         [self.names.get(k, k) for k in self.missing(jid, res)] if state == "nostock" else []])
        placed = sum(k["placed"] for k in kinds.values())
        if not self.samples or now - self.samples[-1][0] >= 60:
            self.samples.append((now, placed))
            self.samples = [p for p in self.samples if now - p[0] <= 600]
        old = self.samples[0]
        rate = (placed - old[1]) / (now - old[0]) * 3600 if now - old[0] >= 120 else None
        zones = []
        for z in self.zones:
            ids = [k for k in self.order if self.jobs[k].get("zone") == z]
            zones.append({"key": z, "name": self.zoneNames.get(z, z), "open": z in self.zonesOpen, "jobs": len(ids),
                          "done": sum(1 for k in ids if k in self.done), "blocks": sum(self.jobs[k]["blocks"] for k in ids),
                          "placed": sum(self.jobs[k]["blocks"] for k in ids if k in self.done) +
                          sum(p for k, p in placing.items() if self.jobs[k].get("zone") == z)})
        return {"kinds": kinds, "claims": self.claims, "reports": reports, "lanes": self.laneY, "zones": zones,
                "doneCount": len(self.done), "jobCount": len(self.order), "jobs": jobs, "names": self.names,
                "origin": self.O, "span": [self.spanX, self.spanZ], "placed": placed, "rate": rate,
                "paused": self.paused, "release": self.release, "restartLeft": max(0, int(self.restartUntil - now)), "stockAge": int(now - self.stockAt) if self.stockAt else None}


city = City()


def qs(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class H(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        q = qs(self.path)
        try:
            with lock:
                if path == "/city/hello":
                    label, n = q["t"], int(q["n"])
                    row, col = divmod(n - 1, DEPOT_ROW) if DEPOT_ROW else (0, n - 1)
                    home = [DEPOT_X0 + DEPOT_STEP * col, city.O[1], DEPOT_Z + DEPOT_ROWSTEP * row]
                    city.homes[label] = home
                    city.save()
                    return self.reply(200, "home %d %d %d\n" % tuple(home))
                if path == "/city/check":
                    if "e" in q:
                        return self.reply(200, city.check_result(q["t"], int(q["x"]), int(q["z"]), q["e"]) + "\n")
                    return self.reply(200, city.check_start(q["t"], int(q["x"])) + "\n")
                if path == "/city/boot":
                    # a turtle starting up: start over from its depot spot if a restart is on and it hasn't yet
                    fresh = q["t"] not in city.homes and time.time() < city.restartUntil
                    return self.reply(200, "fresh\n" if fresh else "go\n")
                if path == "/city/next":
                    return self.reply(200, city.next(q["t"], int(q["x"]), int(q["y"]), int(q["z"])) + "\n")
                if path.startswith("/city/plan/"):
                    jid = path.rsplit("/", 1)[1]
                    if jid not in city.jobs:
                        return self.reply(200, "nojob\n")
                    with open(os.path.join(JOBS_DIR, city.jobs[jid]["file"]), "rb") as f:
                        return self.reply(200, f.read())
                if path == "/city/done":
                    city.finish(q["t"], q["job"])
                    return self.reply(200, "ok\n")
                if path == "/city/needs":
                    # per turtle: "t chest <frequency>", then "t <id:dmg> <n>" for each block it needs next
                    lines = []
                    for label, r in sorted(city.reports.items()):
                        ch = (r.get("chest") or "").partition(":")[2]
                        if ch:
                            lines.append("%s chest %s" % (label, ch))
                        for item in (r.get("need") or "").split():
                            k, _, n = item.partition("=")
                            lines.append("%s %s %s" % (label, k, n))
                    return self.reply(200, "\n".join(lines) + "\n")
                if path == "/city/materials":
                    # every block the city uses ("id:dmg" per line), for the supply computer's counts
                    return self.reply(200, "\n".join(sorted(k for k in city.names if k != "326:0")) + "\n")
                if path == "/city/status.json":
                    return self.reply(200, json.dumps(city.status()), "application/json")
                if path == "/city":
                    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "city.html"), "rb") as f:
                        return self.reply(200, f.read(), "text/html; charset=utf-8")
        except (KeyError, ValueError) as e:
            return self.reply(200, "error bad request %s\n" % e)
        self.reply(404, "not found\n")

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(n, 16384)).decode("utf-8", "replace")
        q = qs(self.path)
        with lock:
            if path == "/city/report":
                r = {}
                for line in body.splitlines():
                    k, _, v = line.partition(" ")
                    if k:
                        r[k] = v
                r["at"] = time.time()
                label = q.get("t", "?")
                city.reports[label] = r
                city.save()
                ans = "pause\n" if city.paused else "ok\n"    # turtles stop while paused
                if label not in city.homes and label != "supply":
                    # not started for this build: stay put (and start over, if at home, while a restart is on)
                    ans = "pause\n" + ("restart\n" if time.time() < city.restartUntil else "")
                rel = city.release
                if rel and r.get("version") != rel["v"]:
                    ans += "update %s %s\n" % (rel["v"], rel["commit"])
                return self.reply(200, ans)
            if path == "/city/stock":
                # the supply computer's AE counts: "id:dmg n" per line
                for line in body.splitlines():
                    k, _, n = line.partition(" ")
                    if k and n.strip().isdigit():
                        city.stock[k] = int(n)
                city.stockAt = time.time()
                city.save()
                return self.reply(200, "ok\n")
            if path == "/city/release":
                if not KEY or self.headers.get("X-Key") != KEY:
                    return self.reply(403, "wrong password\n")
                parts = body.split()
                if len(parts) != 2 or not re.fullmatch(r"v[\d.]+", parts[0]) or not re.fullmatch(r"[0-9a-f]{40}", parts[1]):
                    return self.reply(400, "body: v1.11 <40-char commit>\n")
                city.release = {"v": parts[0], "commit": parts[1]}
                city.save()
                return self.reply(200, "release %s %s\n" % (parts[0], parts[1]))
            if path == "/city/zones":
                if not KEY or self.headers.get("X-Key") != KEY:
                    return self.reply(403, "wrong password\n")
                want = body.replace(",", " ").split()
                bad = [z for z in want if z not in city.zones]
                if bad or not want:
                    return self.reply(400, "zones: %s\n" % " ".join(city.zones))
                city.zonesOpen = want
                city.save()
                return self.reply(200, "open %s\n" % " ".join(want))
            if path in ("/city/pause", "/city/resume"):
                if not KEY or self.headers.get("X-Key") != KEY:
                    return self.reply(403, "wrong password\n")
                city.paused = path == "/city/pause"
                city.save()
                return self.reply(200, ("paused" if city.paused else "running") + "\n")
            if path == "/city/restart":
                if not KEY or self.headers.get("X-Key") != KEY:
                    return self.reply(403, "wrong password\n")
                mins = int(body.strip()) if body.strip().isdigit() else 30
                city.homes, city.claims, city.paused = {}, {}, False
                city.restartUntil = time.time() + 60 * mins
                city.save()
                return self.reply(200, "restart %d\n" % mins)
            if path == "/city/reset":
                if not KEY or self.headers.get("X-Key") != KEY:
                    return self.reply(403, "no\n")
                city.done, city.claims, city.reports, city.doneAt = set(), {}, {}, {}
                city.paused, city.stock, city.stockAt = False, {}, 0
                for jid in (body.split() if body.strip() else []):     # optional: jobs to mark done
                    city.done.add(jid)
                city.save()
                return self.reply(200, "ok\n")
        self.reply(404, "not found\n")

    def log_message(self, fmt, *args):
        if os.environ.get("LOG_REQUESTS") == "1":
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print("citysrv on port %d, %d jobs, %d done" % (PORT, len(city.order), len(city.done)), flush=True)
    ThreadingHTTPServer(("", PORT), H).serve_forever()
