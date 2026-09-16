# flab2bp web interface

The React and three.js front end for `flab2bp`. Paste a Satisfactory FactorioLab
URL, build with the Python planner, inspect the resulting factory in 3D and
download its `.sbp`/`.sbpcfg` pair. The inherited DSP viewer remains available.

The solver is not implemented in TypeScript. Development therefore requires both the Python
API and the Rsbuild development server; `bun run dev` starts and supervises both.

## Prerequisites

- [Bun](https://bun.sh) — the only JavaScript toolchain used here. No npm/yarn/pnpm.
- [uv](https://docs.astral.sh/uv/) — runs the Python extractor and its dependencies.
- **DSP viewing only:** extracted DSP assets, either from a local game install or
  a previously generated assets directory. Satisfactory uses the bundled Python
  registry and does not depend on these optional assets.

## Setup

From the repository root:

```sh
uv sync
cd web
bun install
```

For DSP viewing, `bun run extract-assets "/path/to/Dyson Sphere Program"` writes
`items.json`, `recipes.json`, `models.json` and an icon atlas into `public/assets/`.
Its output is cached and only needs regenerating after a game update.

## Run

For normal use, run the integrated server from the repository root:

```sh
uv run flab2bp-web
```

Open <http://127.0.0.1:8000>. The command builds the front end when necessary and serves both
the page and the Python API.

Paste a `/sfy/` FactorioLab URL, choose Mk.1/Mk.2/Mk.3 and press **Build**.
Without a supplied CSV, entering the URL enables automatic flow capture.
Successful builds appear automatically; both binary downloads and validation
notices stay in the build panel. The config description includes net rates,
recipes/clocks, estimated production power and required boost consumables.

You can also open/drop a `.sbp`. The server decodes its actual actors and
connections; a `.sbpcfg` alone has no scene geometry. The 3D view supports
orbit/pan/zoom, Q/E quarter-turns, O top-down, layer switches, machine
ghost/solid/hidden modes, picking and connection inspection. Expand the building
list for counts and serialized construction costs. Geometry is schematic:
unknown objects are diagnostic markers, lift envelopes are explicitly partial,
and no screenshot establishes in-game paste or throughput correctness.

For TypeScript development, run this from `web/`:

```sh
bun run dev
```

Open <http://127.0.0.1:3001>. `concurrently` starts `flab2bp-web --no-build` on port
8000 while `wait-on` holds Rsbuild until `/api/health` responds. Ctrl-C terminates both
process trees.

To expose the frontend on all interfaces while keeping the Python API bound to loopback, run:

```sh
bun run dev -- --host 0.0.0.0
```

Open `http://<development-machine>:3001`. Anyone who can reach that port can also reach the
proxied solver and unauthenticated `/api/fetch` relay, so use this only on a trusted network.

To use an API at a different origin, manage that API separately and run:

```sh
FLAB2BP_API=http://127.0.0.1:9000 bun run dev:frontend
```

A proxy error for `/api/build` means the configured Python API is not reachable.

## Scripts

| Script | What it does |
| `bun run dev` | Starts the Python API, waits for it, and supervises it with Rsbuild. |
| `bun run dev:frontend` | Starts only Rsbuild for an externally managed API. |
| `bun run build` | Production bundle into `dist/`. |
| `bun run test` | Rstest suite (`test:watch` for watch mode). |
| `bun run typecheck` | `tsc --noEmit`. |
| `bun run lint` | Oxlint and Oxfmt check. |
| `bun run format` | Oxfmt formatter, writing in place. |
| `bun run format:check` | Checks Oxfmt formatting without writing. |
| `bun run extract-assets` | Regenerates `public/assets/` from the game install. |

The development servers bind to `127.0.0.1` by default. Passing a different frontend host
also exposes its proxied `/api` routes; `/api/fetch` is an unauthenticated HTTP(S) relay.

## Architecture

The code is layered so that everything hard to get right can be tested without a renderer.
`src/format/` decodes the blueprint string — base64, gzip, the CSV header envelope, the MD5F
checksum and the binary building records — and `src/model/` turns that into catalog lookups,
a scene layout, a bill of materials and info-panel rows. Neither directory imports React or
three.js, and a guard test in `tests/architecture.test.ts` enforces it; that is what lets the
parser and the layout maths be covered by plain unit tests over real blueprint fixtures
instead of by rendering a canvas and squinting at it. `src/scene/` (react-three-fiber),
`src/ui/` (panels) and `src/state/` (React context, asset loading) sit on top and hold all
the framework-specific code. The Python server in `../src/flab2bp/web/` owns build,
artifact, scene, health and fetch endpoints. `sfy_scene.py` decodes raw Satisfactory
objects into the renderer-free schema in `src/api/satisfactory.ts`; its generated
and imported scenes share the same publication state and camera controls.
Rsbuild proxies `/api` to that server so development and production use the same endpoints.
