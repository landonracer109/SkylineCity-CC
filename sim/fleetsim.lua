-- Simulates a fleet of CC 1.63 turtles running cityfleet against the real job server (citysrv.py).
--   SERVER=http://127.0.0.1:8097/city luajit fleetsim.lua <jobs dir> <turtles> [restarts] [seed] [hours]
-- One shared world: water below the platform, nothing else. Turtles block each other (a move into
-- another turtle fails, and it shows up as a "turtle" peripheral). Each turtle has its own
-- 27-slot Ender Chest that "AE" refills every 5 s with 2 stacks of each block of the turtle's
-- current build file plus coal blocks. The whole server restarts now and then: every turtle's
-- program is killed (sometimes just after a move whose answer never arrived) and started again
-- from startup. At the end every block of every job is checked.
-- Repairing a pasted city: PASTE_JOBS=<full-city jobs dir> PASTE_OFF="dx,dy,dz" CUT="x0,x1,lastZ;..."
-- pre-places every block of the full city north of the cut (shifted), plus a dock block under each
-- depot spot; none of them may be dug. DEPOT_X0/DEPOT_Z place the depot row (home y = the jobs'
-- origin y). OFFSET="n:dx,dy,dz;..." puts turtle n that far off its depot spot (it doesn't know):
-- its position check must stop it without digging anything. EARLY=n adds n server restarts in the
-- first 2 minutes (during the position checks). LATE="n:seconds;..." starts turtle n that late.
-- A zoned build (the metro): ONLY="e" checks only the jobs whose id starts with one of those letters;
-- PREDONE (job ids) are built before the start (blocks placed, marked done on the server);
-- ZONES_AUTO=1 opens the next zone (POST /zones) ZONE_DELAY s (default 300) after a turtle is first
-- told to wait for one. DEPOT_ROW / DEPOT_ROWSTEP: the depot in rows (like citysrv).
-- RESTART=<s>: the Restart turtles button is pressed then; 10 minutes later every turtle that switched
-- itself off is picked up (with its Ender Chest), put on its depot spot facing north and turned on.
-- RESTART_CRASH=1: the Minecraft server restarts 5 s after the button (turtles reboot where they are).
-- RESTART_DONE=1: when everything is built, reset the server and press Restart: the turtles at home
-- must build it all again by themselves.

local JOBS, N, RESTARTS, SEED, HOURS = arg[1], tonumber(arg[2] or 4), tonumber(arg[3] or 3), tonumber(arg[4] or 1), tonumber(arg[5] or 30)
local SERVER = os.getenv("SERVER") or "http://127.0.0.1:8097/city"
local KEY = os.getenv("CITY_KEY") or ""
local ONLY = os.getenv("ONLY")      -- e.g. "g" = only jobs whose id starts with g count for "finished"
-- PAUSE="from,to" (sim seconds): press Pause, then Resume. No turtle may move or place from 2 min
-- after Pause until Resume.
-- STOCKOUT="928:6,2522:0" STOCKBACK=<hours>: AE has none of those blocks until then; the sim sends
-- the stock counts every 5 min like citysupply. No turtle should sit waiting for them.
local PAUSE = os.getenv("PAUSE")
-- RELEASE="seconds,version,file": at that sim time announce a release; turtles "download" it from file
local relAt, relV, relFile = (os.getenv("RELEASE") or ""):match("^(%d+),([^,]+),(.+)$")
relAt = tonumber(relAt)
local STOCKOUT, STOCKBACK = {}, tonumber(os.getenv("STOCKBACK") or 0) * 3600
for k in (os.getenv("STOCKOUT") or ""):gmatch("[^,]+") do STOCKOUT[k] = true end
math.randomseed(SEED)
local DIR = os.getenv("SIMDIR") or "simfleet"
-- Windows (cmd) or Linux (sh). On Windows a fresh SIMDIR is needed per run (nothing is deleted).
local WIN = package.config:sub(1, 1) == "\\"
local function mkdir(p)
  if WIN then os.execute('mkdir "' .. p:gsub("/", "\\") .. '" 2>nul') else os.execute('mkdir -p "' .. p .. '"') end
end
if not WIN then os.execute('rm -rf "' .. DIR .. '"') end
mkdir(DIR)

-- ---------- http through curl ----------
local function curl(args)
  local p = io.popen("curl -s -m 30 " .. args)
  local s = p:read("*a"); p:close()
  return s
end
-- The turtles' requests: on Windows starting a curl process for each one is very slow, so there
-- they go straight over a socket (LuaJIT FFI, ws2_32). Returns the body, whatever the status (like curl -s).
local fastHttp
if WIN and jit then
  local ffi = require("ffi")
  ffi.cdef[[
    typedef struct { unsigned short family; unsigned short port; unsigned int addr; char zero[8]; } fs_sockaddr_in;
    int WSAStartup(unsigned short ver, void *data);
    uintptr_t socket(int af, int type, int proto);
    int connect(uintptr_t s, const fs_sockaddr_in *addr, int len);
    int send(uintptr_t s, const char *buf, int len, int flags);
    int recv(uintptr_t s, char *buf, int len, int flags);
    int closesocket(uintptr_t s);
    unsigned short htons(unsigned short v);
    unsigned long inet_addr(const char *cp);
  ]]
  local ws = ffi.load("ws2_32")
  ws.WSAStartup(0x0202, ffi.new("char[512]"))
  local buf = ffi.new("char[65536]")
  fastHttp = function(url, body)
    local host, port, path = url:match("^http://([%d%.]+):(%d+)(/.*)$")
    if not host then return nil end
    local s = ws.socket(2, 1, 6)
    local a = ffi.new("fs_sockaddr_in")
    a.family, a.port, a.addr = 2, ws.htons(tonumber(port)), ws.inet_addr(host)
    if ws.connect(s, a, ffi.sizeof(a)) ~= 0 then ws.closesocket(s); return nil end
    local req = (body and "POST " or "GET ") .. path .. " HTTP/1.0\r\nHost: " .. host .. "\r\nContent-Length: " .. #(body or "") .. "\r\n\r\n" .. (body or "")
    local sent = 0
    while sent < #req do
      local n = ws.send(s, req:sub(sent + 1), #req - sent, 0)
      if n <= 0 then ws.closesocket(s); return nil end
      sent = sent + n
    end
    local parts = {}
    while true do
      local n = ws.recv(s, buf, 65536, 0)
      if n <= 0 then break end
      parts[#parts + 1] = ffi.string(buf, n)
    end
    ws.closesocket(s)
    return (table.concat(parts):match("^HTTP/[%d%.]+ %d+.-\r\n\r\n(.*)$"))
  end
end
local function reset()
  local f = io.open(DIR .. "/body", "wb"); f:write(os.getenv("PREDONE") or ""); f:close()
  assert(curl(string.format('-X POST -H "X-Key: %s" --data-binary @%s/body %s/reset', KEY, DIR, SERVER)):match("ok"), "server reset failed")
end
reset()

-- ---------- what should be built (every job file) ----------
local function readPlan(file)
  local p = { mats = {}, cells = {} }
  local y, at = 0, nil
  for line in io.lines(file) do
    local a, b, c = line:match("^(%-?%d+) (%-?%d+) (%d+)$")
    if a then p.cells[#p.cells + 1] = { tonumber(a) + at[1], y + at[2], tonumber(b) + at[3], tonumber(c) }
    else
      local cmd, rest = line:match("^(%S+)%s*(.*)$")
      if cmd == "L" then y = tonumber(rest)
      elseif cmd == "at" then local x, yy, z = rest:match("(%-?%d+) (%-?%d+) (%-?%d+)"); at = { tonumber(x), tonumber(yy), tonumber(z) }
      elseif cmd == "job" then p.id = rest:match("^(%S+)")
      elseif cmd == "mat" then local n, id, dmg, name = rest:match("^(%d+) (%d+) (%d+) (.*)$"); p.mats[tonumber(n)] = { id = tonumber(id), dmg = tonumber(dmg), name = name } end
    end
  end
  return p
end
local expected, allPlans = {}, {}
do
  local p = io.popen(WIN and ('dir /b "' .. JOBS:gsub("/", "\\") .. '"') or ('ls "' .. JOBS .. '"'))
  for f in p:lines() do
    if f:match("%.txt$") and f ~= "water.txt" then
      local q = readPlan(JOBS .. "/" .. f)
      allPlans[#allPlans + 1] = q
      if not ONLY or ONLY:find(f:sub(1, 1), 1, true) then expected[#expected + 1] = q end
    end
  end
  p:close()
end

-- ---------- the world ----------
local world, turtleAt = {}, {}
local function key(x, y, z) return x .. "," .. y .. "," .. z end
local ENDER, COALB = 801, 173
local predone = {}
for id in (os.getenv("PREDONE") or ""):gmatch("%S+") do predone[id] = true end
for _, p in ipairs(allPlans) do if predone[p.id] then for _, c in ipairs(p.cells) do local m = p.mats[c[4]]; world[key(c[1], c[2], c[3])] = { id = m.id, dmg = m.dmg } end end end
local function at(x, y, z) return world[key(x, y, z)] end
-- PLANTS=n: n plants (like HarvestCraft seaweed) floating at building-ground level across the city
-- area and the depot strip. Turtles have to clear the ones in their way; none may be left.
local PLANTS = tonumber(os.getenv("PLANTS") or 0)
if PLANTS > 0 then
  local f = io.open(JOBS .. "/jobs.json", "rb"); local jt = f:read("*a"); f:close()
  local ox, oy, oz = jt:match('"origin"%s*:%s*%[%s*(%-?%d+),%s*(%-?%d+),%s*(%-?%d+)')
  ox, oy, oz = tonumber(ox), tonumber(oy), tonumber(oz)
  PLANT_ORIGIN = { ox, oy, oz }
  for _ = 1, PLANTS do
    local x, z = ox + math.random(-2, 262), oz + math.random(-2, 270)
    if not world[key(x, oy, z)] and z ~= oz + 266 then world[key(x, oy, z)] = { id = 31, dmg = 0, plant = true } end
  end
end

local clock = 0            -- the scheduler's time (the earliest turtle's)
local restartTimes = {}
for i = 1, RESTARTS do restartTimes[i] = math.random(600, HOURS * 3600 * 0.6) end
for _ = 1, tonumber(os.getenv("EARLY") or 0) do restartTimes[#restartTimes + 1] = math.random(5, 120) end
table.sort(restartTimes)
table.sort(restartTimes)
local stats = { moves = 0, turns = 0, places = 0, digs = 0, perCalls = 0, restocks = 0, restarts = 0, waitsTurtle = 0, http = 0, lostMoves = 0, coal = 0 }

-- ---------- turtles ----------
local DX = { [0] = 1, 0, -1, 0 }
local DZ = { [0] = 0, 1, 0, -1 }
local turtles = {}
local DEPOT_X0, DEPOT_STEP, DEPOT_Z = tonumber(os.getenv("DEPOT_X0") or -858), 8, tonumber(os.getenv("DEPOT_Z") or -200)
local DEPOT_ROW, DEPOT_ROWSTEP = tonumber(os.getenv("DEPOT_ROW") or 0), tonumber(os.getenv("DEPOT_ROWSTEP") or 8)
local GROUND
do local f = io.open(JOBS .. "/jobs.json", "rb"); local jt = f:read("*a"); f:close(); GROUND = tonumber(jt:match('"origin"%s*:%s*%[%s*%-?%d+,%s*(%-?%d+)')) end
local OFFSET = {}
for n, dx, dy, dz in (os.getenv("OFFSET") or ""):gmatch("(%d+):(%-?%d+),(%-?%d+),(%-?%d+)") do OFFSET[tonumber(n)] = { tonumber(dx), tonumber(dy), tonumber(dz) } end
-- the pasted city and the docks
local pasted = 0
if os.getenv("PASTE_JOBS") then
  local dx, dy, dz = os.getenv("PASTE_OFF"):match("(%-?%d+),(%-?%d+),(%-?%d+)"); dx, dy, dz = tonumber(dx), tonumber(dy), tonumber(dz)
  local cut = {}
  for a, b, z in os.getenv("CUT"):gmatch("(%-?%d+),(%-?%d+),(%-?%d+)") do cut[#cut + 1] = { tonumber(a), tonumber(b), tonumber(z) } end
  local function lastZ(x) for _, c in ipairs(cut) do if x >= c[1] and x <= c[2] then return c[3] end end end
  local PJ = os.getenv("PASTE_JOBS")
  local p = io.popen(WIN and ('dir /b "' .. PJ:gsub("/", "\\") .. '"') or ('ls "' .. PJ .. '"'))
  for f in p:lines() do
    if f:match("%.txt$") and f ~= "water.txt" then
      local q = readPlan(PJ .. "/" .. f)
      for _, c in ipairs(q.cells) do
        local x, y, z = c[1] + dx, c[2] + dy, c[3] + dz
        local lz = lastZ(x)
        if lz and z <= lz then local m = q.mats[c[4]]; world[key(x, y, z)] = { id = m.id, dmg = m.dmg, pasted = true }; pasted = pasted + 1 end
      end
    end
  end
  p:close()
  io.stderr:write(string.format("pasted city: %d blocks\n", pasted))
end

local function stackTable(st, names) return st and { id = st.id, dmg = st.dmg, qty = st.qty, name = "x", rawName = "x" } or nil end

local function makeTurtle(n)
  local o = OFFSET[n] or { 0, 0, 0 }
  local row, col = 0, n - 1
-- CHUNKLOG=<file>: every chunk a turtle moved into, written at the end ("cx cz" lines)
CHUNKS = os.getenv("CHUNKLOG") and {} or nil
  if DEPOT_ROW > 0 then row, col = math.floor((n - 1) / DEPOT_ROW), (n - 1) % DEPOT_ROW end
  local T = { n = n, x = DEPOT_X0 + DEPOT_STEP * col + o[1], y = GROUND + o[2], z = DEPOT_Z + DEPOT_ROWSTEP * row + o[3], f = 3, digs = 0, fuel = 2500, sel = 1, inv = {},
    clock = 0, boot = 0, chest = {}, dir = DIR .. "/t" .. n, files = {}, log = {}, restartIdx = 0, finished = false, planMats = {}, planStamp = "" }
  T.start = { T.x, T.y, T.z }
  if os.getenv("PASTE_JOBS") then world[key(T.x, T.y - 1, T.z)] = { id = 1, dmg = 0, pasted = true }; pasted = pasted + 1 end   -- its dock
  T.inv[16] = { id = ENDER, dmg = n, qty = 1 }
  turtleAt[key(T.x, T.y, T.z)] = T
  turtles[n] = T
  return T
end

local function act(T, dt)
  T.clock = T.clock + dt
  coroutine.yield()
end

-- a server restart reached this turtle: true = kill it now
local function restartDue(T)
  local nxt = restartTimes[T.restartIdx + 1]
  if nxt and T.clock >= nxt then T.restartIdx = T.restartIdx + 1; return true end
  return false
end

local DXc = { [0] = 1, 0, -1, 0 }
local DZc = { [0] = 0, 1, 0, -1 }
local function chestAt(T, side)
  local b
  if side == "top" then b = at(T.x, T.y + 1, T.z) elseif side == "front" then b = at(T.x + DXc[T.f], T.y, T.z + DZc[T.f]) end
  return b and b.id == ENDER and b.dmg == T.n
end
local function chestAbove(T) return chestAt(T, "top") end

-- AE: keeps 2 stacks of each block in the turtle's current build file, and a stack of coal blocks
local function aeFill(T)
  local plan = T.files["fleet.plan"] or ""
  local stamp = plan:sub(1, 400)
  if stamp ~= T.planStamp then
    T.planStamp, T.planMats = stamp, {}
    for line in plan:gmatch("[^\n]+") do local id, dmg = line:match("^mat %d+ (%d+) (%d+)"); if id then T.planMats[#T.planMats + 1] = { tonumber(id), tonumber(dmg) } end; if line:match("^L ") then break end end
  end
  local inv = T.chest
  local want = { [COALB .. ":0"] = 64 }
  for _, m in ipairs(T.planMats) do local k = m[1] .. ":" .. m[2]; if not (STOCKOUT[k] and clock < STOCKBACK) then want[k] = 128 end end
  for s = 1, 27 do local st = inv[s]; if st and not want[st.id .. ":" .. st.dmg] then inv[s] = nil end end
  local keys = { COALB .. ":0" }   -- coal first, like citysupply: a full chest must not starve fuel
  for k in pairs(want) do if k ~= COALB .. ":0" then keys[#keys + 1] = k end end
  for _, k in ipairs(keys) do
    local n = want[k]
    local id, dmg = k:match("(%d+):(%d+)"); id, dmg = tonumber(id), tonumber(dmg)
    local have = 0
    for s = 1, 27 do local st = inv[s]; if st and st.id == id and st.dmg == dmg then have = have + st.qty end end
    n = n - have
    for s = 1, 27 do if n <= 0 then break end; local st = inv[s]; if st and st.id == id and st.dmg == dmg and st.qty < 64 then local k2 = math.min(n, 64 - st.qty); st.qty = st.qty + k2; n = n - k2 end end
    for s = 1, 27 do if n <= 0 then break end; if not inv[s] then local k2 = math.min(n, 64); inv[s] = { id = id, dmg = dmg, qty = k2 }; n = n - k2 end end
  end
end

local function envFor(T)
  local E = setmetatable({}, { __index = _G })
  local function target(dir)
    if dir == "up" then return T.x, T.y + 1, T.z elseif dir == "down" then return T.x, T.y - 1, T.z end
    if dir == "back" then return T.x - DX[T.f], T.y, T.z - DZ[T.f] end
    return T.x + DX[T.f], T.y, T.z + DZ[T.f]
  end
  local function store(id, dmg, qty)
    for i = 0, 15 do
      local s = (T.sel - 1 + i) % 16 + 1
      local st = T.inv[s]
      if st and st.id == id and st.dmg == dmg and st.qty < 64 then local k = math.min(qty, 64 - st.qty); st.qty = st.qty + k; qty = qty - k end
      if not st and qty > 0 then T.inv[s] = { id = id, dmg = dmg, qty = math.min(qty, 64) }; qty = qty - math.min(qty, 64) end
      if qty <= 0 then return end
    end
  end
  local function trace(msg)
    T.trace = T.trace or {}
    T.trace[#T.trace + 1] = string.format("%8.1f %s  real %d,%d,%d f%d fuel %d", T.clock, msg, T.x, T.y, T.z, T.f, T.fuel)
    if #T.trace > 40 then table.remove(T.trace, 1) end
  end
  T.traceFn = trace
  local function move(dir)
    local kill = restartDue(T)
    if kill and math.random() < .5 then coroutine.yield("KILL") end
    act(T, .4)
    local x, y, z = target(dir)
    if y > 255 or at(x, y, z) or turtleAt[key(x, y, z)] then
      if turtleAt[key(x, y, z)] then stats.waitsTurtle = stats.waitsTurtle + 1 end
      if kill then coroutine.yield("KILL") end
      return false
    end
    if T.fuel <= 0 then return false end
    turtleAt[key(T.x, T.y, T.z)] = nil
    T.fuel = T.fuel - 1; T.x, T.y, T.z = x, y, z; stats.moves = stats.moves + 1
    trace("moved " .. dir .. (kill and " (answer lost)" or "")); T.acts = (T.acts or 0) + 1
    turtleAt[key(x, y, z)] = T
    if kill then stats.lostMoves = stats.lostMoves + 1; coroutine.yield("KILL") end   -- moved, but the answer is lost
    return true
  end
  local function simple(dt) if restartDue(T) then coroutine.yield("KILL") end; act(T, dt) end
  local function detect(dir) simple(.05); local x, y, z = target(dir); return at(x, y, z) ~= nil or turtleAt[key(x, y, z)] ~= nil end
    if CHUNKS then CHUNKS[(zoneAll and zoneAll[zoneN] or "all") .. " " .. math.floor(x / 16) .. " " .. math.floor(z / 16)] = true end
  local function dig(dir)
    simple(.4)
    local x, y, z = target(dir)
    local b = world[key(x, y, z)]
    if not b then return false end
    world[key(x, y, z)] = nil; stats.digs = stats.digs + 1; T.digs = T.digs + 1
    if b.id == ENDER and b.dmg ~= T.n then
      stats.chestsTaken = (stats.chestsTaken or 0) + 1
      io.stderr:write(string.format("ENDER CHEST of t%d DUG by t%d at %d,%d,%d\n", b.dmg, T.n, x, y, z))
    end
    if b.pasted then
      stats.pastedDigs = (stats.pastedDigs or 0) + 1
      if stats.pastedDigs <= 10 then io.stderr:write(string.format("PASTED BLOCK DUG by t%d at %d,%d,%d (%d:%d)\n", T.n, x, y, z, b.id, b.dmg)) end
    end
    store(b.id, b.dmg, 1)
    return true
  end
  local function place(dir)
    simple(.05)
    local x, y, z = target(dir)
    local st = T.inv[T.sel]
    if not st then return false end
    if y >= 255 then return false end   -- nothing solid at y 255 (the top layer) or above
    if st.id == 2522 then
      -- Extra Utilities' Magnum Torch needs a block that's solid on top under it
      local b = at(x, y - 1, z)
      local NS = { [4095] = true, [20] = true, [89] = true, [101] = true, [152] = true, [1763] = true, [18] = true, [2522] = true, [31] = true }
      if not b or NS[b.id] then stats.torchFails = (stats.torchFails or 0) + 1; return false end
    end
    if at(x, y, z) or turtleAt[key(x, y, z)] then return false end
    world[key(x, y, z)] = { id = st.id, dmg = st.dmg }
    st.qty = st.qty - 1; if st.qty == 0 then T.inv[T.sel] = nil end
    if st.id ~= ENDER then stats.places = stats.places + 1; T.acts = (T.acts or 0) + 1 end
    return true
  end
  local function compare(dir)
    simple(.05)
    local x, y, z = target(dir)
    local b, st = at(x, y, z), T.inv[T.sel]
    if not b or not st then return (not b) and (not st) end
    return b.id == st.id and b.dmg == st.dmg
  end
  E.turtle = {
    forward = function() return move("forward") end, back = function() return move("back") end, up = function() return move("up") end, down = function() return move("down") end,
    turnLeft = function() simple(.4); T.f = (T.f + 3) % 4; stats.turns = stats.turns + 1; trace("turned left"); return true end,
    turnRight = function() simple(.4); T.f = (T.f + 1) % 4; stats.turns = stats.turns + 1; trace("turned right"); return true end,
    detect = function() return detect("forward") end, detectUp = function() return detect("up") end, detectDown = function() return detect("down") end,
    dig = function() return dig("forward") end, digUp = function() return dig("up") end, digDown = function() return dig("down") end,
    place = function() return place("forward") end, placeUp = function() return place("up") end, placeDown = function() return place("down") end,
    compare = function() return compare("forward") end, compareUp = function() return compare("up") end, compareDown = function() return compare("down") end,
    attack = function() simple(.05); return false end, attackUp = function() simple(.05); return false end, attackDown = function() simple(.05); return false end,
    select = function(s) assert(s >= 1 and s <= 16, "bad slot " .. tostring(s)); T.sel = s; return true end,
    transferTo = function(to, n)
      simple(.05)
      local a, b = T.inv[T.sel], T.inv[to]
      if not a then return false end
      if b and (b.id ~= a.id or b.dmg ~= a.dmg) then return false end
      local k = math.min(n or a.qty, a.qty, 64 - (b and b.qty or 0))
      if b then b.qty = b.qty + k else T.inv[to] = { id = a.id, dmg = a.dmg, qty = k } end
      a.qty = a.qty - k; if a.qty == 0 then T.inv[T.sel] = nil end
      return true
    end,
    getItemCount = function(s) local st = T.inv[s or T.sel]; return st and st.qty or 0 end,
    getFuelLevel = function() return T.fuel end, getFuelLimit = function() return 20000 end,
    refuel = function(n)
      local st = T.inv[T.sel]
      if not st or st.id ~= COALB then return false end
      if n == 0 then return true end
      n = math.min(n or st.qty, st.qty)
      T.fuel = math.min(20000, T.fuel + 720 * n); stats.coal = stats.coal + n; st.qty = st.qty - n; if st.qty == 0 then T.inv[T.sel] = nil end
      return true
    end,
    drop = function()
      simple(.05)
      local st = T.inv[T.sel]; if not st then return false end
      if chestAt(T, "front") then
        local inv = T.chest
        for s2 = 1, 27 do local c = inv[s2]; if c and c.id == st.id and c.dmg == st.dmg and c.qty < 64 then local k = math.min(st.qty, 64 - c.qty); c.qty = c.qty + k; st.qty = st.qty - k end end
        for s2 = 1, 27 do if st.qty > 0 and not inv[s2] then inv[s2] = { id = st.id, dmg = st.dmg, qty = st.qty }; st.qty = 0 end end
        if st.qty == 0 then T.inv[T.sel] = nil end
        return true
      end
      T.inv[T.sel] = nil; return true
    end,
    dropUp = function()
      simple(.05)
      local st = T.inv[T.sel]; if not st then return false end
      if chestAbove(T) then
        local inv = T.chest
        for s = 1, 27 do local c = inv[s]; if c and c.id == st.id and c.dmg == st.dmg and c.qty < 64 then local k = math.min(st.qty, 64 - c.qty); c.qty = c.qty + k; st.qty = st.qty - k end end
        for s = 1, 27 do if st.qty > 0 and not inv[s] then inv[s] = { id = st.id, dmg = st.dmg, qty = st.qty }; st.qty = 0 end end
        if st.qty == 0 then T.inv[T.sel] = nil end
        return true
      end
      T.inv[T.sel] = nil; return true
    end,
  }
  local selfP = {
    getAllStacks = function() stats.perCalls = stats.perCalls + 1; act(T, .05); local t = {}; for s = 1, 16 do t[s] = stackTable(T.inv[s]) end; return t end,
    getStackInSlot = function(s) stats.perCalls = stats.perCalls + 1; act(T, .05); return stackTable(T.inv[s]) end,
  }
  local chestP = {
    getAllStacks = function() stats.perCalls = stats.perCalls + 1; act(T, .05); local t = {}; for s = 1, 27 do t[s] = stackTable(T.chest[s]) end; return t end,
    pushItem = function(dir, cs, n, ts)
      stats.perCalls = stats.perCalls + 1; act(T, .05)
      assert(ts >= 1 and ts <= 16)
      local c, t = T.chest[cs], T.inv[ts]
      if not c then return 0 end
      if t and (t.id ~= c.id or t.dmg ~= c.dmg) then return 0 end
      local k = math.min(n or 64, c.qty, 64 - (t and t.qty or 0))
      if k <= 0 then return 0 end
      c.qty = c.qty - k; if c.qty == 0 then T.chest[cs] = nil end
      if t then t.qty = t.qty + k else T.inv[ts] = { id = c.id, dmg = c.dmg, qty = k } end
      return k
    end,
  }
  E.peripheral = {
    getMethods = function(side)
      if side == "left" then return { "getAllStacks", "getStackInSlot" } end
      if (side == "top" or side == "front") and chestAt(T, side) then return { "getAllStacks", "pushItem" } end
      return nil
    end,
    wrap = function(side)
      if side == "left" then return selfP end
      if (side == "top" or side == "front") and chestAt(T, side) then stats.restocks = stats.restocks + 1; if side == "front" then stats.frontChests = (stats.frontChests or 0) + 1 end; return chestP end
      return nil
    end,
    getType = function(side)
      local x, y, z = target(side == "top" and "up" or side == "bottom" and "down" or "forward")
      if turtleAt[key(x, y, z)] then return "turtle" end
      local b = at(x, y, z)
      if b and b.id == ENDER then return "ender_chest" end
      return nil
    end,
  }
  local FS = T.files
  E.fs = {
    exists = function(p) return FS[p] ~= nil end,
    delete = function(p) FS[p] = nil end,
    move = function(a, b) assert(FS[a], "no file " .. a); FS[b] = FS[a]; FS[a] = nil end,
    getSize = function(p) return FS[p] and #FS[p] or 0 end,
    open = function(p, mode)
      if mode == "a" then return { writeLine = function(s) FS[p] = (FS[p] or "") .. s .. "\n" end, close = function() end } end
      if mode == "w" then
        local buf = {}
        return { write = function(s) buf[#buf + 1] = s end, close = function()
          local text = table.concat(buf)
          FS[p] = text
          if p == "fleet.state" and not T.desynced then
            local st = textutils.unserialize(text)
            local q = st and st.pos
            if q and (q.x ~= T.x or q.y ~= T.y or q.z ~= T.z or q.f ~= T.f) then
              T.desynced = true
              io.stderr:write(string.format("DESYNC t%d at %.0f s: saved %d,%d,%d f%d, real %d,%d,%d f%d, phase %s, pend %s\n", T.n, T.clock,
                q.x, q.y, q.z, q.f, T.x, T.y, T.z, T.f, tostring(st.phase), st.pend and "yes" or "no"))
              for _, l in ipairs(T.trace or {}) do io.stderr:write("   " .. l .. "\n") end
              for i = math.max(1, #T.log - 10), #T.log do io.stderr:write("   log " .. T.log[i] .. "\n") end
            end
          end
        end }
      end
      local text = FS[p]; if not text then return nil end
      local pos = 1
      return { readLine = function()
          if pos > #text then return nil end
          local e = text:find("\n", pos, true)
          local l = text:sub(pos, (e or #text + 1) - 1); pos = (e or #text) + 1
          return l
        end,
        readAll = function() local r = text:sub(pos); pos = #text + 1; return r end, close = function() end }
    end,
  }
  local function resp(s) if not s or s == "" then return nil end; stats.http = stats.http + 1; return { readAll = function() return s end, close = function() end } end
  E.http = {
    get = function(url)
      simple(.1)
      if url:find("raw.githubusercontent.com", 1, true) then
        local h = relFile and io.open(relFile, "rb"); local code = h and h:read("*a"); if h then h:close() end
        stats.downloads = (stats.downloads or 0) + 1
        return resp(code)
      end
      if fastHttp then return resp(fastHttp(url)) end
      return resp(curl('"' .. url .. '"'))
    end,
    post = function(url, body)
      simple(.1)
      local s
      if fastHttp then s = fastHttp(url, body)
      else
        local f = io.open(T.dir .. "/.post", "wb"); f:write(body); f:close()
        s = curl('--data-binary @"' .. T.dir .. '/.post" "' .. url .. '"')
      end
      if os.getenv("DEBUGHTTP") and url:find("/report") then
        io.stderr:write(string.format("%7.0f t%d %s -> %s\n", T.clock, T.n, body:match("msg ([^\n]*)") or "", ((s or "nil"):gsub("\n", ""))))
      end
      return resp(s)
    end,
  }
  E.sleep = function(n) if restartDue(T) then coroutine.yield("KILL") end; act(T, n) end
  E.read = function() return "" end
  E.os = setmetatable({ day = function() return 1 end, time = function() return 12 end, clock = function() return T.clock - T.boot end,
    setComputerLabel = function() end, reboot = function() coroutine.yield("KILL") end, shutdown = function() coroutine.yield("OFF") end }, { __index = os })
  E.textutils = textutils
  E.print = function(s)
    s = tostring(s)
    T.log[#T.log + 1] = string.format("%8.0fs  %s", T.clock, s)
    if s:find("Nothing left to build") then T.finished = true end
    if s:find("Paused") then T.saidPaused = true end
    if s:find(ZONES_STRICT and "the open zones are done" or "open the next zone", 1, true) and not zoneAsk then zoneAsk = T.clock end
    local bx, by, bz = s:match("in my way at (%-?%d+),(%-?%d+),(%-?%d+)")
    if bx and not T.toldBlock then
      T.toldBlock = true
      local b = world[key(tonumber(bx), tonumber(by), tonumber(bz))]
      io.stderr:write(string.format("t%d (fuel %d, at %d,%d,%d facing %d, clock %d) blocked at %s,%s,%s by %s (turtle there: %s)\n", T.n, T.fuel, T.x, T.y, T.z, T.f, T.clock, bx, by, bz,
        b and (b.id .. ":" .. b.dmg) or "nothing", tostring(turtleAt[key(tonumber(bx), tonumber(by), tonumber(bz))] and turtleAt[key(tonumber(bx), tonumber(by), tonumber(bz))].n)))
    end
  end
  E.shell = { run = function() end }
  return E
end

-- textutils (shared)
local function ser(v)
  local t = type(v)
  if t == "table" then local parts = {}; for k, x in pairs(v) do parts[#parts + 1] = "[" .. ser(k) .. "]=" .. ser(x) end; return "{" .. table.concat(parts, ",") .. "}" end
  if t == "string" then return string.format("%q", v) end
  return tostring(v)
end
textutils = { formatTime = function() return "12:00" end, serialize = ser, unserialize = function(s) local f = loadstring("return " .. s); if not f then return nil end; local ok, v = pcall(f); return ok and v or nil end }

local prog = assert(loadfile(os.getenv("CITYFLEET") or "cityfleet"))
local function start(T, runArgs)
  local own = T.files["cityfleet"]     -- an updated copy the turtle downloaded
  local f = own and assert(loadstring(own, "cityfleet")) or assert(loadfile(os.getenv("CITYFLEET") or "cityfleet"))
  setfenv(f, envFor(T))
  T.boot = T.clock
  T.co = coroutine.create(function() return f(unpack(runArgs)) end)
end

-- LATE="n:seconds;...": turtle n is started that late (after others may have built in front of it)
local LATE = {}
-- STRAY="n,m": those turtles are left running from an earlier build (never say hello to this one)
local STRAY = {}
for n in (os.getenv("STRAY") or ""):gmatch("%d+") do STRAY[tonumber(n)] = true end
for n, s in (os.getenv("LATE") or ""):gmatch("(%d+):(%d+)") do LATE[tonumber(n)] = tonumber(s) end
for n = 1, N do
  local T = makeTurtle(n)
  T.clock = LATE[n] or 0
  while restartTimes[T.restartIdx + 1] and restartTimes[T.restartIdx + 1] <= T.clock do T.restartIdx = T.restartIdx + 1 end   -- (before it was started)
  if STRAY[n] then
    -- left running from an earlier build: it never says hello to this one, it just carries on
    T.files["cityfleet.cfg"] = textutils.serialize({ slot = n, label = "stray" .. n, server = SERVER })
    T.files["fleet.state"] = textutils.serialize({ phase = "ask", pos = { x = T.x, y = T.y, z = T.z, f = 3 }, home = { x = T.x, y = T.y, z = T.z },
      chest = { id = ENDER, dmg = n }, lanes = { e = 1e9, w = 1e9, s = 1e9, n = 1e9, wait = 1e9 } })
    start(T, {})
  else
    start(T, { tostring(n), SERVER, "go" })
  end
end

-- ---------- run ----------
local lastAE, lastShow, deadline = -1e9, 0, HOURS * 3600
local pFrom, pTo = (PAUSE or ""):match("(%d+),(%d+)")
pFrom, pTo = tonumber(pFrom), tonumber(pTo)
local pState, movesAt, pausedMoves, pauseDetail = 0, 0, nil, ""
local lastStock, materials = -1e9, nil
local function postBody(path, body, key)
  local f = io.open(DIR .. "/body2", "wb"); f:write(body); f:close()
  return curl(string.format('-X POST %s --data-binary @%s/body2 "%s%s"', key and ('-H "X-Key: ' .. KEY .. '"') or "", DIR, SERVER, path))
end
local stuckSince = {}
zoneAsk = nil
local ZONES_AUTO, ZONE_DELAY, zoneLog = os.getenv("ZONES_AUTO") == "1", tonumber(os.getenv("ZONE_DELAY") or 300), {}
local zonesOpen
if ZONES_AUTO then
  local f = io.open(JOBS .. "/jobs.json", "rb"); local jt = f:read("*a"); f:close()
  local zs = jt:match('"zones"%s*:%s*(%b[])')
  zonesOpen = {}
  for k in zs:gmatch('"key"%s*:%s*"(%a+)"') do zonesOpen[#zonesOpen + 1] = k end
  zoneAll, zoneN = zonesOpen, 1
end
local ended = "time limit"
local RESTART, RESTART_CRASH, RESTART_DONE = tonumber(os.getenv("RESTART") or ""), os.getenv("RESTART_CRASH") == "1", os.getenv("RESTART_DONE") == "1"
local placeAt, restartLog = nil, {}
local function pressRestart(at)
  assert(postBody("/restart", "30", true):match("^restart"), "restart refused")
  restartLog[#restartLog + 1] = string.format("Restart pressed at %.2f h", at / 3600)
  for _, t in ipairs(turtles) do t.finished = false end
  placeAt = at + 600
end
-- the player puts every switched-off turtle back on its depot spot (with its Ender Chest) and turns it on
local function placeTurtles()
  local n = 0
  for _, t in ipairs(turtles) do
    if t.off then
      if not t.inv[16] then
        for k, b in pairs(world) do if b.id == ENDER and b.dmg == t.n then world[k] = nil; t.inv[16] = { id = ENDER, dmg = t.n, qty = 1 }; break end end
      end
      turtleAt[key(t.x, t.y, t.z)] = nil
      t.x, t.y, t.z, t.f = t.start[1], t.start[2], t.start[3], 3
      assert(not turtleAt[key(t.x, t.y, t.z)], "depot spot taken")
      turtleAt[key(t.x, t.y, t.z)] = t
      t.off, t.clock = false, math.max(t.clock, placeAt)
      t.log[#t.log + 1] = string.format("%8.0fs  -- put on its depot spot and turned on --", t.clock)
      start(t, {})
      n = n + 1
    end
  end
  restartLog[#restartLog + 1] = string.format("%d turtles put back on their spots at %.2f h", n, placeAt / 3600)
  placeAt = nil
end
while true do
  local T
  for _, t in ipairs(turtles) do if t.co and (not T or t.clock < T.clock) then T = t end end
  if placeAt and (not T or T.clock >= placeAt) then placeTurtles(); T = nil; for _, t in ipairs(turtles) do if t.co and (not T or t.clock < T.clock) then T = t end end end
-- ZONES_STRICT=1: the next zone is opened only once a turtle is told the open zones are done (like
-- the player, who moves the chunk loaders when every turtle is home)
ZONES_STRICT = os.getenv("ZONES_STRICT") == "1"
  if not T then ended = "every turtle stopped"; break end
  if RESTART and T.clock >= RESTART then
    pressRestart(T.clock); RESTART = nil
    if RESTART_CRASH then for _, t in ipairs(turtles) do if t.co then t.crashAt = T.clock + 5 end end end
  end
  if T.crashAt and T.clock >= T.crashAt then
    T.crashAt = nil
    T.log[#T.log + 1] = string.format("%8.0fs  -- Minecraft server restart (rebooted where it is) --", T.clock)
    T.clock = T.clock + 30
    start(T, {})
  end
  clock = T.clock
  if clock - lastAE >= 5 then lastAE = clock; for _, t in ipairs(turtles) do aeFill(t) end end
  -- Pause / Resume
  if pFrom then
    if pState == 0 and clock >= pFrom then pState = 1; assert(postBody("/pause", "", true):match("paused"), "pause refused") end
    if pState == 1 and clock >= pFrom + 120 then pState = 2; movesAt = stats.moves + stats.places; for _, t in ipairs(turtles) do t.actsAt = t.acts or 0 end end
    if pState == 2 and clock >= pTo then
      pState = 3; pausedMoves = stats.moves + stats.places - movesAt
      local parts = {}
      for _, t in ipairs(turtles) do parts[#parts + 1] = string.format("t%d:%d%s", t.n, (t.acts or 0) - (t.actsAt or 0), t.saidPaused and "P" or "") end
      pauseDetail = table.concat(parts, " ") assert(postBody("/resume", "", true):match("running"), "resume refused") end
  end
  if relAt and clock >= relAt then
    relAt = nil
    assert(postBody("/release", relV .. " " .. string.rep("ab", 20), true):match("^release"), "release refused")
  end
  -- stock counts, like citysupply
  if next(STOCKOUT) and clock - lastStock >= 300 then
    lastStock = clock
    materials = materials or curl('"' .. SERVER .. '/materials"')
    local lines = {}
    for k in materials:gmatch("(%d+:%d+)") do lines[#lines + 1] = k .. " " .. ((STOCKOUT[k] and clock < STOCKBACK) and 0 or 1000000) end
    postBody("/stock", table.concat(lines, "\n") .. "\n")
  end
  if ZONES_AUTO and zoneAsk and clock >= zoneAsk + ZONE_DELAY and zoneN < #zoneAll then
    zoneN = zoneN + 1
    local open = {}
    for i = 1, zoneN do open[#open + 1] = zoneAll[i] end
    assert(postBody("/zones", table.concat(open, " "), true):match("^open"), "zones refused")
    local away = 0
    for _, t in ipairs(turtles) do if t.x ~= t.start[1] or t.z ~= t.start[3] then away = away + 1 end end
    zoneLog[#zoneLog + 1] = string.format("%s opened at %.2f h (asked %.2f h, %d placed, %d turtles away from home)", zoneAll[zoneN], clock / 3600, zoneAsk / 3600, stats.places, away)
    io.stderr:write(zoneLog[#zoneLog] .. "\n")
    zoneAsk = nil
    for _, t in ipairs(turtles) do t.zoneHint = nil end
  end
  if clock > deadline then break end
  local done = true
  for _, t in ipairs(turtles) do if not t.finished and t.co then done = false end end
  if done and RESTART_DONE then
    RESTART_DONE = false
    assert(postBody("/reset", "", true):match("ok"), "reset refused")
    pressRestart(clock)
    placeAt = nil      -- nobody to put back: they're all home
    done = false
  end
  if done then ended = "all turtles home, nothing left"; break end
  -- stalled: nothing placed for 2 sim-hours while work is left
  if stats.places ~= (lastPlaces or -1) then lastPlaces, lastPlaceAt = stats.places, clock end
  if clock - (lastPlaceAt or 0) > 7200 then ended = "STALLED (nothing placed for 2 h)"; break end
  if clock - lastShow > 1800 then
    lastShow = clock
    io.stderr:write(string.format("%5.1f h  placed %d  waits for turtles %d  http %d\n", clock / 3600, stats.places, stats.waitsTurtle, stats.http))
  end
  local ok, sig = coroutine.resume(T.co)
  if not ok then
    T.log[#T.log + 1] = string.format("%8.0fs  CRASH %s", T.clock, tostring(sig))
    io.stderr:write("turtle " .. T.n .. " crashed: " .. tostring(sig) .. "\n")
    T.co = nil
  elseif sig == "KILL" then
    stats.restarts = stats.restarts + 1
    T.log[#T.log + 1] = string.format("%8.0fs  -- server restart --", T.clock)
    if T.traceFn then T.traceFn("RESTART") end
    T.clock = T.clock + 30
    start(T, {})
  elseif sig == "OFF" then
    T.log[#T.log + 1] = string.format("%8.0fs  switched itself off at %d,%d,%d", T.clock, T.x, T.y, T.z)
    T.co, T.off = nil, true
    stats.offs = (stats.offs or 0) + 1
  elseif coroutine.status(T.co) == "dead" then
    T.log[#T.log + 1] = string.format("%8.0fs  program ended", T.clock)
    T.co = nil
  end
end

-- ---------- check ----------
local want, missing, wrong, extra, total = {}, 0, 0, 0, 0
local missingBy = {}
for _, p in ipairs(allPlans) do for _, c in ipairs(p.cells) do want[key(c[1], c[2], c[3])] = true end end
for _, p in ipairs(expected) do
  for _, c in ipairs(p.cells) do
    total = total + 1
    local k = key(c[1], c[2], c[3]); want[k] = true
    local b, m = world[k], p.mats[c[4]]
    if not b then missing = missing + 1; missingBy[p.id] = (missingBy[p.id] or 0) + 1; if missing <= 5 then io.write("  missing at ", k, " ", m.id, ":", m.dmg, " (", p.id, ")\n") end
    elseif b.id ~= m.id or b.dmg ~= m.dmg then wrong = wrong + 1; if wrong <= 5 then io.write("  wrong at ", k, " have ", b.id, ":", b.dmg, " want ", m.id, ":", m.dmg, " (", p.id, ")\n") end end
  end
end
local plantsLeft = 0
for k, b in pairs(world) do
  if b.plant then
    -- a plant inside the city area that no turtle cleared
    local x, _, z = k:match("(%-?%d+),(%-?%d+),(%-?%d+)")
    local lx, lz = tonumber(x) - PLANT_ORIGIN[1], tonumber(z) - PLANT_ORIGIN[3]
    if lx >= 0 and lx <= 260 and lz >= 0 and lz <= 253 then plantsLeft = plantsLeft + 1; if plantsLeft <= 5 then io.write("  plant left at ", k, "\n") end end
  elseif not want[k] and not b.pasted then extra = extra + 1; if extra <= 5 then io.write("  extra block ", k, " id ", b.id, ":", b.dmg, "\n") end end
end
if PLANTS > 0 then io.write(string.format("plants left in the city area: %d\n", plantsLeft)) end
for _, T in ipairs(turtles) do
  io.write(string.format("\n== turtle %d at %d,%d,%d facing %d, fuel %d, clock %.1f h ==\n", T.n, T.x, T.y, T.z, T.f, T.fuel, T.clock / 3600))
  local from = tonumber(os.getenv("LOGLINES") or 12)
  for i = math.max(1, #T.log - from + 1), #T.log do io.write(T.log[i], "\n") end
end
if pasted > 0 then
  local left = 0
  for _, b in pairs(world) do if b.pasted then left = left + 1 end end
  io.write(string.format("\npasted blocks: %d, still there %d, dug %d\n", pasted, left, stats.pastedDigs or 0))
end
for n in pairs(OFFSET) do
  local T = turtles[n]
  io.write(string.format("misplaced turtle %d: started at %d,%d,%d, now %d,%d,%d facing %d, digs %d, program %s\n", n, T.start[1], T.start[2], T.start[3],
    T.x, T.y, T.z, T.f, T.digs, T.co and "still running" or "ended"))
end
local mb = {}
for id, n in pairs(missingBy) do mb[#mb + 1] = id .. ":" .. n end
table.sort(mb)
if pFrom then io.write(string.format("\npause %d..%d s: moves + placements while paused (from 2 min after Pause): %s\n  per turtle (P = said Paused): %s\n", pFrom, pTo, tostring(pausedMoves), pauseDetail)) end
if next(STOCKOUT) then
  local stuck = 0
  for _, T in ipairs(turtles) do for _, l in ipairs(T.log) do if l:find("needs") then stuck = stuck + 1 end end end
  io.write(string.format("Ender Chest waits logged (\"needs ...\"): %d\n", stuck))
end
for _, l in ipairs(zoneLog) do io.write("zone ", l, "\n") end
for _, l in ipairs(restartLog) do io.write(l, "\n") end
if #restartLog > 0 then
  local fresh = 0
  for _, T in ipairs(turtles) do for _, l in ipairs(T.log) do if l:find("start over", 1, true) or l:find("starting over", 1, true) then fresh = fresh + 1 end end end
  io.write(string.format("switched off: %d   fresh starts logged: %d\n", stats.offs or 0, fresh))
end
io.write(string.format("\nended: %s at %.2f h\nblocks %d  missing %d  wrong %d  extra %d\n", ended, clock / 3600, total, missing, wrong, extra))
if #mb > 0 then io.write("missing by job: ", table.concat(mb, " "):sub(1, 600), "\n") end
if relV then
  local n = 0
  for _, t in ipairs(turtles) do
    if t.files["cityfleet"] and t.files["cityfleet"]:find('VERSION = "' .. relV .. '"', 1, true) then n = n + 1 end
  end
  io.write(string.format("release %s: %d of %d turtles updated (%d downloads)\n", relV, n, #turtles, stats.downloads or 0))
end
io.write(string.format("Ender Chests of other turtles dug: %d\n", stats.chestsTaken or 0))
io.write(string.format("Ender Chests put in front (top of the world): %d   torch placements refused: %d\n", stats.frontChests or 0, stats.torchFails or 0))
io.write(string.format("moves %d  turns %d  places %d  digs %d  restocks %d  peripheral calls %d  http %d  restarts %d (lost answers %d)  waits for turtles %d  coal blocks burned %d\n",
  stats.moves, stats.turns, stats.places, stats.digs, stats.restocks, stats.perCalls, stats.http, stats.restarts, stats.lostMoves, stats.waitsTurtle, stats.coal))
if CHUNKS then local f = io.open(os.getenv("CHUNKLOG"), "w"); for k in pairs(CHUNKS) do f:write(k, "\n") end; f:close() end
