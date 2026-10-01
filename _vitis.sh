#!/usr/bin/env bash
# Find a Vitis install, optionally stop stray Vitis processes,
# and optionally run a Python helper with Vitis' bundled Python.
# Usage: ./_vitis.sh -v <version> [-s <script.py> [script args...]] [--stop-dangling] [-i <install-path>]

set -euo pipefail

ROOT_DIR_NAMES=(AMDDesignTools Xilinx)
PROC_NAMES=(vitis vitis-server eclipse java)

usage() {
    # Print CLI usage and exit.
    echo "Usage: $0 -v <ver> [-i <path>] [-s <py> [args...]] [--stop-dangling]" >&2
    echo "  -v <ver>          Vitis version" >&2
    echo "  -i <path>         Try this install path first" >&2
    echo "  -s <py> [args...] Run script with bundled Python" >&2
    echo "  --stop-dangling   Stop stray Vitis processes" >&2
    echo "Examples:" >&2
    echo "  $0 -v 2025.2" >&2
    echo "  $0 -v 2025.2 -s ./checkout.py --platform my_platform" >&2
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
        # Forward script-specific arguments unchanged.
        *) SCRIPT_ARGS+=("$1"); shift ;;
    esac
done

[ -n "$VERSION" ] || usage

# Resolve relative script paths against this file's directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "$SCRIPT" ] && [ "${SCRIPT#/}" = "$SCRIPT" ]; then
    SCRIPT="$SCRIPT_DIR/$SCRIPT"
fi

stop_dangling_processes() {
    # Stop known Vitis processes that belong to this install.
    local vitis_root="$1"
    for name in "${PROC_NAMES[@]}"; do
        pids=$(pgrep -x "$name" 2>/dev/null || true)
        for pid in $pids; do
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

# 1. Try the configured install path and its parent first.
if [ -n "$INSTALL_PATH" ]; then
    for base in "$INSTALL_PATH" "$(dirname "$INSTALL_PATH")"; do
        # Check the root itself and both supported nested layouts.
        for candidate in "$base/bin/vitis" "$base/$VERSION/Vitis/bin/vitis" "$base/Vitis/$VERSION/bin/vitis"; do
            if [ -f "$candidate" ]; then
                VITIS_ROOT="$(dirname "$(dirname "$candidate")")"
                break 2
            fi
        done
    done
fi

# 2. Scan common install bases under both vendor root names.
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
    # Preserve any caller-provided PYTHONPATH entries.
    export PYTHONPATH="$VITIS_ROOT/cli:$VITIS_ROOT/cli/python-packages/lnx64:$VITIS_ROOT/cli/proto:$VITIS_ROOT/cli/python-packages/site-packages:$VITIS_ROOT/scripts/python_pkg${PYTHONPATH:+:$PYTHONPATH}"
    # Point client startup at the installed Vitis server.
    export XILINX_VITIS="$VITIS_ROOT"
    # Required by HSI native libraries.
    export RDI_DATADIR="$VITIS_ROOT/data"
    exec "$VITIS_PYTHON" "$SCRIPT" "${SCRIPT_ARGS[@]}"
fi
