@echo off
rem ============================================================
rem  DeepSeek chubby-fish desktop pet  --  launcher
rem  Starts pet.py with a windowless Python so no console shows.
rem  Kept pure ASCII: cmd.exe decodes .bat files with the OEM
rem  code page, so non-ASCII paths here would be mangled.
rem ============================================================
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "PYW="

rem 1) Python bundled with the harness runtime (preferred)
set "RUNTIME=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime"
if exist "%RUNTIME%\dependencies\python\pythonw.exe" set "PYW=%RUNTIME%\dependencies\python\pythonw.exe"

rem 2) Portable install layout: <drive>:<any folder>\resources\runtime\primary-runtime\...
if not defined PYW (
  for %%D in (C D E F) do (
    if exist "%%D:\" for /d %%P in ("%%D:\*") do (
      if not defined PYW if exist "%%~fP\resources\runtime\primary-runtime\dependencies\python\pythonw.exe" (
        set "PYW=%%~fP\resources\runtime\primary-runtime\dependencies\python\pythonw.exe"
      )
    )
  )
)

rem 3) Any pythonw.exe on PATH
if not defined PYW for %%I in (pythonw.exe) do if not "%%~$PATH:I"=="" set "PYW=%%~$PATH:I"

if not defined PYW (
  echo [pet] pythonw.exe not found.
  echo [pet] Install Python 3.10+ or edit this file to point at one.
  pause
  exit /b 1
)

start "" "%PYW%" "%HERE%pet.py" %*
exit /b 0
