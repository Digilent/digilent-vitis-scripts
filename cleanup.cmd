
@echo off
setlocal enabledelayedexpansion
rem save current working directory
pushd %~dp0

rem delete all files from subfolders, but never touch Git metadata (.git can
rem be a directory in a standalone clone, or a submodule config file). Uses
rem a delayed-expansion substring check instead of piping to findstr, since
rem a pipe inside a parenthesized for/do block is unreliable in batch files.
for /d /r %%i in (*) do (
	set "p=%%i"
	set "chk=!p:.git=!"
	if "!p!"=="!chk!" del /f /q "%%i\*"
)
rem delete all subfolders except .git
for /d %%i in (*) do (
	if /I not "%%~nxi"==".git" rd /S /Q "%%i"
)

rem unmark read only from all files
attrib -R .\* /S

rem mark read only those we wish to keep
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

rem delete all non read-only
del /Q /A:-R .\*

rem unmark read-only
attrib -R .\*

rem restore original working directory
endlocal
popd
