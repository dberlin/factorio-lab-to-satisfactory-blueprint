# Satisfactory M3 corpus audit, 2026-09-15

**Gate: PASS** -- 7 CLEAN, 29 expected REFUSED, 0 other (0 unruled refusal, 0 INVALID, 0 CRASH, 0 NOT RUN) over 36 cells; 0 off their pin; 0 unpinned.

* head: `fdd54806` (working tree dirty)
* budget: 15 s per cell; marks: mk1, mk2, mk3
* strategies: manifold-rows
* command: `uv run python scripts/sfy_audit.py --budget 15 --strategy manifold-rows`
* CPU load while timing: 3.2 mean runnable procs

A cell passes the gate when it is CLEAN, or REFUSED with a cause one of
the plan's R-rulings allows: `rows exceed the designer depth`, `rows exceed the designer width`, `row too deep`, `run exceeds the belt ceiling`, `corridor needs a bridge that does not fit`, `fluids are M5`, `a belt could not be routed`, `the packer found no arrangement`.
`--strict` also asks whether the cell still does what the corpus pins it
to do, which makes the same run a regression pin.

## Every cell

| strategy | entry | mark | winner | verdict | cause or checks | pinned | volume cm³ | belt cm | lifts | attachments | rounds | turns by kind | machines | belts | poles | wires | s |
| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| manifold-rows | iron-plate-60 | mk1 | manifold-rows | CLEAN | -- | as pinned | 7.5072e+09 | 7382 | 0 | 8 | -- | 2 attachment | 6 | 17 | 2 | 7 | 6.25 |
| manifold-rows | iron-plate-60 | mk2 | manifold-rows | CLEAN | -- | as pinned | 9.384e+09 | 8182 | 0 | 8 | -- | 2 attachment | 6 | 17 | 2 | 7 | 0.24 |
| manifold-rows | iron-plate-60 | mk3 | manifold-rows | CLEAN | -- | as pinned | 1.12608e+10 | 8982 | 0 | 8 | -- | 2 attachment | 6 | 17 | 2 | 7 | 0.29 |
| manifold-rows | iron-rod-60 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | iron-rod-60 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | iron-rod-60 | mk3 | manifold-rows | CLEAN | -- | as pinned | 1.7136e+10 | 14940 | 0 | 16 | -- | 4 attachment | 6 | 27 | 2 | 7 | 0.43 |
| manifold-rows | concrete-60 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.00 |
| manifold-rows | concrete-60 | mk2 | manifold-rows | CLEAN | -- | as pinned | 4.704e+09 | 10260 | 0 | 12 | -- | 2 attachment | 4 | 20 | 2 | 5 | 0.29 |
| manifold-rows | concrete-60 | mk3 | manifold-rows | CLEAN | -- | as pinned | 1.08288e+10 | 10540 | 0 | 10 | -- | 2 attachment | 4 | 18 | 1 | 4 | 0.31 |
| manifold-rows | screw-120 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | screw-120 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | screw-120 | mk3 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | wire-120-cable-60 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | wire-120-cable-60 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | wire-120-cable-60 | mk3 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | steel-beam-20 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | steel-beam-20 | mk2 | -- | REFUSED | corridor needs a bridge that does not fit | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | steel-beam-20 | mk3 | manifold-rows | CLEAN | -- | as pinned | 1.3392e+10 | 13951.8 | 0 | 15 | -- | 5 attachment | 4 | 24 | 2 | 5 | 0.41 |
| manifold-rows | rotor-10 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.02 |
| manifold-rows | rotor-10 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.02 |
| manifold-rows | rotor-10 | mk3 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | reinforced-iron-plate-10 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | reinforced-iron-plate-10 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | reinforced-iron-plate-10 | mk3 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | modular-frame-5 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.02 |
| manifold-rows | modular-frame-5 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.02 |
| manifold-rows | modular-frame-5 | mk3 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.01 |
| manifold-rows | smart-plating-5 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.02 |
| manifold-rows | smart-plating-5 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.02 |
| manifold-rows | smart-plating-5 | mk3 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.02 |
| manifold-rows | heavy-modular-frame-2 | mk1 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.06 |
| manifold-rows | heavy-modular-frame-2 | mk2 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.05 |
| manifold-rows | heavy-modular-frame-2 | mk3 | -- | REFUSED | rows exceed the designer depth | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.04 |
| manifold-rows | plastic-20 | mk1 | -- | REFUSED | fluids are M5 | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.00 |
| manifold-rows | plastic-20 | mk2 | -- | REFUSED | fluids are M5 | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.00 |
| manifold-rows | plastic-20 | mk3 | -- | REFUSED | fluids are M5 | as pinned | -- | -- | -- | -- | -- | -- | 0 | 0 | 0 | 0 | 0.00 |

## what refuses and why

### `corridor needs a bridge that does not fit` -- 1 cells

* `manifold-rows/steel-beam-20` in mk2: manifold-rows/steel-beam*20: corridor needs a bridge that does not fit

### `fluids are M5` -- 3 cells

* `manifold-rows/plastic-20` in mk1: no further detail
* `manifold-rows/plastic-20` in mk2: no further detail
* `manifold-rows/plastic-20` in mk3: no further detail

### `rows exceed the designer depth` -- 25 cells

* `manifold-rows/iron-rod-60` in mk1: manifold-rows/iron-rod*60: rows exceed the designer depth
* `manifold-rows/iron-rod-60` in mk2: manifold-rows/iron-rod*60: rows exceed the designer depth
* `manifold-rows/concrete-60` in mk1: manifold-rows/concrete*60: rows exceed the designer depth
* `manifold-rows/screw-120` in mk1: manifold-rows/screw*120: rows exceed the designer depth
* `manifold-rows/screw-120` in mk2: manifold-rows/screw*120: rows exceed the designer depth
* `manifold-rows/screw-120` in mk3: manifold-rows/screw*120: rows exceed the designer depth
* `manifold-rows/wire-120-cable-60` in mk1: manifold-rows/wire*120 + cable*60: rows exceed the designer depth
* `manifold-rows/wire-120-cable-60` in mk2: manifold-rows/wire*120 + cable*60: rows exceed the designer depth
* `manifold-rows/wire-120-cable-60` in mk3: manifold-rows/wire*120 + cable*60: rows exceed the designer depth
* `manifold-rows/steel-beam-20` in mk1: manifold-rows/steel-beam*20: rows exceed the designer depth
* `manifold-rows/rotor-10` in mk1: manifold-rows/rotor*10: rows exceed the designer depth
* `manifold-rows/rotor-10` in mk2: manifold-rows/rotor*10: rows exceed the designer depth
* `manifold-rows/rotor-10` in mk3: manifold-rows/rotor*10: rows exceed the designer depth
* `manifold-rows/reinforced-iron-plate-10` in mk1: manifold-rows/reinforced-iron-plate*10: rows exceed the designer depth
* `manifold-rows/reinforced-iron-plate-10` in mk2: manifold-rows/reinforced-iron-plate*10: rows exceed the designer depth
* `manifold-rows/reinforced-iron-plate-10` in mk3: manifold-rows/reinforced-iron-plate*10: rows exceed the designer depth
* `manifold-rows/modular-frame-5` in mk1: manifold-rows/modular-frame*5: rows exceed the designer depth
* `manifold-rows/modular-frame-5` in mk2: manifold-rows/modular-frame*5: rows exceed the designer depth
* `manifold-rows/modular-frame-5` in mk3: manifold-rows/modular-frame*5: rows exceed the designer depth
* `manifold-rows/smart-plating-5` in mk1: manifold-rows/smart-plating*5: rows exceed the designer depth
* `manifold-rows/smart-plating-5` in mk2: manifold-rows/smart-plating*5: rows exceed the designer depth
* `manifold-rows/smart-plating-5` in mk3: manifold-rows/smart-plating*5: rows exceed the designer depth
* `manifold-rows/heavy-modular-frame-2` in mk1: manifold-rows/heavy-modular-frame*2: rows exceed the designer depth
* `manifold-rows/heavy-modular-frame-2` in mk2: manifold-rows/heavy-modular-frame*2: rows exceed the designer depth
* `manifold-rows/heavy-modular-frame-2` in mk3: manifold-rows/heavy-modular-frame*2: rows exceed the designer depth

## What moved off its pin

Nothing: no measured pin changed; unpinned cells are not comparisons.

