# FactorioLab to Satisfactory Blueprint

An independent Satisfactory-focused fork of `flab2bp`. Turn a
[FactorioLab Satisfactory](https://factoriolab.github.io/sfy) URL and its exported
flow into a connected production blueprint (`.sbp` and `.sbpcfg`). The `sections`
planner builds rated production sections, composes them by material flow within
the selected Blueprint Designer, and validates the complete placement before
emission. It preserves FactorioLab's machine counts, clocks and rates, including
supported mixed solid/fluid recipes and their byproducts.

Standard plastic and rubber layouts have been verified offline at 20, 60 and
80 items/min in Mk2, using one, three and four refineries on one production
floor. Crude-oil supply and residue drainage are retained. This is geometry,
connectivity and binary validation, not an in-game throughput guarantee; see
[the placement model](docs/sfy-layout-model.md) for stacking and hydraulic limits.

The Python package and commands retain the `flab2bp` name. For example:

```bash
uv sync
uv run flab2bp 'https://factoriolab.github.io/sfy/list?o=iron-plate*60&v=11' \
  --flow path/to/export.csv --designer mk3 -o out/plates
```

Satisfactory defaults to `sections`, its only production planner, with one layout
budget. The CLI and web API reject the former `best`, `manifold-rows` and
`grid-routed` Satisfactory choices rather than translating or falling back.
Historical measurements and reference implementations remain available as
evidence, not production alternatives. Inherited DSP functionality retains its
own defaults; the following inherited interface notes describe that DSP path.

## Web interface

### Requirements and setup

- Python 3.14 or newer
- [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh/) for the browser interface and TypeScript cross-validation
- A C++17-capable compiler when building the native extensions from source

Satisfactory visualization uses the bundled registry and the Python binary decoder;
it does not require DSP assets or a local game installation. The optional DSP
viewer needs its extracted `web/public/assets/` directory.

```bash
uv sync
cd web
bun install --frozen-lockfile
cd ..
uv run flab2bp-web
```

Open <http://127.0.0.1:8000>. `flab2bp-web` builds the front end when necessary;
pass `--build` to force a rebuild, `--no-build` to serve an existing build, or
`--host` and `--port` to change the listener. For DSP viewing only, run
`bun run extract-assets "/path/to/Dyson Sphere Program"` from `web/`, or copy
an existing extracted assets directory.

Paste a Satisfactory FactorioLab URL, choose the Blueprint Designer and press
**Build**. Automatic flow capture is selected when entering a Satisfactory URL
without a supplied CSV; a local flow export can be used instead. The completed
factory appears in 3D alongside its validation report and both binary downloads.
The inherited DSP controls below retain their own strategies and blueprint-string
copying workflow.

**A build is a job, not a request.** `--budget` is per layout and `best` lays out every
candidate with freeform, sequence-pair, CaDiCaL-based transport-routing and hierarchical,
so a build can run for seconds to minutes. `best` selects the smallest validator-clean result, not the first to
finish. Transport-routing supports unsprayed and sprayed interfaces, including separate
raw-material and proliferator feeds; every result must pass the same physical validation.
`POST /api/build` returns an id immediately and the page polls `GET /api/build/<id>`. `pipeline.build` reports
each candidate/strategy pair as it starts and settles. A projected total over 300 seconds
warns that the job may take a while; it does not refuse or silently clamp the request.
Raced builds allocate an aggregate CPU budget (by default at most 16 affinity CPUs)
across the widest candidate batch that can fund four workers per candidate. Each
candidate gives hierarchical one quarter of its share (at least one worker), keeps
one worker for transport-routing, and divides the rest between freeform and sequence-pair.
A single 16-worker portfolio starts at 8/3/1/4 workers in that order. When the
request's machine groups already guarantee at least 15 strips, Freeform's existing
single-worker packing rule reduces its share to one and transfers the unused
workers to hierarchy (1/3/1/11 at 16 total; 1/6/1/24 at 32). Smaller requests keep
the base allocation. If four workers cannot be funded, `best` runs the strategies
serially within the aggregate worker budget.
Completed race children perform full validation. The parent reuses that judgment
only for the identical placement, complete request and belt rules; changed geometry
or a missing judgment is validated again. Every retained alternative is still encoded.

Detailed routing and relaxed global congestion routing use the native geometric interval
engine (`route_backend: geometric`), without an A* fallback. Routing budget and `expansions`
counters measure newly prepared cells plus processed active intervals, not historical A*
node expansions. Existing numerical caps, deadlines, and atomic-completion grace remain
unchanged. The two routing modes retain their distinct congestion costs: detailed search
charges arrivals; relaxed search also charges starts and ramp-via cells.
A bounded reverse interval probe can identify a sealed destination pocket without
exhausting the larger source region. Its prepared cells and interval work consume the
same allowance as forward search. Exhaustion reports distinguish the source-reachable
component from the goal-reaching component; interrupted probes supply neither proof.

Constructive transport selection enforces the emitter's inclusive XY canvas envelope
for both selected routes and immutable fixed paths, including owned endpoints.
Out-of-envelope routes are rejected before emission; bounds are not widened to fit
a selected route. Standalone template problems remain unbounded unless bounds are supplied.
Power infill indexes fixed power-node centres per exact projection and frame before
checking prospective sites. The index conservatively narrows static peers; original
power-spacing predicates, candidate order, selected-node checks and final validation remain.

**A refusal is a result.** A spec that cannot be laid out reports why each strategy and
candidate gave up. An invalid build withholds the blueprint and lists the validation errors,
matching the CLI unless `--allow-invalid` is explicitly requested.

Flow provenance is explicit. `--flow FILE` pins a FactorioLab CSV export; `--fetch-flow` is
opt-in and drives installed Chromium to export FactorioLab's solved flow. Capture failure
refuses the build instead of silently deriving a different recipe selection. Automatic fetch
and pasted or uploaded CSV are mutually exclusive.

For TypeScript development, `cd web && bun run dev` starts and supervises both the Python API
on port 8000 and Rsbuild on port 3001. To use an API managed separately at a different origin,
run the frontend from `web/` with:

```bash
FLAB2BP_API=http://127.0.0.1:9000 bun run dev:frontend
```

`web/README.md` documents remote access, external API configuration, asset extraction, and
the individual web commands.

`uv run scripts/web_smoke.py` drives the integrated server in a real browser and decodes the
blueprint copied by the page. [docs/WEB_UI.md](docs/WEB_UI.md) documents the UI and API.

## Alternative: command-line interface

Use the CLI for scripting, automation, or direct access to advanced solver options:

```bash
uv run flab2bp 'https://factoriolab.github.io/dsp/flow?o=super-magnetic-ring*60&ibe=conveyor-belt-2&mmr=arc-smelter~assembling-machine-2~chemical-plant~matrix-lab&mps=proliferator-2-products&v=11'
```

The command prints the blueprint string to standard output. Use `-o FILE` to write it to a
file, and `uv run flab2bp --help` for the full strategy, candidate-policy, latitude-band,
flow-provenance, and validation options.

Power is always included. The web **Power tower** selector and CLI
`--power-tower tesla|substation|wireless` override the URL's preferred machines.
Without an override, the first recognized power building in the URL's machine
rank is used; otherwise it remains Tesla Tower. The Python API accepts the same
choice names as `pipeline.build(..., power_tower="substation")`; web requests
use `"power_tower": "auto"` for URL selection. Non-default blueprint descriptions
and the web **Power** report row name the selected building.

Satellite Substations retain a low-confidence collider caveat: their planner
reserves centred catalog clearance, but a clean certificate alone does not prove
the suppressed substation/belt collider cases. Direct clearance checks and
in-game paste validation remain necessary; larger towers can also expose
conservative power-planning refusals near a coverage boundary.

## What it builds

The whole recipe chain from the FactorioLab flow, minus mining. Ores, water, oil and
proliferator arrive on input belts at one edge; the target item leaves on an output belt at the
other. Everything between is built, wired and throughput-correct: no belt lane carries more than
its tier allows; belts start at the tier the URL chose and a run that needs more is raised to the
cheapest faster belt the URL's technologies unlock; sorters likewise stay within the researched
tiers, no sorter is asked to exceed its rate, and every machine is fed at the rate its
recipe demands.

Density is the objective. The layout may use direct insertion between adjacent machines, long
(2- and 3-tile) sorters, stacked belts at multiple altitudes, and proliferator up to Mk.III --
choosing per recipe between *extra products* mode, which compounds savings up the chain, and
*production speedup* mode, which halves machine count at that step.

The `hierarchical` strategy automatically competes in `best` and can also be selected alone.
It prioritizes a feasible factory: it tries one eligible
solver per unresolved block shape and runs an alternate only if that shape remains unresolved. Divisible
refused blocks are cut before another parent-widening round, within the existing budget.
The initial block schedule reduces workers per block only when needed to fund its
independent waves; that worker profile stays fixed across re-cuts. Final composition
may negotiate further routing rounds within the same deadline and expansion allowance.
This can trade density for lower latency; it does not promise the smallest layout.
The `best` portfolio's smallest-validator-clean-result selection is unchanged.

## Latitude portability

`--band portable` is the default for all layout strategies and the web interface. The
available selections are:

```text
portable
5x20 5x40 5x80 5x100
10x160 10x200
15x300 15x400
25x500 25x600
50x800
160x1000
```

Portable mode starts with the globally smallest band in which the unpadded layout fits (`B0`)
and certifies `B0` plus up to two bands with greater `area_segments`. It checks the same
orientation and frame at every legal latitude anchor in every named band. Near the equator
there may be fewer than three bands left, so the report lists the exact bands actually
certified; Portable does not claim every latitude band on the planet.

The finalizer may add zero through four empty **latitude** rows, split between the north and
south margins, while keeping `B0` fixed. It never adds longitude padding. It picks the smallest
passing frame, or refuses the layout rather than emitting a blueprint with a weaker guarantee.
A refusal retains structured evidence for each distinct projection failure: band, check,
building indices, and the authoritative detail.

An explicit named selection certifies only that requested band, using the same search of up to
four latitude rows. If the layout does not fit the band or fails at any legal anchor, the build
refuses. Successful CLI reports and web results expose both `primary_band` and the literal
`certified_bands` tuple.

Latitude certification does not change the validator's `flow.external_entry_points` warning:
multiple reachable external lanes for one item remain valid, but the player must connect a
supply to every lane.

## Pipeline

```
URL ──1──> LabRequest ──2──> RateSolution ──3──> BuildSpec ──4──> Placement ──5──> blueprint
           settings +        recipe →            integer          buildings,
           objectives        machines,           machines +       belts, sorters
                             exact Fractions     item flows       on a tile grid
```

| Module | Responsibility |
| --- | --- |
| `lab/url.py` | Parse bare and `z=` compressed FactorioLab URLs |
| `lab/data.py` | Fetch, cache and type the DSP dataset |
| `rates/solve.py` | Objectives → per-recipe machine counts, in exact rational arithmetic |
| `spec.py` | `RateSolution` → `BuildSpec`, the frozen rates/geometry boundary |
| `layout/base.py` | `LayoutStrategy` protocol, `Placement`, geometry primitives |
| `layout/freeform.py` | Freeform — CP-SAT rectangle packing + detailed belt router |
| `layout/sequence_solver.py` | SequencePair — staged sequence-pair search using the shared router |
| `layout/transport_routing/` | CaDiCaL-backed constructive transport routing |
| `layout/hierarchy/` | Hierarchical block decomposition, solving and composition |
| `layout/validate.py` | Strategy-independent judge: overlap, reach, continuity, throughput |
| `dsp/codec.py` | `Placement` → binary → gzip → base64 → header → MD5F, and back |
| `bench/` | Compares Freeform and SequencePair over the URL corpus |

Stage 4 has four production implementations behind one interface. `best` runs all four and
returns the smallest validator-clean placement.

Everything upstream of `BuildSpec` is arithmetic on rationals with no geometry. Everything
downstream is geometry with no rate reasoning.

## Correctness

The DSP blueprint format is unforgiving — a checksum mismatch or a byte out of place and the game
silently refuses the paste. Four independent guards:

1. **Byte-identical re-encode.** All 11 real game blueprints in `tests/fixtures/` decode and
   re-encode to exactly their original string, checksum included. This proves the writer emits
   what the game itself emits.
2. **Self-check.** Every generated `Placement` goes through `layout/validate.py` before encoding.
3. **Cross-validation.** Generated strings are parsed by the independent TypeScript decoder in
   `../dsp-blueprint-viewer` via `bun`, so an encoder bug cannot hide behind a matching bug in our
   own decoder. Skipped cleanly when that repo or `bun` is absent.
4. **Geometry against real blueprints.** `tile_to_local_offset` — the one place tile space becomes
   DSP world coordinates — is checked against player-built fixtures: 686 of 686 machine-side sorter
   endpoints land inside the machine they serve, where the two corner readings score 248/676 and
   174/666. A blueprint the game emitted is necessarily legal, which makes the fixtures an oracle.

A layout is only shipped if the validator accepts it. When no strategy can produce a valid one,
`lay_out` raises `NoValidLayout` rather than degrading — a blueprint that pastes and then does not
run is the one failure nobody discovers until they are standing in front of it in game.

DSP's blueprint checksum is a *variant* of MD5 — two altered init constants and eight altered
round constants, not derivable from `sin()`. See `dsp/md5f.py`.

## Gate result

On 2026-09-07, the `lane-fanout` branch's final gate ran two rounds of the stress-tier corpus
(72 cells, both placers) against the master `a1401518` baseline: **FAIL**. Coverage held at
66/72 CLEAN in both rounds, with zero regressions against baseline (0/72 cells moved status in
either direction) and a paired area ratio of 1.000000 (round 1) / 0.999424 (round 2) on the 66
cells CLEAN in both arms. The six `universe-matrix` cells this branch's work targets (both
placers × `no-proliferator`/`all-products`/`output-products`) remain REFUSED in both rounds — the
fan-out guard this branch deleted no longer fires (no candidate refusal says "must tap"), but two
other, pre-existing blockers do: the freeform packer's clock-limited packing shortfall and the
sequence-pair per-island exact-layout search's non-convergence. Full measurements, verbatim
refusal text, and the ranked residual blockers are in
`docs/superpowers/evidence/2026-09-07-lane-fanout/gate/verdict.md`.

## Satisfactory production sections

`flab2bp.sfy` reads and writes Satisfactory blueprint pairs (`.sbp`/`.sbpcfg`)
and uses the extracted game registry in `src/flab2bp/sfy/data/` for machines,
ports, conveyors, lifts, pipes, pumps, junctions, beams, floor holes and designers.

The same `flab2bp` command takes a Satisfactory URL:

```bash
flab2bp 'https://factoriolab.github.io/sfy/list?o=iron-plate*60&v=11' \
    --flow plates.csv --designer mk1 -o blueprints/
```

It validates the placement against the rules extracted from the game and writes
`<name>.sbp` and `<name>.sbpcfg` into the directory `-o` names (created if missing;
the report goes to stdout, since the blueprint is two binary files). Copy both
into a save's `blueprints/<session>` folder and load them from inside a Blueprint
Designer. `--designer` picks `mk1`, `mk2` or `mk3`, sized from the game's own
designer buildable, and defaults to `mk1`.

`--strategy` accepts only `sections` (the default). Solid recipe groups use
opposing machine rows, or one longer row when opposing bodies are too deep for
the designer. Supported mixed-material groups add separate pipe manifolds and
retain every ingredient and product, including fluid byproducts.
Exact machine clocks, fractional last machines and transport-tier ceilings
are preserved. Each external input has an upward pass-through lane; each export
has a separate downward collector ending at the bottom. Conveyor stack lanes use
the lowest available tier carrying this module's local demand/export, not the
available upgrade ceiling or the URL's selected `ibe` tier. Inter-section lifts
are sized for the busiest routed stream. Combined flow from stacked copies must
remain within the reported trunk capacities.

Whole sections are composed by material dependency, keeping consumers above
their producers while allowing independent recipe sections to share a floor.
The local routers connect section interfaces, not arbitrarily packed individual
machines. Full base/roof slabs and painted-beam outlines define the repeatable module;
matching floor/ceiling holes align its material trunks. The selected designer
contains one complete blueprint, not a silently truncated partial factory.

Each material lane gets native paired signs at both physical ends: an INPUT/OUTPUT
header, a stable lane letter, and the material's local per-module rate (items/min
or m³/min). Bottom signs label local input or output; upper continuation ends say PASS THROUGH
instead of claiming an unknown stacked rate. Orange-on-black SmallWide signs
mount on short painted beams along the front (+X) frame, above the base or hanging
beneath the roof. Placement checks the whole text face's view toward the front,
so lifts cannot hide the words. Signs add Iron Plates and Quartz Crystals to the
construction cost; their supports add Steel Beams. Missing clear mounting space
is an explicit refusal.

Frame-mounted double Wall Outlets Mk.2 replace floor power poles. Their inside
faces form one connected circuit and power every machine and pump; the outside
faces stay free for external hookups. Native seven-wire limits apply per face,
and additional sockets are added when needed.

**Stacking:** keep the same XY position and orientation and raise each copy by
the stack pitch printed in its `.sbpcfg`. Manually bridge the matching holes
across the **400 cm (4 m) seam** with lifts or pipes; automatic joining is not
promised. Feed the bottom inputs, connect an outside wall outlet to power, and drain
the bottom outputs, including byproducts. Inputs rise and combined outputs descend.
Supply all copies' combined demand and
keep accumulated flow within each reported trunk capacity. For liquids, also
provide the reported inlet head above the bottom port: it is calculated from
the actual routed path to the next pump inlet, not assumed external pressure.

`--budget` supplies one layout/validation deadline (15 seconds by default).
There is no strategy race or fallback, and a routing refusal is not proof of
geometric infeasibility.

**FactorioLab's own solved flow is required**, unlike the DSP path, which re-derives a recipe
selection when none is given. Pass `--flow` with the CSV the list view's "download as CSV"
button writes, or `--fetch-flow` to have it captured from the URL; without one the build refuses
rather than solving a selection the player did not choose. Exit codes are `0`
written, `2` a bad URL, spec or missing flow, and `3` no validator-clean layout or
no encodable blueprint. `--allow-invalid` remains DSP-only; it cannot override
the Satisfactory strategy's validation contract.

FactorioLab **Input** and **Limit** objectives are accepted and skipped when identifying
blueprint outputs. They remain in the URL, so FactorioLab can use them to constrain its
recipe selection. **Output** and **Maximize** item objectives export the achieved net
rate from the solved flow, after internal consumption—not the Maximize objective's
weight. Required input connections and rates also come from the solved flow, not the
Input or Limit amounts; recipes and machine counts are not re-solved.

Supported solid-production families include constructors, assemblers,
manufacturers, smelters and foundries; supported mixed recipes include refinery
plastic production with heavy-oil residue retained as an output. This is not
arbitrary fluid-network support: cyclic/recycled coupled dependencies,
unsupported port geometry, insufficient belt/pipe capacity or hydraulic head,
and geometry beyond the designer remain explicit refusals.

The web build API accepts the same strategy/designer choices and a pinned or
automatically captured flow. Successful Satisfactory jobs are visualized in the
same web UI and expose downloadable `.sbp`/`.sbpcfg` pairs. Generated configs
describe net input/output rates, machine recipes and clocks, estimated production
power, required Power Shards/Somersloops and hookup instructions. The existing
verified icon/color defaults are retained; the game takes the name from the filename.

Open or drop an existing `.sbp` to inspect it, including historical files with
arbitrary object IDs. The schematic viewer shows registry-derived machine geometry,
sampled belts and pipes, splitters/mergers, junctions/pumps, lift spans,
foundations, beams, floor holes, storage and power links.
Use Q/E to rotate, O for top-down, drag to orbit and scroll to zoom. Layer controls,
ghost/solid/hidden machines, click selection, connection navigation and the
building/material list support inspection. Unknown geometry is marked explicitly.
Lift envelopes and dynamic floor-hole middle meshes are not complete game housing
or cap meshes; the preview is not a paste test.

Offline stack/fluid reports and native investigation artifacts are retained at
`/home/dannyb/satisfactory-tests/stackable-production/`. The Mk3 examples include
Plastic 10/min and 20/min (one refinery each, retaining heavy-oil residue) and
Reinforced Iron Plate 10/min (fourteen machines across three mixed-recipe floors).
These are measured examples, not general fit guarantees.

Binary/physical validation does not replace an in-game paste test. Native
length/curvature/fluid-identity checks are distinguished from project-owned
collision, connectivity, stack and hydraulic checks. Partial clearance and
dynamic cap-mesh coverage remain explicitly reported; a clean report does not
establish that unread game rules passed.

The section cutover also corrects objective export rates when another recipe
consumes the same item: the CSV's gross item flow is not exported a second time.
For example, wire plus cable exports only the wire left after cable production.

Historical manifold measurements remain in
[docs/sfy-layout-model.md](docs/sfy-layout-model.md); they describe the retired
layout, not current section spacing or fit guarantees.

The committed data files mean neither the tests nor a build need a game install.
[docs/sfy-regenerating-game-data.md](docs/sfy-regenerating-game-data.md) is the runbook for
regenerating them after a game update, in order, with the prerequisites each step needs.

### Satisfactory in-game verification

**No Satisfactory in-game load, paste or throughput verification has been performed.**
Offline checks and the browser preview are not game-acceptance evidence. The local
verification pack at `/home/dannyb/satisfactory-tests/in-game-verification/` contains
matched pairs, `SHA256SUMS`, `manifest.json`, `EXPECTATIONS.txt`, a concrete
`CHECKLIST.txt` and an unfilled `RESULTS.template.json`; its status is explicitly
`NOT_RUN` until a human records game results.

Use a backed-up test save/session, not a live factory. Save one disposable blueprint
in-game to identify that session's actual blueprint folder; on Windows the usual
root is `%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\blueprints\<session name>\`.
With the game closed, copy each `.sbp` with its same-stem `.sbpcfg`, never overwrite
an existing pair, then reload the session. Load it in the specified Blueprint
Designer mark and separately select the original blueprint in the build menu for
placement on a recorded, flat, unobstructed test site. Record those two outcomes
separately, including any missing unlocks or construction resources.

Compare machine counts, recipes, clocks, floors, splitters/mergers and both ends
of every lift with the pack's expectations. Supply every listed boundary input,
connect the power network, leave all outputs unblocked, let buffers settle, then
count each output over a timed interval. Preserve exact error text, game build,
mods/settings, location/orientation, screenshots and the original pair's hashes.
Before repairing suspect connections, capture the first stall. Re-save from the
Designer under a new name and return both game-written files alongside the
untouched originals; never replace the failing control. The DSP location and
paste protocol in `docs/IN_GAME_TESTING.md` do not apply to Satisfactory.

## Development

Run the Python and web gates relevant to the files changed:

```bash
uv run pytest
uv run ruff check .
uv run mypy

cd web
bun install --frozen-lockfile
bun run typecheck
bun run lint
bun run test
```

### Runtime verification

The Satisfactory corpus audit exercises `sections` and reports flow-pinning status:

```bash
uv run python scripts/sfy_audit.py --strategy sections
```

The inherited DSP audit below is separate. Unit tests do not establish that
every real factory fits within a time-limited solver budget:

```bash
uv run python scripts/audit.py                  # every tier, both strategies, exits non-zero if not
uv run python scripts/audit.py --tier mid       # quicker
uv run python scripts/audit.py --budget 1,4,15  # sweep the solver budget
```

The budget sweep matters: CP-SAT is time-limited and multi-worker, so "clean at 4s" is not "clean".

```bash
uv run python scripts/ab_compare.py --tier mid --repeat 3   # which strategy is denser
```

Both refuse to score a layout the validator rejected. Invalid layouts are systematically *smaller* —
an unrouted net is a belt run that does not exist — so scoring them rewards dropping connections.
