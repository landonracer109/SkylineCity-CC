# Skyline City CC

A fleet of ComputerCraft 1.63 turtles that builds Skyline City, a procedurally generated downtown of
51 skyscrapers, parks, streets and a highway grid (seed 49696, 388k blocks), on the TechIt-ng
server. Built for Minecraft 1.6.4 with OpenPeripherals, EnderStorage and Applied Energistics 1.

## How it works

- **Job server** ([server/citysrv.py](server/citysrv.py)): splits the city into 206 jobs (72 platform tiles,
  64 street-furniture tiles, 66 buildings and building parts, 3 parks, the park water), hands each
  turtle the next ready job, and plans its flight. Turtles travel on lanes 12–15 blocks above the
  streets, one height per direction (east, west, south, north), so they never meet head on. Also
  serves a progress page at `/city/`.
- **`cityfleet`** (turtle): asks for a job, flies there, builds it bottom-up placing blocks down,
  restocks from its own Ender Chest, and asks for the next one. Survives server restarts: it saves
  its state on every move, and tells from its fuel level whether a move whose answer was lost
  happened.
- **`citysupply`** (computer at home): keeps every turtle's Ender Chest stocked from AE with exactly
  what its next ~640 placements need, plus coal blocks, and sends everything else back.
- **`citybuild`**: the older single-building version of the turtle program (no server).

## Install (in game, with the `get` program)

Turtles: advanced turtle with a Narcissistic upgrade (OpenPeripherals Addons) and a diamond
pickaxe, an Ender Chest in slot 16 (different colours for each turtle; private chests work and keep
other players out, but then every chest, turtle or home, must be yours).

```
get https://raw.githubusercontent.com/landonracer109/SkylineCity-CC/<commit>/cityfleet cityfleet
cityfleet <slot>
```

Put turtle `<slot>` (1–16) on its depot spot facing **north** first: x = −858 + 8 × (slot − 1),
y 63, z −200. It counts its own moves (there's no GPS), so never move it by hand while it runs.

Supply computer: wired modems on 4 ME Interfaces (ME cable on the bottom) and on 16 public Ender
Chests around them (any colours: a spare chest takes the colours of a turtle that has none;
private or public, the same as the turtles' chests).

```
get https://raw.githubusercontent.com/landonracer109/SkylineCity-CC/<commit>/citysupply citysupply
citysupply
```

## Tools

- `tools/export-all.js`: turns the generator ([tools/skyline-city.html](tools/skyline-city.html)) into job
  files: `node export-all.js skyline-city.html 49696 220 192 jobs [originX originY originZ]`.
- `tools/render.js`: a top-down PNG of the exported jobs, for checking.
- `sim/fleetsim.lua`: many turtles running the real `cityfleet` against the real job server, with
  collisions, Ender Chest restocks and server restarts; checks every block at the end
  (`sh sim/run.sh 16 4`). Whole city: 5.5 h with 16 turtles, no errors.
- `sim/supplysim.lua`: `citysupply` against a fake AE network.
- `testrig/`: puts a test fleet into a stopped test server's world (region files), and checks the
  result afterwards.
