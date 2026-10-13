
@echo off
rem Remove generated project contents while keeping launcher/helper files.
rem Usage: run cleanup.cmd from any directory.

setlocal enabledelayedexpansion
rem Work relative to this script.
pushd %~dp0

rem Delete files under subfolders while keeping Git metadata.
for /d /r %%i in (*) do (
	set "p=%%i"
	set "chk=!p:.git=!"
	if "!p!"=="!chk!" del /f /q "%%i\*"
)
rem Delete top-level subfolders except .git.
for /d %%i in (*) do (
	if /I not "%%~nxi"==".git" rd /S /Q "%%i"
)

rem Clear read-only from all files.
attrib -R .\* /S

rem Restore read-only on files we keep.
attrib +R .\cleanup.sh
attrib +R .\cleanup.cmd
attrib +R .\checkin.py
attrib +R .\checkout.py
attrib +R .\misc.py
attrib +R .\_vitis.ps1
attrib +R .\_vitis.bat
attrib +R .\_vitis.sh
attrib +R .\LICENSE
attrib +R .\README.md
attrib +R .\.gitignore
attrib +R .\.git
attrib +R .\.keep

rem Delete remaining writable files.
del /Q /A:-R .\*

rem Clear read-only again.
attrib -R .\*

rem Restore the caller's working directory.
endlocal
popd
