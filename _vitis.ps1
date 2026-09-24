<#
.SYNOPSIS
    Locates a Vitis install (any version) with no dependency on env vars or
    PATH, finds its bundled python interpreter, and (optionally) runs a
    python script (checkin.py/checkout.py/...) with it -- meant as a
    `vitis -s <script>` counterpart that also picks the Vitis version and
    does not require `vitis` to already be reachable from PATH -- or stops
    dangling vitis/eclipse/java processes left holding workspace file locks.

    Mirrors misc.py's findVitisRoot/findVitisPython/vitisPythonPathEntries/
    stopDanglingVitisProcesses, as a python-free bootstrap for the same
    Windows search rules (drives x {AMDDesignTools, Xilinx} root names).

.PARAMETER Version
    Vitis version to look for, e.g. "2025.2". Alias: -v (same spirit as
    `vitis`'s own flags).

.PARAMETER Script
    Optional python script to run with the bundled interpreter, e.g.
    checkin.py or checkout.py. Alias: -s, same as `vitis -s <script>`.

.PARAMETER InstallPath
    Optional install path to try first (equivalent to config.ini's
    VivadoInstallPath on the Vivado side). Checked directly as a "...\Vitis"
    root itself, and via both "<path>\<ver>\Vitis" and "<path>\Vitis\<ver>"
    layouts under it and under its parent.

.PARAMETER StopDangling
    Stop dangling vitis/vitis-server/eclipse/java processes before doing
    anything else (root cause of checkout.py's WinError 32 rmtree failures).

.EXAMPLE
    .\_vitis.ps1 -v 2025.2
    .\_vitis.ps1 -v 2025.2 -StopDangling
    .\_vitis.ps1 -v 2025.2 -s .\checkout.py
    .\_vitis.ps1 -v 2025.2 -s .\checkout.py --platform system_wrapper_tac5112
#>
param(
    [Parameter(Mandatory = $true)][Alias("v")][string]$Version,
    [Alias("s")][string]$Script = "",
    [Alias("i")][string]$InstallPath = "",
    [switch]$StopDangling,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArgs
)

# Same rename AMD did for Vivado (Xilinx -> AMDDesignTools) applies to Vitis.
$RootDirNames = @("AMDDesignTools", "Xilinx")
$ProcNames = @("vitis", "vitis-server", "eclipse", "java")

function Get-LayoutCandidates([string]$RootBase, [string]$Ver) {
    @(
        (Join-Path $RootBase (Join-Path $Ver (Join-Path "Vitis" (Join-Path "bin" "vitis.bat")))),
        (Join-Path $RootBase (Join-Path "Vitis" (Join-Path $Ver (Join-Path "bin" "vitis.bat"))))
    )
}

function Test-PathUnderRoot([string]$Candidate, [string]$Root) {
    # Boundary-aware, case-insensitive "is Candidate under Root" check: a
    # plain StartsWith on the raw strings would let a sibling install like
    # "C:\AMD\Vitis-old\java.exe" match root "C:\AMD\Vitis", since that root
    # is itself just a string prefix of the sibling's path.
    if (-not $Candidate -or -not $Root) { return $false }
    $normalizedRoot = $Root.TrimEnd('\')
    return ($Candidate.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            $Candidate.StartsWith($normalizedRoot + '\', [System.StringComparison]::OrdinalIgnoreCase))
}

function Find-VitisRoot([string]$Ver, [string]$Configured) {
    $candidates = @()
    if ($Configured) {
        $resolved = (Resolve-Path -LiteralPath $Configured -ErrorAction SilentlyContinue).Path
        if ($resolved) {
            # $resolved may already be the "...\Vitis" root itself (as
            # documented for -InstallPath/-i), so check that directly too.
            $candidates += (Join-Path $resolved (Join-Path "bin" "vitis.bat"))
            $candidates += Get-LayoutCandidates $resolved $Ver
            $candidates += Get-LayoutCandidates (Split-Path $resolved -Parent) $Ver
        }
    }
    $drives = Get-PSDrive -PSProvider FileSystem | ForEach-Object { "$($_.Name):\" }
    foreach ($drive in $drives) {
        foreach ($name in $RootDirNames) {
            $candidates += Get-LayoutCandidates (Join-Path $drive $name) $Ver
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            # candidate = <vitis_root>\bin\vitis.bat
            return (Split-Path (Split-Path $candidate -Parent) -Parent)
        }
    }
    return $null
}

function Find-VitisPython([string]$VitisRoot) {
    $tpsDir = Join-Path $VitisRoot "tps\win64"
    if (-not (Test-Path -LiteralPath $tpsDir -PathType Container)) { return $null }
    $pyDir = Get-ChildItem -LiteralPath $tpsDir -Directory |
        Where-Object { $_.Name -like "python-*" } | Select-Object -First 1
    if (-not $pyDir) { return $null }
    $exe = Join-Path $pyDir.FullName "python.exe"
    if (Test-Path -LiteralPath $exe -PathType Leaf) { return $exe }
    return $null
}

function Get-VitisPythonPathEntries([string]$VitisRoot) {
    @(
        (Join-Path $VitisRoot "cli"),
        (Join-Path $VitisRoot "cli\python-packages\win64"),
        (Join-Path $VitisRoot "cli\proto"),
        # Platform-independent 3rd-party deps (pyelftools, psutil, ...) that
        # `import xsdb` needs transitively (via xsdb._elf).
        (Join-Path $VitisRoot "cli\python-packages\site-packages"),
        # `import hsi` (GetMetadata) is HSI's own self-contained package,
        # not under `cli` at all, one level up under `scripts\python_pkg`.
        (Join-Path $VitisRoot "scripts\python_pkg")
    )
}

function Stop-DanglingVitisProcesses([string]$VitisRoot) {
    $stopped = @()
    foreach ($name in $ProcNames) {
        $procs = Get-Process -Name $name -ErrorAction SilentlyContinue
        foreach ($p in $procs) {
            # VitisRoot is always resolved by this point, so every matched
            # name (including the otherwise-unambiguous "vitis"/
            # "vitis-server") is scoped to it - never touches a different
            # Vitis install's processes, or an unrelated Java/Eclipse-based
            # program left running on the machine.
            if (-not $VitisRoot -or -not $p.Path -or -not (Test-PathUnderRoot $p.Path $VitisRoot)) {
                continue
            }
            try {
                Stop-Process -Id $p.Id -Force -ErrorAction Stop
                $stopped += $p.Id
                Write-Host "Stopped $($p.ProcessName) (pid=$($p.Id))"
            } catch {
                Write-Warning "Failed to stop $($p.ProcessName) (pid=$($p.Id)): $_"
            }
        }
    }
    return $stopped
}

# This launcher (and, by extension, -Script) must work regardless of the
# caller's current directory: a bare/relative -Script is resolved against
# this file's own directory ($PSScriptRoot), not the working directory, so
# `checkin.py`/`checkout.py` are found even when invoked from anywhere else
# in (or outside) the repo. checkin.py/checkout.py then locate `src`/`ws`
# the same CWD-independent way, via their own `__file__`.
if ($Script -and -not [System.IO.Path]::IsPathRooted($Script)) {
    $Script = Join-Path $PSScriptRoot $Script
}

$vitisRoot = Find-VitisRoot -Ver $Version -Configured $InstallPath
if (-not $vitisRoot) {
    Write-Error "Could not locate a Vitis $Version install (searched drives x {AMDDesignTools, Xilinx})."
    exit 1
}

if ($StopDangling) {
    Stop-DanglingVitisProcesses -VitisRoot $vitisRoot | Out-Null
}

$vitisPython = Find-VitisPython -VitisRoot $vitisRoot

Write-Host "VITIS_ROOT   = $vitisRoot"
Write-Host "VITIS_PYTHON = $vitisPython"
Write-Host "VITIS_PYTHONPATH:"
Get-VitisPythonPathEntries -VitisRoot $vitisRoot | ForEach-Object { Write-Host "  $_" }

if ($Script) {
    if (-not $vitisPython) {
        Write-Error "Could not locate the python interpreter bundled with Vitis $Version."
        exit 1
    }
    $env:PYTHONPATH = (Get-VitisPythonPathEntries -VitisRoot $vitisRoot) -join ";"
    # create_client()'s startServer falls back to a stale dev-build layout
    # ("rigel-server/build/install/...") when XILINX_VITIS is unset, which
    # does not exist in a real install; setting it (scoped to this process
    # only, not the user's global environment) makes it use the correct
    # "<VitisRoot>/bin/vitis-server.bat" instead.
    $env:XILINX_VITIS = $vitisRoot
    # `import hsi`'s native libs (xv_pycommontasks/xv_hsmpytasks) require
    # RDI_DATADIR to be set, otherwise HwManager.open_hw_design fails hard.
    $env:RDI_DATADIR = Join-Path $vitisRoot "data"
    & $vitisPython $Script @ScriptArgs
    exit $LASTEXITCODE
}
