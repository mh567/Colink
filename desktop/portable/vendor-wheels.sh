#!/usr/bin/env bash
# Build native wheel caches for OCTOP_GREEN_OFFLINE=1 packaging.
#
# Usage:
#   bash desktop/portable/vendor-wheels.sh              # host platform
#   bash desktop/portable/vendor-wheels.sh windows-amd64  # on Windows x86_64
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=desktop/portable/_common.sh
source "${REPO_ROOT}/desktop/portable/_common.sh"

require_uv

vendor_one() {
  local plat="$1"
  local wheel_dir req_file override_file pins_file
  if [[ "$plat" != "$(host_plat)" ]]; then
    echo "[wheels] use a native ${plat} builder; cross-platform wheel prefetch is unsupported" >&2
    return 1
  fi
  wheel_dir="${GREEN_WHEELS}/${plat}"
  mkdir -p "$wheel_dir" "$GREEN_CACHE"

  local ocr_extra=()
  if supports_local_ocr "$plat"; then
    ocr_extra+=( --extra local-ocr )
  fi

  req_file="${GREEN_ROOT}/requirements-${plat}.txt"
  echo "[wheels] ${plat}: exporting frozen deps → ${req_file}" >&2
  uv export --project "$REPO_ROOT" --frozen --no-dev "${ocr_extra[@]+"${ocr_extra[@]}"}" --no-emit-project --no-hashes -o "$req_file" >/dev/null
  # Keep offline bundles equipped with the same online updater as package.sh.
  echo "uv==${GREEN_UV_VERSION}" >> "$req_file"

  override_file="$(write_green_overrides "$plat")"
  local extra=()
  if [[ -n "$override_file" ]]; then
    extra+=( --overrides "$override_file" )
  fi

  # Apply replacements before pip constraints: constraints cannot override an
  # incompatible frozen pin or exclude Windows ARM packages by false markers.
  pins_file="${GREEN_CACHE}/wheel-pins-${plat}.txt"
  uv pip compile "$req_file" --no-deps --python 3.12 \
    "${extra[@]+"${extra[@]}"}" --output-file "$pins_file" >/dev/null

  echo "[wheels] ${plat}: building → ${wheel_dir}" >&2
  # pip is an isolated build tool, not a product dependency. Source-only packages
  # such as antlr4-python3-runtime need wheels before the disconnected install.
  uv tool run --python 3.12 --from pip==25.3 pip wheel \
    --wheel-dir "$wheel_dir" \
    --no-deps \
    --only-binary cryptography \
    --constraint "$pins_file" \
    -r "$pins_file"

  echo "[wheels] ${plat}: done ($(find "$wheel_dir" -maxdepth 1 -type f -name '*.whl' | wc -l | tr -d ' ') wheels)"
}

main() {
  local plat="${1:-}"
  if [[ -z "$plat" ]]; then
    plat="$(host_plat)"
  fi
  if ! is_known_plat "$plat"; then
    echo "unknown platform: $plat (known: ${ALL_PLATS[*]})" >&2
    exit 1
  fi
  vendor_one "$plat"
}

main "${1:-}"
