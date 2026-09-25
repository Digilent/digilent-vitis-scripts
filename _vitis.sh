#!/usr/bin/env bash
# Locates a Vitis install (any version) with no dependency on env vars or
# PATH, finds its bundled python interpreter, and (optionally) runs a
# python script (checkin.py/checkout.py/...) with it -- meant as a
# `vitis -s <script>` counterpart that also picks the Vitis version and
# does not require `vitis` to already be reachable from PATH -- or stops
# dangling vitis/eclipse/java processes left holding workspace file locks.
#
# Mirrors misc.py's findVitisRoot/findVitisPython/vitisPythonPathEntries/
# stopDanglingVitisProcesses, as a python-free bootstrap for the same
# Linux search rules (common install bases x {AMDDesignTools, Xilinx}).
#
# Usage: _vitis.sh -v <version> [-s <script.py> [script args...]] [--stop-dangling] [-i <install-path>]
# Example: ./_vitis.sh -v 2025.2
#          ./_vitis.sh -v 2025.2 --stop-dangling
#          ./_vitis.sh -v 2025.2 -s ./checkout.py
#          ./_vitis.sh -v 2025.2 -s ./checkout.py --platform my_platform

set -euo pipefail

ROOT_DIR_NAMES=(AMDDesignTools Xilinx)
PROC_NAMES=(vitis vitis-server eclipse java)

usage() {
    echo "Usage: $0 -v <version> [-s <script.py>] [--stop-dangling] [-i <install-path>]" >&2
    exit 1
}

VERSION=""
SCRIPT=""
INSTALL_PATH=""
STOP_DANGLING=0
SCRIPT_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        -v) VERSION="$2"; shift 2 ;;
        -s) SCRIPT="$2"; shift 2 ;;
        -i) INSTALL_PATH="$2"; shift 2 ;;
        --stop-dangling) STOP_DANGLING=1; shift ;;
        # Anything else is forwarded as-is to SCRIPT (e.g. checkout.py's own
        # --platform/--app selective-rebuild flags), not silently dropped.
        *) SCRIPT_ARGS+=("$1"); shift ;;
    esac
done

[ -n "$VERSION" ] || usage

# This launcher (and, by extension, -s) must work regardless of the
# caller's current directory: a bare/relative script name is resolved
# against this file's own directory, not the working directory, so
# checkin.py/checkout.py are found even when invoked from anywhere else
# in (or outside) the repo. checkin.py/checkout.py then locate src/ws the
# same CWD-independent way, via their own __file__.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "$SCRIPT" ] && [ "${SCRIPT#/}" = "$SCRIPT" ]; then
    SCRIPT="$SCRIPT_DIR/$SCRIPT"
fi

stop_dangling_processes() {
    local vitis_root="$1"
    for name in "${PROC_NAMES[@]}"; do
        pids=$(pgrep -x "$name" 2>/dev/null || true)
        for pid in $pids; do
            # vitis_root is always resolved by this point, so every
            # matched name (including the otherwise-unambiguous "vitis"/
            # "vitis-server") is scoped to it - never touches a different
            # Vitis install's processes, or an unrelated Java/Eclipse-based
            # program left running on the machine.
            exe_path="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
            case "$exe_path" in
                "$vitis_root"/*) ;;
                *) continue ;;
            esac
            if kill -9 "$pid" 2>/dev/null; then
                echo "Stopped $name (pid=$pid)"
            fi
        done
    done
}

VITIS_ROOT=""

# 1. Configured install path (equivalent to config.ini's VivadoInstallPath on
#    the Vivado side), and its parent, in case it was already given as a
#    ".../Vitis" style path. Candidates are listed inline (not through a
#    helper + process substitution) to stay usable on minimal/older shells.
if [ -n "$INSTALL_PATH" ]; then
    for base in "$INSTALL_PATH" "$(dirname "$INSTALL_PATH")"; do
        # base may already be the ".../Vitis" root itself, so check that
        # directly first, before the two known nested layouts.
        for candidate in "$base/bin/vitis" "$base/$VERSION/Vitis/bin/vitis" "$base/Vitis/$VERSION/bin/vitis"; do
            if [ -f "$candidate" ]; then
                VITIS_ROOT="$(dirname "$(dirname "$candidate")")"
                break 2
            fi
        done
    done
fi

# 2. Same rename AMD did for Vivado (Xilinx -> AMDDesignTools) applies to
#    Vitis; both are searched under common install bases.
if [ -z "$VITIS_ROOT" ]; then
    for base in /opt /tools "${HOME:-}"; do
        [ -n "$base" ] || continue
        for name in "${ROOT_DIR_NAMES[@]}"; do
            for candidate in "$base/$name/$VERSION/Vitis/bin/vitis" "$base/$name/Vitis/$VERSION/bin/vitis"; do
                if [ -f "$candidate" ]; then
                    VITIS_ROOT="$(dirname "$(dirname "$candidate")")"
                    break 3
                fi
            done
        done
    done
fi

if [ -z "$VITIS_ROOT" ]; then
    echo "Could not locate a Vitis $VERSION install (searched /opt, /tools, \$HOME x {AMDDesignTools, Xilinx})." >&2
    exit 1
fi

[ "$STOP_DANGLING" -eq 1 ] && stop_dangling_processes "$VITIS_ROOT"

VITIS_PYTHON=""
for d in "$VITIS_ROOT"/tps/lnx64/python-*; do
    if [ -x "$d/bin/python3" ]; then
        VITIS_PYTHON="$d/bin/python3"
        break
    fi
done

echo "VITIS_ROOT   = $VITIS_ROOT"
echo "VITIS_PYTHON = $VITIS_PYTHON"
echo "VITIS_PYTHONPATH:"
echo "  $VITIS_ROOT/cli"
echo "  $VITIS_ROOT/cli/python-packages/lnx64"
echo "  $VITIS_ROOT/cli/proto"
echo "  $VITIS_ROOT/cli/python-packages/site-packages"
echo "  $VITIS_ROOT/scripts/python_pkg"

if [ -n "$SCRIPT" ]; then
    if [ -z "$VITIS_PYTHON" ]; then
        echo "Could not locate the python interpreter bundled with Vitis $VERSION." >&2
        exit 1
    fi
    export PYTHONPATH="$VITIS_ROOT/cli:$VITIS_ROOT/cli/python-packages/lnx64:$VITIS_ROOT/cli/proto:$VITIS_ROOT/cli/python-packages/site-packages:$VITIS_ROOT/scripts/python_pkg${PYTHONPATH:+:$PYTHONPATH}"
    # create_client()'s startServer falls back to a stale dev-build layout
    # ("rigel-server/build/install/...") when XILINX_VITIS is unset, which
    # does not exist in a real install; setting it here (scoped to this
    # process only, not the user's shell environment) makes it use the
    # correct "$VITIS_ROOT/bin/vitis-server" instead.
    export XILINX_VITIS="$VITIS_ROOT"
    # `import hsi`'s native libs (xv_pycommontasks/xv_hsmpytasks) require
    # RDI_DATADIR to be set, otherwise HwManager.open_hw_design fails hard.
    export RDI_DATADIR="$VITIS_ROOT/data"
    exec "$VITIS_PYTHON" "$SCRIPT" "${SCRIPT_ARGS[@]}"
fi
