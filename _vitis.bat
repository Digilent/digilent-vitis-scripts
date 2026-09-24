@echo off
rem Locates a Vitis install (any version) with no dependency on env vars or
rem PATH, finds its bundled python interpreter, and (optionally) runs a
rem python script (checkin.py/checkout.py/...) with it -- meant as a
rem `vitis -s <script>` counterpart that also picks the Vitis version and
rem does not require `vitis` to already be reachable from PATH.
rem
rem Usage: _vitis.bat -v ^<version^> [-s ^<script.py^> [script args...]] [--stop-dangling]
rem Example: _vitis.bat -v 2025.2
rem          _vitis.bat -v 2025.2 --stop-dangling
rem          _vitis.bat -v 2025.2 -s .\checkout.py
rem          _vitis.bat -v 2025.2 -s .\checkout.py --platform system_wrapper_tac5112

setlocal enabledelayedexpansion

rem `shift` (used below to walk the arg list) also shifts %0, which makes
rem %~dp0 unreliable/CWD-rooted after the first shift. Capture it into a
rem variable first, before any shift happens, and use that everywhere else.
set "SELF_DIR=%~dp0"

set "VERSION="
set "SCRIPT="
set "STOP_DANGLING=0"
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
    echo Usage: _vitis.bat -v ^<version^> [-s ^<script.py^>] [--stop-dangling]
    exit /b 1
)

rem This launcher (and, by extension, -s) must work regardless of the
rem caller's current directory: a bare/relative script name is resolved
rem against this file's own directory (%SELF_DIR%), not the working
rem directory, so checkin.py/checkout.py are found even when invoked from
rem anywhere else in (or outside) the repo. checkin.py/checkout.py then
rem locate src/ws the same CWD-independent way, via their own __file__.
set "SCRIPT_COLON="
if defined SCRIPT (
    set "SCRIPT_COLON=%SCRIPT:~1,1%"
)
if defined SCRIPT if not "%SCRIPT_COLON%"==":" set "SCRIPT=%SELF_DIR%%SCRIPT%"

rem Same rename AMD did for Vivado (Xilinx -^> AMDDesignTools) applies to Vitis.
set "VITIS_ROOT="
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
    rem "vitis.exe"/"vitis-server.exe" are unambiguous, but "eclipse.exe"/
    rem "java.exe" are generic image names also used by unrelated apps, so
    rem those two are only killed once their own ExecutablePath is confirmed
    rem to live under this VITIS_ROOT (avoids taking down some unrelated
    rem Java/Eclipse-based program left running on the machine).
    powershell -NoProfile -NonInteractive -Command ^
        "Get-CimInstance Win32_Process | Where-Object { ('vitis.exe','vitis-server.exe') -contains $_.Name -or (('eclipse.exe','java.exe') -contains $_.Name -and $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith('%VITIS_ROOT%'.ToLower())) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
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
