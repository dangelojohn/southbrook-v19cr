#!/usr/bin/env bash
# scripts/test_lint_design_tokens.sh — self-test for lint-design-tokens.py
#
# Verifies:
#   1. bad.scss (hardcoded status-color literals) is rejected
#   2. good.scss (comment-only literals / no status context / brand tokens)
#      passes cleanly
#   3. line numbers match the bad.scss fixture layout
#   4. the allowlist is seeded with both required entries, and the real
#      canonical token file is exempt regardless of its content
#   5. a fully-baselined violation PASSES by default, but is REPORTED
#      (with an exit-0 [baselined] line) under --all
#   6. a NEW violation in a file that already has a baselined violation
#      still FAILS, and the baselined one stays suppressed by default

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCAN="$ROOT/scripts/lint-design-tokens.py"
FIX_DIR="$ROOT/scripts/_test_fixtures/lint-design-tokens"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

EXPECTED_BAD=3

echo "=== Test 1: BAD fixture must be rejected with >= $EXPECTED_BAD violations ==="
EMPTY_BASELINE="$TMP/empty-baseline.txt"
: > "$EMPTY_BASELINE"
if "$SCAN" --baseline "$EMPTY_BASELINE" "$FIX_DIR/bad.scss" > "$TMP/bad.out" 2>&1; then
    echo "FAIL: scanner exited 0 on bad.scss — should have rejected"
    cat "$TMP/bad.out"
    exit 1
fi
BAD_COUNT=$(grep -c 'bad.scss:' "$TMP/bad.out" || true)
if [ "$BAD_COUNT" -lt "$EXPECTED_BAD" ]; then
    echo "FAIL: expected >= $EXPECTED_BAD violations in bad.scss, got $BAD_COUNT"
    cat "$TMP/bad.out"
    exit 1
fi
echo "OK: $BAD_COUNT violations flagged in bad.scss (>= $EXPECTED_BAD expected)"

echo
echo "=== Test 2: GOOD fixture must pass cleanly ==="
if ! "$SCAN" --baseline "$EMPTY_BASELINE" "$FIX_DIR/good.scss" > "$TMP/good.out" 2>&1; then
    echo "FAIL: scanner rejected good.scss — false positives:"
    cat "$TMP/good.out"
    exit 1
fi
echo "OK: good.scss accepted clean (comment-only literals, non-adjacent"
echo "    comments, and brand tokens with no status context all pass)"

echo
echo "=== Test 3: line numbers for known-bad cases (B01..B03) ==="
# bad.scss layout: violations on lines 4, 8, 13.
for expected_line in 4 8 13; do
    if ! grep -q "bad.scss:$expected_line:" "$TMP/bad.out"; then
        echo "FAIL: no violation reported at bad.scss:$expected_line"
        cat "$TMP/bad.out"
        exit 1
    fi
done
echo "OK: all expected line numbers (4, 8, 13) reported"

echo
echo "=== Test 4: actionable fix message names a token ==="
if ! grep -q 'fix: use var(--sb-status-' "$TMP/bad.out"; then
    echo "FAIL: violation output doesn't name a suggested token"
    cat "$TMP/bad.out"
    exit 1
fi
echo "OK: fix message names a --sb-status-* token to use instead"

echo
echo "=== Test 5: ALLOWLIST is seeded with both required entries ==="
if ! grep -q '"addons/southbrook_ui/static/src/scss/_sb_tokens.scss"' "$SCAN"; then
    echo "FAIL: DESIGN_TOKEN_ALLOWLIST missing _sb_tokens.scss entry"
    exit 1
fi
if ! grep -q '"addons/southbrook_estimating/static/src/scss/_southbrook_design_tokens.scss"' "$SCAN"; then
    echo "FAIL: DESIGN_TOKEN_ALLOWLIST missing _southbrook_design_tokens.scss entry"
    exit 1
fi
echo "OK: both required allowlist entries present in the script source"

REAL_TOKEN_FILE="$ROOT/addons/southbrook_estimating/static/src/scss/_southbrook_design_tokens.scss"
if [ -f "$REAL_TOKEN_FILE" ]; then
    if ! "$SCAN" --baseline "$EMPTY_BASELINE" "$REAL_TOKEN_FILE" > "$TMP/allow.out" 2>&1; then
        echo "FAIL: allowlisted _southbrook_design_tokens.scss was flagged despite the allowlist"
        cat "$TMP/allow.out"
        exit 1
    fi
    echo "OK: real allowlisted token file passes regardless of its literal content"
else
    echo "SKIP: real token file not present in this checkout"
fi

echo
echo "=== Test 6: a fully-baselined violation PASSES by default, is REPORTED under --all ==="
BASE1="$TMP/baseline1.txt"
printf 'scripts/_test_fixtures/lint-design-tokens/baselined_only.scss:4:#c62828\n' > "$BASE1"

if ! "$SCAN" --baseline "$BASE1" "$FIX_DIR/baselined_only.scss" > "$TMP/base_only.out" 2>&1; then
    echo "FAIL: fully-baselined file should PASS by default"
    cat "$TMP/base_only.out"
    exit 1
fi
if grep -q 'baselined_only.scss:4:' "$TMP/base_only.out"; then
    echo "FAIL: baselined violation should not be printed by default"
    cat "$TMP/base_only.out"
    exit 1
fi
echo "OK: baselined-only file passes silently by default"

# --all still exits 0 here (no NEW violations) but must print the baselined one.
if ! "$SCAN" --baseline "$BASE1" --all "$FIX_DIR/baselined_only.scss" > "$TMP/base_only_all.out" 2>&1; then
    echo "FAIL: --all should still exit 0 when there are no NEW violations"
    cat "$TMP/base_only_all.out"
    exit 1
fi
if ! grep -q 'baselined_only.scss:4:.*\[baselined\]' "$TMP/base_only_all.out"; then
    echo "FAIL: --all should report the baselined violation, tagged [baselined]"
    cat "$TMP/base_only_all.out"
    exit 1
fi
echo "OK: --all reports the baselined violation (tagged) without failing the build"

echo
echo "=== Test 7: a NEW violation in a file with an existing baselined one still FAILS ==="
BASE2="$TMP/baseline2.txt"
printf 'scripts/_test_fixtures/lint-design-tokens/baselined_plus_new.scss:4:#c92a2a\n' > "$BASE2"

if "$SCAN" --baseline "$BASE2" "$FIX_DIR/baselined_plus_new.scss" > "$TMP/base_new.out" 2>&1; then
    echo "FAIL: file with a NEW (non-baselined) violation should FAIL"
    cat "$TMP/base_new.out"
    exit 1
fi
if grep -q 'baselined_plus_new.scss:4:' "$TMP/base_new.out"; then
    echo "FAIL: the baselined violation (line 4) should stay suppressed by default"
    cat "$TMP/base_new.out"
    exit 1
fi
if ! grep -q 'baselined_plus_new.scss:8:' "$TMP/base_new.out"; then
    echo "FAIL: the NEW violation (line 8) should be reported"
    cat "$TMP/base_new.out"
    exit 1
fi
echo "OK: default mode fails on the new violation only, baselined one stays quiet"

if "$SCAN" --baseline "$BASE2" --all "$FIX_DIR/baselined_plus_new.scss" > "$TMP/base_new_all.out" 2>&1; then
    echo "FAIL: --all must still exit non-zero when a NEW violation is present"
    cat "$TMP/base_new_all.out"
    exit 1
fi
if ! grep -q 'baselined_plus_new.scss:4:.*\[baselined\]' "$TMP/base_new_all.out"; then
    echo "FAIL: --all should also show the baselined violation, tagged"
    cat "$TMP/base_new_all.out"
    exit 1
fi
if ! grep -q 'baselined_plus_new.scss:8:' "$TMP/base_new_all.out"; then
    echo "FAIL: --all should still show the new violation"
    cat "$TMP/base_new_all.out"
    exit 1
fi
echo "OK: --all shows both, exit code still reflects the new violation"

echo
echo "=== Test 8: --update-baseline regenerates a baseline file and exits 0 ==="
UPDATE_TARGET="$TMP/update-baseline.txt"
if ! "$SCAN" --baseline "$UPDATE_TARGET" --update-baseline "$FIX_DIR/bad.scss" > "$TMP/update.out" 2>&1; then
    echo "FAIL: --update-baseline should exit 0"
    cat "$TMP/update.out"
    exit 1
fi
if [ ! -s "$UPDATE_TARGET" ]; then
    echo "FAIL: --update-baseline did not write a non-empty baseline file"
    exit 1
fi
UPDATE_COUNT=$(wc -l < "$UPDATE_TARGET" | tr -d ' ')
if [ "$UPDATE_COUNT" -lt "$EXPECTED_BAD" ]; then
    echo "FAIL: expected >= $EXPECTED_BAD baseline entries, got $UPDATE_COUNT"
    cat "$UPDATE_TARGET"
    exit 1
fi
# Running again against the freshly-generated baseline must now PASS.
if ! "$SCAN" --baseline "$UPDATE_TARGET" "$FIX_DIR/bad.scss" > "$TMP/update2.out" 2>&1; then
    echo "FAIL: re-running against the just-generated baseline should PASS"
    cat "$TMP/update2.out"
    exit 1
fi
echo "OK: --update-baseline writes $UPDATE_COUNT entries; re-run against them passes"

echo
echo "All design-token lint self-tests passed."
