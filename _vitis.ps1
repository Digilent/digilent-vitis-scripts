<#
.SYNOPSIS
    Find a Vitis install, optionally stop stray Vitis processes, and optionally run a Python helper with Vitis' bundled Python.
    Usage: .\_vitis.ps1 -v <version> [-s <script.py> [script args...]] [-StopDangling] [-i <install-path>]
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
    # Return the two supported versioned Vitis layouts under a root.
    @(
        (Join-Path $RootBase (Join-Path $Ver (Join-Path "Vitis" (Join-Path "bin" "vitis.bat")))),
        (Join-Path $RootBase (Join-Path "Vitis" (Join-Path $Ver (Join-Path "bin" "vitis.bat"))))
    )
}

function Test-PathUnderRoot([string]$Candidate, [string]$Root) {
    # Match the root itself or a child path beneath it.
    if (-not $Candidate -or -not $Root) { return $false }
    $normalizedRoot = $Root.TrimEnd('\')
    return ($Candidate.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            $Candidate.StartsWith($normalizedRoot + '\', [System.StringComparison]::OrdinalIgnoreCase))
}

function Find-VitisRoot([string]$Ver, [string]$Configured) {
    # Try the configured path first, then scan known drive roots.
    $candidates = @()
    if ($Configured) {
        $resolved = (Resolve-Path -LiteralPath $Configured -ErrorAction SilentlyContinue).Path
        if ($resolved) {
            # Check the root itself and both nested layouts under it and its parent.
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
    # Find the bundled Python executable under tps\win64.
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
    # Return the PYTHONPATH entries needed by bundled Vitis modules.
    @(
        (Join-Path $VitisRoot "cli"),
        (Join-Path $VitisRoot "cli\python-packages\win64"),
        (Join-Path $VitisRoot "cli\proto"),
        (Join-Path $VitisRoot "cli\python-packages\site-packages"),
        (Join-Path $VitisRoot "scripts\python_pkg")
    )
}

function Stop-DanglingVitisProcesses([string]$VitisRoot) {
    # Stop named processes that belong to this Vitis install only.
    $stopped = @()
    foreach ($name in $ProcNames) {
        $procs = Get-Process -Name $name -ErrorAction SilentlyContinue
        foreach ($p in $procs) {
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

# Resolve relative script paths against this file's directory.
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
    # Preserve any caller-provided PYTHONPATH entries.
    $vitisPythonPathEntries = @(Get-VitisPythonPathEntries -VitisRoot $vitisRoot)
    if ($env:PYTHONPATH) { $vitisPythonPathEntries += $env:PYTHONPATH }
    $env:PYTHONPATH = $vitisPythonPathEntries -join ";"
    # Point client startup at the installed Vitis server.
    $env:XILINX_VITIS = $vitisRoot
    # Required by HSI native libraries.
    $env:RDI_DATADIR = Join-Path $vitisRoot "data"
    & $vitisPython $Script @ScriptArgs
    exit $LASTEXITCODE
}
