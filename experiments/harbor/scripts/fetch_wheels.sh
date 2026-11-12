#!/bin/bash
# Download the pinned wheels once on the host and copy them into each task's build context.
# The task images then install pytest with no network (pip --no-index --require-hashes).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WHEELS="${HERE}/../.wheels"
mkdir -p "$WHEELS"
pip download --no-cache-dir -q --only-binary=:all: --python-version 3.12 \
  --platform manylinux2014_x86_64 --require-hashes -r "$HERE/requirements-env.txt" -d "$WHEELS"
for t in "$HERE"/../tasks/*/environment; do
  mkdir -p "$t/wheels"
  cp "$WHEELS"/*.whl "$t/wheels/"
  cp "$HERE/requirements-env.txt" "$t/requirements-env.txt"
done
echo "wheels copied into: $(ls -d "$HERE"/../tasks/*/environment | xargs -n1 dirname | xargs -n1 basename | tr '\n' ' ')"
