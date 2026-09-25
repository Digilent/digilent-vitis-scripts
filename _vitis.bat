@echo off
rem Locates a Vitis install (any version) with no dependency on env vars or
rem PATH, finds its bundled python interpreter, and (optionally) runs a
rem python script (checkin.py/checkout.py/...) with it -- meant as a
rem `vitis -s <script>` counterpart that also picks the Vitis version and
rem does not require `vitis` to already be reachable from PATH.
rem
rem Usage: _vitis.bat -v ^<version^> [-i ^<install-path^>] [-s ^<script.py^> [script args...]] [--stop-dangling]
rem Example: _vitis.bat -v 2025.2
rem          _vitis.bat -v 2025.2 --stop-dangling
rem          _vitis.bat -v 2025.2 -s .\checkout.py
rem          _vitis.bat -v 2025.2 -s .\checkout.py --platform my_platform

setlocal enabledelayedexpansion

rem `shift` (used below to walk the arg list) also shifts %0, which makes
rem %~dp0 unreliable/CWD-rooted after the first shift. Capture it into a
rem variable first, before any shift happens, and use that everywhere else.
set "SELF_DIR=%~dp0"

set "VERSION="
set "SCRIPT="
set "STOP_DANGLING=0"
set "INSTALL_PATH="
set "SCRIPT_ARGS="

:parse_args
if "%~1"=="" goto after_args
if /I "%~1"=="-v" (
    set "VERSION=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-s" (
    set "SCRIPT=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-i" (
    set "INSTALL_PATH=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="--stop-dangling" (
    set "STOP_DANGLING=1"
    shift
    goto parse_args
)
rem Anything else is forwarded as-is to SCRIPT (e.g. checkout.py's own
rem --platform/--app selective-rebuild flags), not silently dropped.
set "SCRIPT_ARGS=%SCRIPT_ARGS% "%~1""
shift
goto parse_args
:after_args

if not defined VERSION (
    echo Usage: _vitis.bat -v ^<version^> [-i ^<install-path^>] [-s ^<script.py^>] [--stop-dangling]
    exit /b 1
)

rem This launcher (and, by extension, -s) must work regardless of the
rem caller's current directory: a bare/relative script name is resolved
rem against this file's own directory (%SELF_DIR%), not the working
rem directory, so checkin.py/checkout.py are found even when invoked from
rem anywhere else in (or outside) the repo. checkin.py/checkout.py then
rem locate src/ws the same CWD-independent way, via their own __file__.
rem A path is already rooted (and must be left alone) if it has a drive
rem letter ("C:\..."), is a UNC path ("\\server\share\...") or is rooted on
rem the current drive ("\dir\..."/"/dir/..."), so check the first two
rem characters instead of assuming only "C:\..." counts as absolute.
set "SCRIPT_COLON="
set "SCRIPT_FIRSTCHAR="
if defined SCRIPT (
    set "SCRIPT_COLON=%SCRIPT:~1,1%"
    set "SCRIPT_FIRSTCHAR=%SCRIPT:~0,1%"
)
if defined SCRIPT if not "%SCRIPT_COLON%"==":" if not "%SCRIPT_FIRSTCHAR%"=="\" if not "%SCRIPT_FIRSTCHAR%"=="/" set "SCRIPT=%SELF_DIR%%SCRIPT%"

rem Same rename AMD did for Vivado (Xilinx -^> AMDDesignTools) applies to Vitis.
set "VITIS_ROOT="
if defined INSTALL_PATH (
    rem INSTALL_PATH may already be the "...\Vitis" root itself, or one of
    rem the two known layouts under it - try all three directly first.
    if exist "%INSTALL_PATH%\bin\vitis.bat" set "VITIS_ROOT=%INSTALL_PATH%"
    if not defined VITIS_ROOT if exist "%INSTALL_PATH%\%VERSION%\Vitis\bin\vitis.bat" set "VITIS_ROOT=%INSTALL_PATH%\%VERSION%\Vitis"
    if not defined VITIS_ROOT if exist "%INSTALL_PATH%\Vitis\%VERSION%\bin\vitis.bat" set "VITIS_ROOT=%INSTALL_PATH%\Vitis\%VERSION%"
    rem INSTALL_PATH may also be one level ABOVE the vendor dir (e.g.
    rem "C:\AMDDesignTools\2025.2" itself, one level short of "...\Vitis"),
    rem matching the shell/PowerShell/Python discovery implementations,
    rem which all also try the parent of the configured path. Without this,
    rem such a path misses the valid install below it and falls through to
    rem the (much less targeted) drive-wide scan below.
    rem This whole "if defined INSTALL_PATH ( ... )" is a single parenthesized
    rem block, so a plain "%INSTALL_PARENT%" below would be expanded once at
    rem parse time -- before the "set" above even runs -- and would always
    rem see it as empty/undefined, silently skipping this fallback. Delayed
    rem expansion ("!INSTALL_PARENT!", enabled above) re-reads the variable
    rem at execution time instead, the same fix used for "!ERRORLEVEL!" below.
    if not defined VITIS_ROOT (
        for %%P in ("%INSTALL_PATH%\..") do set "INSTALL_PARENT=%%~fP"
    )
    if not defined VITIS_ROOT if defined INSTALL_PARENT if exist "!INSTALL_PARENT!\%VERSION%\Vitis\bin\vitis.bat" set "VITIS_ROOT=!INSTALL_PARENT!\%VERSION%\Vitis"
    if not defined VITIS_ROOT if defined INSTALL_PARENT if exist "!INSTALL_PARENT!\Vitis\%VERSION%\bin\vitis.bat" set "VITIS_ROOT=!INSTALL_PARENT!\Vitis\%VERSION%"
)
for %%D in (C D E F G H I J K L M N O P Q R S T U V W X Y Z) do (
    for %%N in (AMDDesignTools Xilinx) do (
        if not defined VITIS_ROOT if exist "%%D:\%%N\%VERSION%\Vitis\bin\vitis.bat" set "VITIS_ROOT=%%D:\%%N\%VERSION%\Vitis"
        if not defined VITIS_ROOT if exist "%%D:\%%N\Vitis\%VERSION%\bin\vitis.bat" set "VITIS_ROOT=%%D:\%%N\Vitis\%VERSION%"
    )
)

if not defined VITIS_ROOT (
    echo Could not locate a Vitis %VERSION% install ^(searched drives x {AMDDesignTools, Xilinx}^).
    exit /b 1
)

if "%STOP_DANGLING%"=="1" (
    rem VITIS_ROOT is always resolved by this point, so every matched
    rem process name (including the otherwise-unambiguous "vitis.exe"/
    rem "vitis-server.exe") is scoped to it - never touches a different
    rem Vitis install's processes, or an unrelated Java/Eclipse-based
    rem program left running on the machine. A trailing separator is
    rem appended to VITIS_ROOT before the prefix check (with an exact-match
    rem fallback) so a sibling install like "...\Vitis-old\java.exe" can
    rem never match root "...\Vitis" as a mere string prefix.
    powershell -NoProfile -NonInteractive -Command ^
        "Get-CimInstance Win32_Process | Where-Object { ('vitis.exe','vitis-server.exe','eclipse.exe','java.exe') -contains $_.Name -and $_.ExecutablePath -and ($_.ExecutablePath.Equals('%VITIS_ROOT%', [System.StringComparison]::OrdinalIgnoreCase) -or $_.ExecutablePath.StartsWith('%VITIS_ROOT%\', [System.StringComparison]::OrdinalIgnoreCase)) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)

set "VITIS_PYTHON="
for /d %%I in ("%VITIS_ROOT%\tps\win64\python-*") do (
    if not defined VITIS_PYTHON if exist "%%I\python.exe" set "VITIS_PYTHON=%%I\python.exe"
)

echo VITIS_ROOT   = %VITIS_ROOT%
echo VITIS_PYTHON = %VITIS_PYTHON%
echo VITIS_PYTHONPATH:
echo   %VITIS_ROOT%\cli
echo   %VITIS_ROOT%\cli\python-packages\win64
echo   %VITIS_ROOT%\cli\proto
echo   %VITIS_ROOT%\cli\python-packages\site-packages
echo   %VITIS_ROOT%\scripts\python_pkg

if defined SCRIPT (
    if not defined VITIS_PYTHON (
        echo Could not locate the python interpreter bundled with Vitis %VERSION%.
        exit /b 1
    )
    set "PYTHONPATH=%VITIS_ROOT%\cli;%VITIS_ROOT%\cli\python-packages\win64;%VITIS_ROOT%\cli\proto;%VITIS_ROOT%\cli\python-packages\site-packages;%VITIS_ROOT%\scripts\python_pkg"
    rem create_client()'s startServer falls back to a stale dev-build layout
    rem ("rigel-server\build\install\...") when XILINX_VITIS is unset, which
    rem does not exist in a real install; setting it here (scoped to this
    rem process only, not the user's global environment) makes it use the
    rem correct "%VITIS_ROOT%\bin\vitis-server.bat" instead.
    set "XILINX_VITIS=%VITIS_ROOT%"
    rem `import hsi`'s native libs (xv_pycommontasks/xv_hsmpytasks) require
    rem RDI_DATADIR to be set, otherwise HwManager.open_hw_design fails hard.
    set "RDI_DATADIR=%VITIS_ROOT%\data"
    rem `%ERRORLEVEL%` would be expanded at parse-time here (since this whole
    rem "if defined SCRIPT (...)" body is a single parenthesized block), i.e.
    rem it would capture whatever ERRORLEVEL was BEFORE the python line even
    rem runs, not python's actual exit code -- silently forwarding a stale
    rem (usually 0) code regardless of real success/failure. Delayed
    rem expansion ("!ERRORLEVEL!", enabled above) re-reads the variable at
    rem execution time instead, so the real exit code is forwarded.
    "%VITIS_PYTHON%" "%SCRIPT%" %SCRIPT_ARGS%
    exit /b !ERRORLEVEL!
)

endlocal
