"""citysrv: hands out Skyline City jobs to cityfleet turtles and plans their flights.

Plain-text answers so CC 1.63 can read them line by line. Only the standard library.
  GET  /city/hello?t=<label>&n=<slot>       -> "home x y z"  (the turtle's depot spot)
  GET  /city/next?t=<label>&x=&y=&z=        -> a job + route, or "wait <s> <why>", or "home ..." + route, or "none"
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
Coordinates on the wire are world coordinates. Inside, columns are city-local (origin = NW corner).
"""

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
# The flying area beyond the city: a margin round it, and the depot strip along the south edge.
MARGIN, SOUTH = 2, 24

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
        # the flying grid: x -MARGIN..spanX-1+MARGIN, z -MARGIN..spanZ-1+SOUTH
        self.gx0, self.gz0 = -MARGIN, -MARGIN
        self.gw, self.gd = self.spanX + 2 * MARGIN, self.spanZ + MARGIN + SOUTH
        self.top = bytearray(self.gw * self.gd)      # 0 outside the city
        for z in range(self.spanZ):
            for x in range(self.spanX):
                self.top[self.idx(x, z)] = min(255, top[z][x])
        # readable block names, from the build files ("id:dmg" -> name), for the web page
        self.names = {}
        for j in J["jobs"]:
            with open(os.path.join(JOBS_DIR, j["file"])) as f:
                for line in f:
                    if line.startswith("mat "):
                        p = line.split(None, 4)
                        self.names["%s:%s" % (p[2], p[3])] = p[4].strip()
                    elif line.startswith("L "):
                        break
        self.samples = []      # (time, blocks placed) once a minute, for the build rate
        self.load()

    # ---- grid helpers (city-local columns) ----
    def idx(self, x, z):
        return (z - self.gz0) * self.gw + (x - self.gx0)

    def inside(self, x, z):
        return self.gx0 <= x < self.gx0 + self.gw and self.gz0 <= z < self.gz0 + self.gd

    def flyable(self, x, z):
        return self.inside(x, z) and self.top[self.idx(x, z)] <= LANE_LOW

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

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = self.path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"done": sorted(self.done), "claims": self.claims, "homes": self.homes,
                       "reports": self.reports, "doneAt": self.doneAt, "paused": self.paused,
                       "stock": self.stock, "stockAt": self.stockAt, "release": self.release}, f)
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

    def box(self, j):
        x0, z0 = self.local(j["at"][0], j["at"][2])
        return x0, z0, x0 + j["size"][0] - 1, z0 + j["size"][2] - 1

    def docks(self, j):
        """Empty columns next to the job's box (not corners), with the box column each one faces."""
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
            ds = [dist[d] for d, _ in self.docks(j) if d in dist]
            if not ds:
                continue
            rank = {"ground": 0, "street": 1}.get(j["kind"], 2)
            key = (rank, -j["blocks"] if rank == 2 else 0, min(ds))
            if bestKey is None or key < bestKey:
                best, bestKey = jid, key
        return best

    def lanes(self):
        y = self.O[1]
        return "lanes %d %d %d %d %d" % (y + LANES["+x"], y + LANES["-x"], y + LANES["+z"], y + LANES["-z"], y + WAIT_ABOVE)

    def lane_route(self, corners):
        return " ".join("%d,%d" % self.world(x, z) for x, z in corners)

    def next(self, label, wx, wy, wz):
        if self.paused:
            return "wait 20 paused"
        sx, sz = self.local(wx, wz)
        dist, par = self.bfs(sx, sz)
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
                if left:
                    busy = sum(1 for k in left if k in {c["job"] for c in self.claims.values()})
                    res = self.reserved()
                    short = sorted({k for jid in left if jid not in {c["job"] for c in self.claims.values()} for k in self.missing(jid, res)})
                    if short:
                        return "wait 60 %d jobs left, %d being built; the rest wait for blocks AE is short of: %s" % (
                            len(left), busy, ", ".join(self.names.get(k, k) for k in short))
                    return "wait 30 %d jobs left, %d being built, the rest wait for them" % (len(left), busy)
                home = self.homes.get(label)
                if not home:
                    return "none"
                hx, hz = self.local(home[0], home[2])
                if (sx, sz) == (hx, hz):
                    return "none"
                if (hx, hz) not in dist:
                    return "wait 60 no way home from here"
                return "home %d %d %d\n%s\nroute %s\nend" % (home[0], home[1], home[2], self.lanes(), self.lane_route(self.route_to(par, sx, sz, hx, hz)))
            j = self.jobs[jid]
            dock, enter = min(((d, e) for d, e in self.docks(j) if d in dist), key=lambda de: dist[de[0]])
            self.claims[label] = {"job": jid, "dock": list(dock), "enter": list(enter), "since": time.time()}
            self.save()
        j = self.jobs[jid]
        if dock not in dist:
            return "wait 60 no way to %s from here" % jid
        route = self.route_to(par, sx, sz, *dock) if dock != (sx, sz) else []
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
        for label, r in self.reports.items():
            c = self.claims.get(label)
            if c and c["job"] not in self.done and r.get("job") == c["job"]:
                kinds[self.jobs[c["job"]]["kind"]]["placed"] += max(0, int(r.get("cell", 1)) - 1)
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
        return {"kinds": kinds, "claims": self.claims, "reports": reports, "lanes": LANES,
                "doneCount": len(self.done), "jobCount": len(self.order), "jobs": jobs, "names": self.names,
                "origin": self.O, "span": [self.spanX, self.spanZ], "placed": placed, "rate": rate,
                "paused": self.paused, "release": self.release, "stockAge": int(now - self.stockAt) if self.stockAt else None}


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
                    home = [DEPOT_X0 + DEPOT_STEP * (n - 1), city.O[1], DEPOT_Z]
                    city.homes[label] = home
                    city.save()
                    return self.reply(200, "home %d %d %d\n" % tuple(home))
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
                city.reports[q.get("t", "?")] = r
                city.save()
                ans = "pause\n" if city.paused else "ok\n"    # turtles stop while paused
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
            if path in ("/city/pause", "/city/resume"):
                if not KEY or self.headers.get("X-Key") != KEY:
                    return self.reply(403, "wrong password\n")
                city.paused = path == "/city/pause"
                city.save()
                return self.reply(200, ("paused" if city.paused else "running") + "\n")
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
