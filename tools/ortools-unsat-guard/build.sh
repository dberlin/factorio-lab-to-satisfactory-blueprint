#!/usr/bin/env bash
# Build the patched OR-Tools 9.15 Python wheel from source.
#
# Usage: build.sh <work-dir>
#
# Produces <work-dir>/or-tools/build/python/dist/ortools-9.15.6755-*.whl and
# <work-dir>/build.log. Needs cmake, ninja, gcc/g++, ccache (optional), uv and
# network access for the OR-Tools dependency fetch. Verified 2026-09-13 with
# gcc 16.2.1, cmake 4.4.3, ninja 1.13.2, Python 3.14.7; about 20 minutes on a
# 128-core box, 10 of them the dependency fetch.
#
# Three toolchain adjustments are baked in; none touch the OR-Tools source:
#   1. -DCMAKE_C_FLAGS="-include stdlib.h": current glibc declares once_flag
#      behind its C23 gate and clashes with SCIP's tinycthread macro.
#   2. The build venv's bin on PATH: protoc needs protoc-gen-mypy there.
#   3. SWIG pinned to 4.3.0: 4.5.0 dropped the PyInt_AsLong compatibility
#      macro that ortools/base/python-swig.h still uses.
set -euo pipefail
WORK="${1:?usage: build.sh <work-dir>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$HERE/unsat-callback-guard.patch"
mkdir -p "$WORK"
cd "$WORK"

if [ ! -d or-tools ]; then
  GIT_EDITOR=true git clone --depth 1 --branch v9.15 https://github.com/google/or-tools or-tools
fi
cd or-tools
if ! git apply --check --reverse "$PATCH" 2>/dev/null; then
  GIT_EDITOR=true git apply "$PATCH"
fi
cd "$WORK"

if [ ! -x venv-build/bin/python ]; then
  uv venv venv-build --python 3.14
  uv pip install --python venv-build/bin/python swig==4.3.0 absl-py numpy pandas protobuf \
    mypy-protobuf mypy setuptools wheel typing-extensions virtualenv
fi

# Keeps ortools.__version__ == 9.15.6755, the version the lockfile pins.
export OR_TOOLS_PATCH=6755
export PATH="$WORK/venv-build/bin:$PATH"
LAUNCHER=()
if command -v ccache >/dev/null; then
  LAUNCHER=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
fi
JOBS="${JOBS:-64}"

{
  echo "=== build start $(date -u +%FT%TZ) OR_TOOLS_PATCH=$OR_TOOLS_PATCH"
  gcc --version | head -1; cmake --version | head -1; ninja --version
  cd or-tools
  cmake -S. -Bbuild -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_DEPS=ON -DBUILD_PYTHON=ON \
    -DBUILD_SAMPLES=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF -DBUILD_VENV=OFF \
    -DPython3_EXECUTABLE="$WORK/venv-build/bin/python" -DSWIG_EXECUTABLE="$WORK/venv-build/bin/swig" \
    "${LAUNCHER[@]}" -DCMAKE_C_FLAGS="-include stdlib.h"
  cmake --build build -j "$JOBS" --target python_package
  echo "=== build end $(date -u +%FT%TZ)"
  ls -la build/python/dist/
} 2>&1 | tee "$WORK/build.log"
