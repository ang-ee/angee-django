#!/bin/bash
# Template matrix probe: render every cell, then verify the classes of defect
# that only surface in real use — nonexistent registry images and dead git sources.
# Renders with the installed operator from REGISTRY.
set -uo pipefail
REGISTRY="${1:-${ANGEE_TEMPLATE_REGISTRY:?Pass a registry path or set ANGEE_TEMPLATE_REGISTRY}}"
OUT=$(mktemp -d "${TMPDIR:-/tmp}/matrix-probe.XXXXXX") || exit 1
trap 'rm -rf -- "$OUT"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
FAIL=0
note() { echo "  $1"; }
bad() { echo "  ✗ $1"; FAIL=1; }

render() { # name template inputs...
  local name="$1" tpl="$2"; shift 2
  local dir="$OUT/$name"; mkdir -p "$dir"
  local args=(); for i in "$@"; do args+=(--input "$i"); done
  if ! ANGEE_TEMPLATE_REGISTRY="$REGISTRY" angee init "$dir" -t "$tpl" --yes --force "${args[@]}" >/dev/null 2>"$OUT/$name.err"; then
    bad "$name: render FAILED: $(tail -1 "$OUT/$name.err")"; return 1
  fi
}

echo "== render matrix =="
CELLS=(
  "dev-process-base|dev|"
  "dev-process-full|dev|addons_profile=full"
  "dev-docker-base|dev|runtime_mode=docker"
  "dev-docker-full|dev|runtime_mode=docker addons_profile=full"
  "dev-docker-host|dev|runtime_mode=docker ingress_domain=demo.example.com"
  "local-source|local|instance_name=probe framework=source"
  "local-baked|local|instance_name=probe framework=baked"
)
for cell in "${CELLS[@]}"; do
  IFS='|' read -r name tpl inputs <<<"$cell"
  if render "$name" "$tpl" $inputs; then note "✓ $name rendered"; fi
done

echo "== registry images exist =="
IMAGES=$(grep -rh "image:" "$OUT"/*/angee.yaml 2>/dev/null | awk '{print $2}' | sort -u)
for img in $IMAGES; do
  if docker manifest inspect "$img" >/dev/null 2>&1; then note "✓ $img"
  else bad "$img: NOT FOUND on registry (or auth-gated for anonymous pulls)"; fi
done

echo "== git sources resolve =="
REPOS=$(grep -rh "repo: https" "$OUT"/*/angee.yaml 2>/dev/null | awk '{print $2}' | sort -u)
for r in $REPOS; do
  if git ls-remote --heads "$r" >/dev/null 2>&1; then note "✓ $r"
  else bad "$r: unreachable"; fi
done

[ "$FAIL" = 0 ] && echo "MATRIX PROBE: ALL GREEN" || echo "MATRIX PROBE: FAILURES ABOVE"
exit $FAIL
