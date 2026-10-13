-- Tries citysupply against a fake AE network and fake home Ender Chests.
--   luajit supplysim.lua
-- 2 ME Interfaces; 5 home chests: 4 touch an interface (one on each of 4 sides), 1 touches none.
-- Turtles: city1 (chest colours 100, has a matching home chest), city2 (colours 200, no home chest
-- yet: a spare one should take its colours), city3 (colours 300, idle: its chest should be emptied
-- back to AE except coal). AE is short of lapis. Checks the chests after two rounds.
local DIR = "simsupply"
for _, f in ipairs({ "supply.log", "citysupply.map", "citysupply.cfg", "startup" }) do os.remove(DIR .. "/" .. f) end

local AE = { ["173:0"] = 1000, ["1:0"] = 5000, ["4095:0"] = 3000, ["22:0"] = 40, ["928:2"] = 900 }
local calls = 0
local INV = {}       -- frequency -> inventory (chests on the same colours share it)
local function invOf(f) INV[f] = INV[f] or {}; return INV[f] end
local function newChest(freq)
  return setmetatable({ freq = freq }, { __index = function(t, k) if k == "inv" then return invOf(t.freq) end end })
end
local SHARED = os.getenv("SHARED")
local CH = SHARED and {
  ender_chest_0 = newChest(0), ender_chest_1 = newChest(0), ender_chest_2 = newChest(0),
  ender_chest_3 = newChest(0), ender_chest_4 = newChest(0),
} or {
  ender_chest_0 = newChest(100), ender_chest_1 = newChest(555), ender_chest_2 = newChest(300),
  ender_chest_3 = newChest(777), ender_chest_4 = newChest(888),
}
-- the suction chest on frequency 0 empties it into AE now and then
local function suck() for s2 = 1, 27 do local st = invOf(0)[s2]; if st then AE[st.id .. ":" .. st.dmg] = (AE[st.id .. ":" .. st.dmg] or 0) + st.qty; invOf(0)[s2] = nil end end end
-- leftovers in city3's chest (it's between jobs) and junk in city1's
if not SHARED then
CH.ender_chest_2.inv[1] = { id = 1, dmg = 0, qty = 50 }
CH.ender_chest_2.inv[2] = { id = 173, dmg = 0, qty = 10 }
CH.ender_chest_0.inv[5] = { id = 3, dmg = 0, qty = 12 }
end
-- which chest is on which side of which interface
local SIDES = { me_interface_0 = { north = "ender_chest_0", south = "ender_chest_1" }, me_interface_1 = { east = "ender_chest_2", up = "ender_chest_3" } }
local OPP = { north = "south", south = "north", east = "west", west = "east", up = "down", down = "up" }

local function add(c, id, dmg, n)
  for s = 1, 27 do local st = c.inv[s]; if n <= 0 then break end; if st and st.id == id and st.dmg == dmg and st.qty < 64 then local k = math.min(n, 64 - st.qty); st.qty = st.qty + k; n = n - k end end
  for s = 1, 27 do if n <= 0 then break end; if not c.inv[s] then local k = math.min(n, 64); c.inv[s] = { id = id, dmg = dmg, qty = k }; n = n - k end end
  return n   -- left over (the real chest would throw it away)
end

local function iface(name)
  return {
    extractItem = function(it, dir)
      calls = calls + 1
      local key = it.id .. ":" .. it.dmg
      local cname = SIDES[name][dir]
      if not cname then return 0 end
      local n = math.min(it.qty, AE[key] or 0)
      assert(it.qty <= 64, "asked for more than a stack")
      local left = add(CH[cname], it.id, it.dmg, n)
      AE[key] = (AE[key] or 0) - (n - left)
      return n - left
    end,
    countOfItemType = function(id, dmg) calls = calls + 1; return AE[id .. ":" .. dmg] or 0 end,
    getAvailableItems = function() calls = calls + 1; local t = {}; for k, n in pairs(AE) do local id, dmg = k:match("(%d+):(%d+)"); t[#t + 1] = { id = tonumber(id), dmg = tonumber(dmg), qty = n, maxSize = 64 } end; return t end,
  }
end
local function chestP(name)
  local c = CH[name]
  return {
    getAllStacks = function() calls = calls + 1; if math.random() < .5 then suck() end; local t = {}; for s = 1, 27 do t[s] = c.inv[s] and { id = c.inv[s].id, dmg = c.inv[s].dmg, qty = c.inv[s].qty } end; return t end,
    getFrequency = function() calls = calls + 1; return c.freq end,
    setFrequency = function(f) calls = calls + 1; c.freq = f end,
    pushItem = function(dir, slot, n)
      calls = calls + 1
      -- into the interface on that side = back into AE
      for iname, sides in pairs(SIDES) do for d, cn in pairs(sides) do
        if cn == name and OPP[d] == dir then
          local st = c.inv[slot]; if not st then return 0 end
          local k = math.min(n or st.qty, st.qty); st.qty = st.qty - k; if st.qty == 0 then c.inv[slot] = nil end
          AE[st.id .. ":" .. st.dmg] = (AE[st.id .. ":" .. st.dmg] or 0) + k
          return k
        end
      end end
      return 0
    end,
  }
end
local P = { me_interface_0 = iface("me_interface_0"), me_interface_1 = iface("me_interface_1") }
for name in pairs(CH) do P[name] = chestP(name) end
peripheral = {
  getNames = function() local t = {}; for k in pairs(P) do t[#t + 1] = k end; table.sort(t); return t end,
  getMethods = function(n) local t = {}; for k in pairs(P[n] or {}) do t[#t + 1] = k end; return t end,
  getType = function(n) return n:match("^(.-)_%d+$") end,
  wrap = function(n) return P[n] end,
}
local NEEDS = "city1 chest 100\ncity1 4095:0 380\ncity1 928:2 200\ncity1 22:0 60\ncity2 chest 200\ncity2 1:0 640\ncity3 chest 300\n"
http = {
  get = function(url) assert(url:match("/needs$")); return { readAll = function() return NEEDS end, close = function() end } end,
  post = function() return { close = function() end } end,
}
local function path(p) return DIR .. "/" .. p end
fs = {
  exists = function(p) local h = io.open(path(p)); if h then h:close() return true end return false end,
  getSize = function(p) local h = io.open(path(p), "rb"); if not h then return 0 end; local n = h:seek("end"); h:close(); return n end,
  open = function(p, mode)
    if mode == "a" then return { writeLine = function(s) local h = io.open(path(p), "ab"); h:write(s, "\n"); h:close() end, close = function() end } end
    if mode == "w" then local buf = {}; return { write = function(s) buf[#buf + 1] = s end, close = function() local h = io.open(path(p), "wb"); h:write(table.concat(buf)); h:close() end } end
    local h = io.open(path(p), "rb"); if not h then return nil end
    return { readAll = function() return h:read("*a") end, close = function() h:close() end }
  end,
}
local function ser(v)
  if type(v) == "table" then local parts = {}; for k, x in pairs(v) do parts[#parts + 1] = "[" .. ser(k) .. "]=" .. ser(x) end; return "{" .. table.concat(parts, ",") .. "}" end
  if type(v) == "string" then return string.format("%q", v) end
  return tostring(v)
end
textutils = { serialize = ser, unserialize = function(s) local f = loadstring("return " .. s); if not f then return nil end; local ok, v = pcall(f); return ok and v or nil end, formatTime = function() return "12:00" end }
os.day = function() return 1 end
os.time = function() return 12 end
local rounds = 0
function sleep() rounds = rounds + 1; if rounds >= 2 then error("SIM_END", 0) end end

local f = assert(loadfile("citysupply"))
local ok, err = pcall(f)
if err ~= "SIM_END" then print("ERROR: " .. tostring(err)) end

local function show(name)
  local t, total = {}, {}
  for s = 1, 27 do local st = CH[name].inv[s]; if st then local k = st.id .. ":" .. st.dmg; total[k] = (total[k] or 0) + st.qty end end
  for k, n in pairs(total) do t[#t + 1] = k .. "=" .. n end
  table.sort(t)
  print(string.format("  %s (colours %d): %s", name, CH[name].freq, table.concat(t, " ")))
end
print("chests after 2 rounds:")
for _, n in ipairs({ "ender_chest_0", "ender_chest_1", "ender_chest_2", "ender_chest_3", "ender_chest_4" }) do show(n) end
print(string.format("AE now: lapis %d, stone %d, coal blocks %d, dirt %d; peripheral calls %d", AE["22:0"], AE["1:0"], AE["173:0"], AE["3:0"] or 0, calls))
