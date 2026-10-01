@echo off
rem Find a Vitis install, optionally stop stray Vitis processes,
rem and optionally run a Python helper with Vitis' bundled Python.
rem Usage: _vitis.bat -v ^<version^> [-i ^<install-path^>] [-s ^<script.py^> [script args...]] [--stop-dangling]

setlocal enabledelayedexpansion

rem Capture this script's directory before shifting args.
set "SELF_DIR=%~dp0"

set "VERSION="
set "SCRIPT="
set "STOP_DANGLING=0"
set "INSTALL_PATH="
set "SCRIPT_ARGS="

:parse_args
rem Parse launcher options and collect script arguments.
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
rem Forward script-specific arguments unchanged.
set "SCRIPT_ARGS=%SCRIPT_ARGS% "%~1""
shift
goto parse_args
:after_args

if not defined VERSION (
    echo Usage: _vitis.bat -v ^<ver^> [-i ^<path^>] [-s ^<py^> [args...]] [--stop-dangling]
    echo   -v ^<ver^>          Vitis version
    echo   -i ^<path^>         Try this install path first
    echo   -s ^<py^> [args...] Run script with bundled Python
    echo   --stop-dangling   Stop stray Vitis processes
    echo Examples:
    echo   _vitis.bat -v 2025.2
    echo   _vitis.bat -v 2025.2 -s .\checkout.py --platform my_platform
    exit /b 1
)

rem Resolve relative -s paths against this file's directory.
rem Treat drive-letter, UNC, and rooted paths as already absolute.
set "SCRIPT_COLON="
set "SCRIPT_FIRSTCHAR="
if defined SCRIPT (
    set "SCRIPT_COLON=%SCRIPT:~1,1%"
    set "SCRIPT_FIRSTCHAR=%SCRIPT:~0,1%"
)
if defined SCRIPT if not "%SCRIPT_COLON%"==":" if not "%SCRIPT_FIRSTCHAR%"=="\" if not "%SCRIPT_FIRSTCHAR%"=="/" set "SCRIPT=%SELF_DIR%%SCRIPT%"

set "VITIS_ROOT="
if defined INSTALL_PATH (
    rem Try INSTALL_PATH itself and both supported nested layouts.
    if exist "%INSTALL_PATH%\bin\vitis.bat" set "VITIS_ROOT=%INSTALL_PATH%"
    if not defined VITIS_ROOT if exist "%INSTALL_PATH%\%VERSION%\Vitis\bin\vitis.bat" set "VITIS_ROOT=%INSTALL_PATH%\%VERSION%\Vitis"
    if not defined VITIS_ROOT if exist "%INSTALL_PATH%\Vitis\%VERSION%\bin\vitis.bat" set "VITIS_ROOT=%INSTALL_PATH%\Vitis\%VERSION%"
    rem Also try the parent of INSTALL_PATH.
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
    rem Stop only processes that belong to this Vitis root.
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
    rem Preserve any caller-provided PYTHONPATH entries.
    set "PYTHONPATH=%VITIS_ROOT%\cli;%VITIS_ROOT%\cli\python-packages\win64;%VITIS_ROOT%\cli\proto;%VITIS_ROOT%\cli\python-packages\site-packages;%VITIS_ROOT%\scripts\python_pkg;%PYTHONPATH%"
    rem Point client startup at the installed Vitis server.
    set "XILINX_VITIS=%VITIS_ROOT%"
    rem Required by HSI native libraries.
    set "RDI_DATADIR=%VITIS_ROOT%\data"
    rem Forward Python's exit code.
    "%VITIS_PYTHON%" "%SCRIPT%" %SCRIPT_ARGS%
    exit /b !ERRORLEVEL!
)

endlocal
