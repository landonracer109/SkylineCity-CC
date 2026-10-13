-- Simulates a CC 1.63 turtle (Narcissistic upgrade + pickaxe) running citybuild against a fake world.
--   luajit sim.lua <build file> [restarts] [seed]
-- The world: solid ground below y 0, tall grass and a small tree inside the footprint, another
-- turtle that parks on the build path for a while, and a 27-slot Ender Chest that "AE" refills
-- every 5 s (each block kept at 2 stacks, coal at 1 stack, anything else pulled out), with one
-- block missing from AE for 5 minutes. The run is killed (like a server restart) at random
-- moments and started again from startup. At the end every block is checked against the plan.

local PLANFILE, RESTARTS, SEED = arg[1], tonumber(arg[2] or 5), tonumber(arg[3] or 1)
math.randomseed(SEED)
local DIR = os.getenv("SIMDIR") or "simfs"
os.execute('mkdir "' .. DIR .. '" 2>nul')
for _, f in ipairs({ "city.state", "city.plan", "plan.src", "startup" }) do os.remove(DIR .. "/" .. f) end

-- ---------- read the plan (for setting up the world and checking the result) ----------
local plan = { mats = {}, cells = {} }
do
  local y = 0
  for line in io.lines(PLANFILE) do
    local a, b, c = line:match("^(%-?%d+) (%-?%d+) (%d+)$")
    if a then plan.cells[#plan.cells + 1] = { tonumber(a), y, tonumber(b), tonumber(c) }
    else
      local cmd, rest = line:match("^(%S+)%s*(.*)$")
      if cmd == "L" then y = tonumber(rest)
      elseif cmd == "mat" then local n, id, dmg, name = rest:match("^(%d+) (%d+) (%d+) (.*)$"); plan.mats[tonumber(n)] = { id = tonumber(id), dmg = tonumber(dmg), name = name }
      elseif cmd == "size" then local w, h, d = rest:match("^(%d+) (%d+) (%d+)"); plan.w, plan.h, plan.d = tonumber(w), tonumber(h), tonumber(d) end
    end
  end
end

-- ---------- the world ----------
local clock, log = 0, {}
local stats = { moves = 0, turns = 0, places = 0, digs = 0, perCalls = 0, pushes = 0, restocks = 0, restarts = 0, waits = 0 }
local world = {}
local function key(x, y, z) return x .. "," .. y .. "," .. z end
local function at(x, y, z) local b = world[key(x, y, z)]; if b then return b end; if y < 0 then return { id = 3, dmg = 0, ground = true } end; return nil end
local ENDER, COAL, LOG, LEAVES, GRASS = 801, 263, 17, 18, 31
local terrain = {}
-- tall grass on 40 random spots of the first layer, a tree in the footprint
for _ = 1, 40 do local x, z = math.random(0, plan.w - 1), math.random(0, plan.d - 1); world[key(x, 0, z)] = { id = GRASS, dmg = 0 }; terrain[key(x, 0, z)] = true end
do
  local tx, tz = math.floor(plan.w / 2), math.floor(plan.d / 2) + 1
  for y = 0, 3 do world[key(tx, y, tz)] = { id = LOG, dmg = 0 }; terrain[key(tx, y, tz)] = true end
  for dx = -1, 1 do for dz = -1, 1 do for y = 3, 4 do
    if not (dx == 0 and dz == 0 and y == 3) then world[key(tx + dx, y, tz + dz)] = { id = LEAVES, dmg = 0 }; terrain[key(tx + dx, y, tz + dz)] = true end
  end end end
end
-- another turtle that sits on the path above the first layers from 120 s to 200 s
local other = { x = 1e9, y = 0, z = 0, from = 0, to = 0, spawned = false }   -- parks right in front of me once, for 60 s
local function otherAt(x, y, z) return clock >= other.from and clock < other.to and x == other.x and y == other.y and z == other.z end

-- ---------- the Ender Chest (shared storage, 27 slots) and AE refilling it ----------
local chestInv = {}
local NAMES = {}
for n, m in pairs(plan.mats) do NAMES[m.id .. ":" .. m.dmg] = m.name end
local function itemName(id, dmg) return NAMES[id .. ":" .. dmg] or (id == COAL and "Coal") or (id == ENDER and "Ender Chest") or ("item " .. id) end
local missing   -- the most-used block runs out in AE for a while
do local c, best = {}, 0; for _, cl in ipairs(plan.cells) do c[cl[4]] = (c[cl[4]] or 0) + 1; if c[cl[4]] > best then best, missing = c[cl[4]], cl[4] end end end
local missingFrom, missingTo = nil, nil
local lastAE = -1e9
local function aeTick()
  if clock - lastAE < 5 then return end
  lastAE = clock
  -- pull out anything that isn't a building block or coal
  for s = 1, 27 do local st = chestInv[s]; if st and not NAMES[st.id .. ":" .. st.dmg] and st.id ~= COAL then chestInv[s] = nil end end
  local function have(id, dmg) local n = 0; for s = 1, 27 do local st = chestInv[s]; if st and st.id == id and st.dmg == dmg then n = n + st.qty end end; return n end
  local function add(id, dmg, n)
    for s = 1, 27 do local st = chestInv[s]; if n <= 0 then break end; if st and st.id == id and st.dmg == dmg and st.qty < 64 then local k = math.min(n, 64 - st.qty); st.qty = st.qty + k; n = n - k end end
    for s = 1, 27 do if n <= 0 then break end; if not chestInv[s] then local k = math.min(n, 64); chestInv[s] = { id = id, dmg = dmg, qty = k }; n = n - k end end
  end
  for n, m in pairs(plan.mats) do
    local off = n == missing and missingFrom and clock >= missingFrom and clock < missingTo
    if off then for s = 1, 27 do local st = chestInv[s]; if st and st.id == m.id and st.dmg == m.dmg then chestInv[s] = nil end end
    else add(m.id, m.dmg, 128 - have(m.id, m.dmg)) end
  end
  add(COAL, 0, 64 - have(COAL, 0))
end

-- ---------- the turtle ----------
local T = { x = -1, y = 0, z = -1, f = 0, fuel = 400, sel = 1, inv = {} }
T.inv[16] = { id = ENDER, dmg = 0, qty = 1 }
local DX = { [0] = 1, 0, -1, 0 }
local DZ = { [0] = 0, 1, 0, -1 }
local killAt = {}
local actions = 0
local function act(dt, noKill)
  clock = clock + dt; actions = actions + 1
  aeTick()
  if clock > 4 * 3600 then error("SIM_TIMEOUT", 0) end
  if killAt[actions] and not noKill then killAt[actions] = nil; error("SIM_RESTART", 0) end
end
local function target(dir)
  if dir == "up" then return T.x, T.y + 1, T.z elseif dir == "down" then return T.x, T.y - 1, T.z end
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
local function move(dir)
  act(.4)
  local x, y, z = target(dir)
  if not other.spawned and actions > 600 and dir == "forward" then other = { x = x, y = y, z = z, from = clock, to = clock + 60, spawned = true } end
  if at(x, y, z) or otherAt(x, y, z) then return false end
  if T.fuel <= 0 then return false end
  T.fuel = T.fuel - 1; T.x, T.y, T.z = x, y, z; stats.moves = stats.moves + 1
  return true
end
local function detect(dir) act(.05); local x, y, z = target(dir); return at(x, y, z) ~= nil or otherAt(x, y, z) end
local function dig(dir)
  act(.4)
  local x, y, z = target(dir)
  local b = world[key(x, y, z)]
  if not b or otherAt(x, y, z) then return false end
  world[key(x, y, z)] = nil; stats.digs = stats.digs + 1
  if b.id == LOG then store(LOG, 0, 1) elseif b.id == ENDER then store(ENDER, 0, 1)
  elseif b.id ~= LEAVES and b.id ~= GRASS then store(b.id, b.dmg, 1) end
  return true
end
local function place(dir)
  act(.05)
  local x, y, z = target(dir)
  local st = T.inv[T.sel]
  if not st then return false end
  local b = at(x, y, z)
  if (b and b.id ~= GRASS) or otherAt(x, y, z) then return false end
  world[key(x, y, z)] = { id = st.id, dmg = st.dmg }
  st.qty = st.qty - 1; if st.qty == 0 then T.inv[T.sel] = nil end
  if st.id ~= ENDER then stats.places = stats.places + 1 end
  return true
end
local function compare(dir)
  act(.05)
  local x, y, z = target(dir)
  local b, st = at(x, y, z), T.inv[T.sel]
  if not b or not st then return (not b) and (not st) end
  return b.id == st.id and b.dmg == st.dmg
end

turtle = {
  forward = function() return move("forward") end, up = function() return move("up") end, down = function() return move("down") end,
  back = function() return false end,
  turnLeft = function() act(.4); T.f = (T.f + 3) % 4; stats.turns = stats.turns + 1; return true end,
  turnRight = function() act(.4); T.f = (T.f + 1) % 4; stats.turns = stats.turns + 1; return true end,
  detect = function() return detect("forward") end, detectUp = function() return detect("up") end, detectDown = function() return detect("down") end,
  dig = function() return dig("forward") end, digUp = function() return dig("up") end, digDown = function() return dig("down") end,
  place = function() return place("forward") end, placeUp = function() return place("up") end, placeDown = function() return place("down") end,
  compare = function() return compare("forward") end, compareUp = function() return compare("up") end, compareDown = function() return compare("down") end,
  attack = function() act(.05); return false end, attackUp = function() act(.05); return false end, attackDown = function() act(.05); return false end,
  select = function(s) assert(s >= 1 and s <= 16, "bad slot " .. tostring(s)); T.sel = s; return true end,
  getSelectedSlot = function() return T.sel end,
  transferTo = function(to, n)
    act(.05)
    local a, b = T.inv[T.sel], T.inv[to]
    if not a then return false end
    if b and (b.id ~= a.id or b.dmg ~= a.dmg) then return false end
    local k = math.min(n or a.qty, a.qty, 64 - (b and b.qty or 0))
    if b then b.qty = b.qty + k else T.inv[to] = { id = a.id, dmg = a.dmg, qty = k } end
    a.qty = a.qty - k; if a.qty == 0 then T.inv[T.sel] = nil end
    return true
  end,
  getItemCount = function(s) local st = T.inv[s or T.sel]; return st and st.qty or 0 end,
  getItemSpace = function(s) local st = T.inv[s or T.sel]; return st and 64 - st.qty or 64 end,
  getFuelLevel = function() return T.fuel end, getFuelLimit = function() return 20000 end,
  refuel = function(n)
    local st = T.inv[T.sel]
    if not st or st.id ~= COAL then return false end
    if n == 0 then return true end
    n = math.min(n or st.qty, st.qty)
    T.fuel = math.min(20000, T.fuel + 80 * n); st.qty = st.qty - n; if st.qty == 0 then T.inv[T.sel] = nil end
    return true
  end,
  dropUp = function()
    act(.05)
    local st = T.inv[T.sel]; if not st then return false end
    local b = at(T.x, T.y + 1, T.z)
    if b and b.id == ENDER then
      for s = 1, 27 do local c = chestInv[s]; if c and c.id == st.id and c.dmg == st.dmg and c.qty < 64 then local k = math.min(st.qty, 64 - c.qty); c.qty = c.qty + k; st.qty = st.qty - k end end
      for s = 1, 27 do if st.qty > 0 and not chestInv[s] then chestInv[s] = { id = st.id, dmg = st.dmg, qty = st.qty }; st.qty = 0 end end
      if st.qty == 0 then T.inv[T.sel] = nil end
      return true
    end
    T.inv[T.sel] = nil; return true
  end,
}

local function stackTable(st) return st and { id = st.id, dmg = st.dmg, qty = st.qty, name = itemName(st.id, st.dmg), rawName = "x" } or nil end
local selfP = {
  getAllStacks = function() stats.perCalls = stats.perCalls + 1; act(.05, true); local t = {}; for s = 1, 16 do t[s] = stackTable(T.inv[s]) end; return t end,
  getStackInSlot = function(s) stats.perCalls = stats.perCalls + 1; act(.05, true); return stackTable(T.inv[s]) end,
}
local chestP = {
  getAllStacks = function() stats.perCalls = stats.perCalls + 1; act(.05, true); local t = {}; for s = 1, 27 do t[s] = stackTable(chestInv[s]) end; return t end,
  pushItem = function(dir, cs, n, ts)
    stats.perCalls = stats.perCalls + 1; stats.pushes = stats.pushes + 1; act(.05, true)
    assert(dir == "down", "pushItem direction " .. tostring(dir))
    assert(ts >= 1 and ts <= 16, "pushItem into bad slot " .. tostring(ts))
    local c, t = chestInv[cs], T.inv[ts]
    if not c then return 0 end
    if t and (t.id ~= c.id or t.dmg ~= c.dmg) then return 0 end
    local k = math.min(n or 64, c.qty, 64 - (t and t.qty or 0))
    if k <= 0 then return 0 end
    c.qty = c.qty - k; if c.qty == 0 then chestInv[cs] = nil end
    if t then t.qty = t.qty + k else T.inv[ts] = { id = c.id, dmg = c.dmg, qty = k } end
    return k
  end,
}
local function chestAbove() local b = at(T.x, T.y + 1, T.z); return b and b.id == ENDER end
peripheral = {
  getMethods = function(side)
    if side == "left" then return { "getAllStacks", "getStackInSlot", "pushItem", "pullItem" } end
    if side == "top" and chestAbove() then return { "getAllStacks", "pushItem", "pullItem" } end
    return nil
  end,
  wrap = function(side)
    if side == "left" then return selfP end
    if side == "top" and chestAbove() then
      stats.restocks = stats.restocks + 1
      if os.getenv("SIMDEBUG") then local inChest, inTurtle = 0, 0; local m = plan.mats[missing]; for s = 1, 27 do local c = chestInv[s]; if c and c.id == m.id and c.dmg == m.dmg then inChest = inChest + c.qty end end; for s = 1, 15 do local c = T.inv[s]; if c and c.id == m.id and c.dmg == m.dmg then inTurtle = inTurtle + c.qty end end; log[#log + 1] = string.format("%7.0fs  [debug] restock: %s in chest %d, in turtle %d", clock, m.name, inChest, inTurtle) end
      return chestP
    end
    return nil
  end,
  getType = function(side)
    local x, y, z = target(side == "top" and "up" or side == "bottom" and "down" or "forward")
    if otherAt(x, y, z) then return "turtle" end
    local b = at(x, y, z)
    if b and b.id == ENDER then return "ender_chest" end
    return nil
  end,
}

-- ---------- the computer: files, timers, http, terminal ----------
local function path(p) return DIR .. "/" .. p end
fs = {
  exists = function(p) local h = io.open(path(p), "r"); if h then h:close(); return true end; return false end,
  delete = function(p) os.remove(path(p)) end,
  copy = function(a, b) local h = io.open(path(a), "rb"); local d = h:read("*a"); h:close(); h = io.open(path(b), "wb"); h:write(d); h:close() end,
  getSize = function(p) local h = io.open(path(p), "rb"); if not h then return 0 end; local n = h:seek("end"); h:close(); return n end,
  open = function(p, mode)
    if mode == "a" then
      return { writeLine = function(s) local h = io.open(path(p), "ab"); h:write(s, "\n"); h:close() end, close = function() end }
    end
    if mode == "w" then
      local buf = {}
      return { write = function(s) buf[#buf + 1] = s end, close = function() local h = io.open(path(p), "wb"); h:write(table.concat(buf)); h:close() end }
    end
    local h = io.open(path(p), "rb"); if not h then return nil end
    return { readLine = function() return h:read("*l") end, readAll = function() return h:read("*a") end, close = function() h:close() end }
  end,
}
http = { get = function(url) local h = io.open(PLANFILE, "rb"); local d = h:read("*a"); h:close(); return { readAll = function() return d end, close = function() end } end }
local function ser(v)
  local t = type(v)
  if t == "table" then local parts = {}; for k, x in pairs(v) do parts[#parts + 1] = "[" .. ser(k) .. "]=" .. ser(x) end; return "{" .. table.concat(parts, ",") .. "}" end
  if t == "string" then return string.format("%q", v) end
  return tostring(v)
end
textutils = { formatTime = function() return "12:00" end, serialize = ser, unserialize = function(s) local f = loadstring("return " .. s); if not f then return nil end; local ok, v = pcall(f); return ok and v or nil end }
function sleep(n) stats.waits = stats.waits + 1; clock = clock + n; aeTick(); if clock > 4 * 3600 then error("SIM_TIMEOUT", 0) end end
function read() return "" end
os.day = function() return 1 end
os.time = function() return 12 end
local lastPrint = ""
function print(s) s = tostring(s); if s ~= lastPrint then log[#log + 1] = string.format("%7.0fs  %s", clock, s) end; lastPrint = s end
shell = { run = function() end }

-- ---------- run it, killing it now and then ----------
local copy = io.open(path("plan.src"), "wb"); copy:write(io.open(PLANFILE, "rb"):read("*a")); copy:close()
local expected = #plan.cells
local approxActions = expected * 4
for _ = 1, RESTARTS do killAt[math.random(50, approxActions)] = true end
missingFrom = 600; missingTo = 1000
local prog = assert(loadfile("citybuild"))
local runArgs = { "plan.src" }
local finished = false
for attempt = 1, RESTARTS + 5 do
  local env = setmetatable({}, { __index = _G })
  setfenv(prog, env)
  local ok, err = pcall(prog, unpack(runArgs))
  if ok then finished = true; break end
  if err ~= "SIM_RESTART" then print("ERROR: " .. tostring(err)); break end
  stats.restarts = stats.restarts + 1
  print("-- server restart --")
  runArgs = {}     -- after a restart the computer runs startup -> citybuild with no arguments
end

-- ---------- check the result ----------
local wrong, missingBlocks, extra = 0, 0, 0
local want = {}
for _, c in ipairs(plan.cells) do
  local m = plan.mats[c[4]]
  want[key(c[1], c[2], c[3])] = true
  local b = world[key(c[1], c[2], c[3])]
  if not b then missingBlocks = missingBlocks + 1 elseif b.id ~= m.id or b.dmg ~= m.dmg then wrong = wrong + 1 end
end
for k, b in pairs(world) do if not want[k] and not terrain[k] then extra = extra + 1; if extra <= 3 then io.write("  extra block ", k, " id ", b.id, "\n") end end end
local leftInTurtle = 0
for s = 1, 15 do if T.inv[s] then leftInTurtle = leftInTurtle + T.inv[s].qty end end
for i = 1, #log do if not log[i]:find("Layer") then io.write(log[i], "\n") end end
io.write(string.format("\nfinished=%s  blocks %d  missing %d  wrong %d  extra %d  turtle at %d,%d,%d facing %d  state file left: %s\n",
  tostring(finished), expected, missingBlocks, wrong, extra, T.x, T.y, T.z, T.f, tostring(fs.exists("city.state"))))
io.write(string.format("time %.1f h (%.0f s)  moves %d  turns %d  places %d  digs %d  restocks %d  peripheral calls %d (%.1f per 100 blocks)  restarts %d  fuel left %d  items left in turtle %d\n",
  clock / 3600, clock, stats.moves, stats.turns, stats.places, stats.digs, stats.restocks, stats.perCalls, stats.perCalls * 100 / expected, stats.restarts, T.fuel, leftInTurtle))
