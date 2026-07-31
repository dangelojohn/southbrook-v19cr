#!/usr/bin/env bash
# scripts/prod_canary.sh
#
# WHY THIS EXISTS
# ----------------
# Two Claude sessions deploy to the same production Odoo (QNAP) and, on the
# night of 2026-07-30/31, stomped each other's work TWICE:
#   - One deploy overwrote/erased a live feature's server code outright
#     (only caught because someone happened to grep for a signature symbol
#     and it came back empty).
#   - A second incident left prod with files newer than the DB's registered
#     module version (deploy ran without -u, or a later manual edit/copy
#     silently overwrote what -u had just installed) — invisible unless you
#     compare the manifest file version against ir_module_module.
#
# This script is the guard BOTH sessions run before touching prod, so that
# "did I just erase something" and "did the last deploy actually register"
# stop being tribal knowledge and start being one command.
#
# WHEN TO RUN
# -----------
#   BEFORE any deploy  — establish/confirm the known-good baseline.
#   AFTER  any deploy   — prove the deploy didn't regress a signature or
#                          leave prod in a files-changed-but-not-upgraded
#                          state.
#
# THE RULE
# --------
#   Deploy only from pushed main. If this script FAILs after a deploy, do
#   NOT deploy again to "fix" it blind — stop, diagnose, and coordinate with
#   the other session before touching prod again.
#
# WHAT THIS SCRIPT DOES
# ----------------------
#   1. One SSH round-trip greps known signature symbols out of known-live
#      files on prod (addons root) to prove they're still there.
#   2. The SAME round-trip also reads each module's manifest "version" on
#      prod and queries ir_module_module.latest_version from Postgres, so a
#      file/DB mismatch (deploy without -u, or overwrite after -u) is caught.
#   3. Optionally (arg 1 = a local repo path, default /Users/naadmin/southbrook-v19cr,
#      expected to be the pushed-main checkout) also diffs each prod manifest
#      version against the local one and WARNs on mismatch — this is
#      informational only (local may legitimately be ahead of prod between
#      merge and deploy) and never causes a FAIL / non-zero exit by itself.
#
# This script is strictly READ-ONLY. It never writes to prod, never runs -u,
# never touches the database beyond a SELECT. Safe to run as often as you like.
#
# Usage:
#   ./scripts/prod_canary.sh                       # local compare against /Users/naadmin/southbrook-v19cr
#   ./scripts/prod_canary.sh /path/to/local/main    # local compare against a different checkout
#   ./scripts/prod_canary.sh ""                     # skip local compare entirely
#
# Exit:
#   0 = "CANARY: OK"        (zero FAILs; WARNs are still worth reading)
#   1 = "CANARY: N FAILURES"

set -u

SSH_HOST="qnap-tunnel"
PROD_ADDONS_ROOT="/share/CACHEDEV3_DATA/Container/southbrook/addons"
LOCAL_MAIN="${1-/Users/naadmin/southbrook-v19cr}"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

report() {
    # $1=PASS|FAIL|WARN  $2=message
    local status="$1"; shift
    echo "${status}: $*"
    case "$status" in
        PASS) PASS_COUNT=$((PASS_COUNT + 1)) ;;
        FAIL) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
        WARN) WARN_COUNT=$((WARN_COUNT + 1)) ;;
    esac
}

# ---------------------------------------------------------------------------
# 1) Single SSH round-trip: signature greps + manifest versions + DB versions.
#    Emits parseable "KEY:value" lines; everything else is parsed locally.
# ---------------------------------------------------------------------------
REMOTE_OUT=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_HOST" bash -s <<'REMOTE_EOF'
set -u
ROOT=/share/CACHEDEV3_DATA/Container/southbrook/addons

sig() { # $1=file $2=pattern -> prints count, or NOFILE if file missing
    if [ -f "$1" ]; then
        grep -c -- "$2" "$1" 2>/dev/null || echo 0
    else
        echo "NOFILE"
    fi
}

echo "SIG:premium_orch_skj_board_intel:$(sig "$ROOT/southbrook_premium_orchestration/models/project_task.py" 'skj_board_intel')"

if [ -f "$ROOT/southbrook_premium_orchestration/static/src/js/kitchen_jobs_kanban.js" ]; then
    echo "SIG:premium_orch_kanban_js_exists:1"
else
    echo "SIG:premium_orch_kanban_js_exists:0"
fi

echo "SIG:project_mrp_group_expand:$(sig "$ROOT/southbrook_project_mrp/models/project_task.py" 'group_expand=True')"
echo "SIG:kitchen_tools_not_assessed:$(sig "$ROOT/southbrook_mrp_kitchen_tools/models/product_template.py" 'not_assessed')"
echo "SIG:kitchen_tools_dq_action:$(sig "$ROOT/southbrook_mrp_kitchen_tools/views/dq_views.xml" 'action_dq_safety_assessment')"
echo "SIG:materials_hw_selection_flag:$(sig "$ROOT/sb_materials_hardware/models/tools_catalog_provider.py" '_selection_flag_values')"
echo "SIG:materials_hw_search_note:$(sig "$ROOT/sb_materials_hardware/models/tools_catalog_provider.py" 'search_note')"

for m in southbrook_premium_orchestration southbrook_project_mrp southbrook_mrp_kitchen_tools sb_materials_hardware; do
    f="$ROOT/$m/__manifest__.py"
    if [ -f "$f" ]; then
        v=$(grep -i "version" "$f" | grep -oE '19\.0\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        echo "MANIFEST:$m:${v:-UNKNOWN}"
    else
        echo "MANIFEST:$m:NOFILE"
    fi
done

export DOCKER_HOST=unix:///var/run/system-docker.sock
DOCKER=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/docker
Q="SELECT name || '=' || COALESCE(latest_version,'NULL') FROM ir_module_module WHERE name IN ('southbrook_premium_orchestration','southbrook_project_mrp','southbrook_mrp_kitchen_tools','sb_materials_hardware') ORDER BY name;"
DBOUT=$("$DOCKER" exec southbrook-postgres psql -U odoo -d southbrook -Atc "$Q" 2>&1)
if [ $? -ne 0 ]; then
    echo "DBERROR:$DBOUT"
else
    echo "$DBOUT" | while IFS='=' read -r n v; do
        [ -n "$n" ] && echo "DBVER:$n:$v"
    done
fi
REMOTE_EOF
)
SSH_STATUS=$?

if [ "$SSH_STATUS" -ne 0 ] || [ -z "$REMOTE_OUT" ]; then
    report FAIL "could not reach prod via 'ssh $SSH_HOST' (exit $SSH_STATUS) — canary cannot run"
    echo
    echo "CANARY: $FAIL_COUNT FAILURES"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2) Parse signature checks.
# ---------------------------------------------------------------------------
sig_value() { # $1=key
    echo "$REMOTE_OUT" | grep "^SIG:$1:" | head -1 | cut -d: -f3
}

check_sig() { # $1=key $2=description
    local key="$1" desc="$2" val
    val=$(sig_value "$key")
    if [ "$val" = "NOFILE" ] || [ -z "$val" ]; then
        report FAIL "$desc — file missing on prod"
    elif [ "$val" -gt 0 ] 2>/dev/null; then
        report PASS "$desc (matches=$val)"
    else
        report FAIL "$desc — signature symbol NOT FOUND on prod (matches=0)"
    fi
}

check_sig "premium_orch_skj_board_intel" "southbrook_premium_orchestration/models/project_task.py contains skj_board_intel"
check_sig "premium_orch_kanban_js_exists" "southbrook_premium_orchestration/static/src/js/kitchen_jobs_kanban.js exists"
check_sig "project_mrp_group_expand" "southbrook_project_mrp/models/project_task.py contains group_expand=True"
check_sig "kitchen_tools_not_assessed" "southbrook_mrp_kitchen_tools/models/product_template.py contains not_assessed"
check_sig "kitchen_tools_dq_action" "southbrook_mrp_kitchen_tools/views/dq_views.xml contains action_dq_safety_assessment"
check_sig "materials_hw_selection_flag" "sb_materials_hardware/models/tools_catalog_provider.py contains _selection_flag_values"
check_sig "materials_hw_search_note" "sb_materials_hardware/models/tools_catalog_provider.py contains search_note"

# ---------------------------------------------------------------------------
# 3) Parse version checks: prod manifest file version vs DB latest_version.
# ---------------------------------------------------------------------------
DBERROR=$(echo "$REMOTE_OUT" | grep "^DBERROR:" | head -1)
if [ -n "$DBERROR" ]; then
    report FAIL "could not query southbrook-postgres on prod: ${DBERROR#DBERROR:}"
fi

MODULES="southbrook_premium_orchestration southbrook_project_mrp southbrook_mrp_kitchen_tools sb_materials_hardware"

manifest_version() { # $1=module
    echo "$REMOTE_OUT" | grep "^MANIFEST:$1:" | head -1 | cut -d: -f3
}
db_version() { # $1=module
    echo "$REMOTE_OUT" | grep "^DBVER:$1:" | head -1 | cut -d: -f3
}

for m in $MODULES; do
    fv=$(manifest_version "$m")
    dv=$(db_version "$m")

    if [ -z "$fv" ] || [ "$fv" = "NOFILE" ] || [ "$fv" = "UNKNOWN" ]; then
        report FAIL "$m: could not read manifest version on prod (got '${fv:-empty}')"
        continue
    fi
    if [ -z "$dv" ] || [ "$dv" = "NULL" ]; then
        report FAIL "$m: no ir_module_module.latest_version row on prod (got '${dv:-empty}')"
        continue
    fi

    if [ "$fv" = "$dv" ]; then
        report PASS "$m: registered version matches file version ($dv)"
    else
        report FAIL "$m: registered version != file version (deploy ran without -u, or files overwritten after -u) — file=$fv db=$dv"
    fi
done

# ---------------------------------------------------------------------------
# 4) Optional: prod manifest version vs LOCAL main checkout version (WARN only).
# ---------------------------------------------------------------------------
if [ -n "$LOCAL_MAIN" ]; then
    if [ ! -d "$LOCAL_MAIN" ]; then
        report WARN "local compare skipped — '$LOCAL_MAIN' is not a directory"
    else
        for m in $MODULES; do
            fv=$(manifest_version "$m")
            local_manifest="$LOCAL_MAIN/addons/$m/__manifest__.py"
            if [ ! -f "$local_manifest" ]; then
                report WARN "$m: no local manifest at $local_manifest — skipped local compare"
                continue
            fi
            lv=$(grep -i "version" "$local_manifest" | grep -oE '19\.0\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
            if [ -z "$lv" ]; then
                report WARN "$m: could not read local manifest version at $local_manifest"
            elif [ -n "$fv" ] && [ "$fv" != "NOFILE" ] && [ "$fv" != "UNKNOWN" ] && [ "$fv" != "$lv" ]; then
                report WARN "$m: prod differs from local main — check who deployed what (prod=$fv local=$lv)"
            fi
        done
    fi
else
    :  # LOCAL_MAIN explicitly empty ("") — local compare intentionally skipped
fi

# ---------------------------------------------------------------------------
# 5) Summary.
# ---------------------------------------------------------------------------
echo
echo "----------------------------------------------------------------------"
echo "PASS=$PASS_COUNT  FAIL=$FAIL_COUNT  WARN=$WARN_COUNT"
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "CANARY: OK"
    exit 0
else
    echo "CANARY: $FAIL_COUNT FAILURES"
    exit 1
fi
