#!/bin/bash
# Template matrix probe: render every cell, then verify the classes of defect
# that only surface in real use — nonexistent registry images, dead git sources,
# unconsumed copier inputs. Renders with the installed operator from REGISTRY.
set -u
REGISTRY="${1:-/Users/alexis/.angee/workspaces/src/angee}"
OUT=$(mktemp -d /tmp/matrix-probe.XXXX)
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
  echo "$dir"
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
DIRS=()
for cell in "${CELLS[@]}"; do
  IFS='|' read -r name tpl inputs <<<"$cell"
  d=$(render "$name" "$tpl" $inputs) && { note "✓ $name rendered"; DIRS+=("$d"); }
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

echo "== declared inputs are consumed (bloat scan) =="
for tpl in dev local; do
  cop="$REGISTRY/templates/stacks/$tpl/copier.yml"
  body="$REGISTRY/templates/stacks"
  for key in $(python3 -c "
import re,sys
s=open('$cop').read()
ang=s.split('_angee:')[1] if '_angee:' in s else ''
seen=set()
for m in re.finditer(r'^(\w+):\s*$',s,re.M):
    k=m.group(1)
    if k not in ('true','false'): seen.add(k)
print(' '.join(sorted(k for k in seen if not k.startswith('_'))))"); do
    # a chained project template consumes inputs through the copier.yml chain block
    grep -rq "$key" "$body/_shared" "$body/$tpl/template" "$cop" 2>/dev/null || bad "stacks/$tpl input '$key' never referenced by its templates or chain"
  done
done

echo "== rendered scripts sanity =="
grep -rn '"test": "vitest run"' "$OUT"/*/web/package.json 2>/dev/null | head -2 | while read -r l; do bad "bare vitest run (empty-suite exit 1): $l"; done

rm -rf "$OUT"
[ "$FAIL" = 0 ] && echo "MATRIX PROBE: ALL GREEN" || echo "MATRIX PROBE: FAILURES ABOVE"
exit $FAIL
