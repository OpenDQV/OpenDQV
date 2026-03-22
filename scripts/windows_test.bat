@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  OpenDQV Windows Test Runner
::  3-run benchmark suite — mirrors the RT72 Pi 400 methodology
::
::  Usage: windows_test.bat
::  Requirements: Python 3.11+ on PATH, internet access (pip)
::
::  What it does:
::    1. Creates a temporary .venv
::    2. Installs all dev dependencies
::    3. Runs lint check (ruff)
::    4. Runs the full test suite 3 times
::    5. Prints a summary with timing per run
::    6. Cleans up all generated artefacts
:: ============================================================

echo.
echo ============================================================
echo   OpenDQV Windows Test Runner
echo ============================================================
echo.

:: ── Pre-flight: Disk space check (need ~2GB free on C:) ──────
for /f "tokens=3" %%s in ('dir /-c C:\ 2^>nul ^| findstr /i "bytes free"') do set FREE_BYTES=%%s
:: Strip commas — Windows dir output uses locale-specific separators
set FREE_BYTES=!FREE_BYTES:,=!
:: Check if >= 2,000,000,000 bytes free (2GB)
:: Use string length comparison on the number — 2GB = 10 digits minimum
set "MIN_DIGITS=2000000000"
if not defined FREE_BYTES (
    echo WARNING: Could not determine free disk space. Proceeding anyway.
    echo   Ensure at least 2GB free on C: before continuing.
    echo.
    goto :disk_ok
)
:: Compare numerically via length then value
set LEN_FREE=0
set TMPSTR=!FREE_BYTES!
:count_loop
if "!TMPSTR!"=="" goto :count_done
set TMPSTR=!TMPSTR:~1!
set /a LEN_FREE+=1
goto :count_loop
:count_done
set LEN_MIN=10
if !LEN_FREE! LSS !LEN_MIN! goto :disk_fail
if !LEN_FREE! EQU !LEN_MIN! if "!FREE_BYTES!" LSS "!MIN_DIGITS!" goto :disk_fail
goto :disk_ok

:disk_fail
echo WARNING: Low disk space detected.
echo   Free space: approximately !FREE_BYTES! bytes
echo   Required:   at least 2,000,000,000 bytes (2GB)
echo.
echo   The dependency install may fail with "No space left on device".
echo   Free up disk space before continuing.
echo.
set /p CONTINUE="Continue anyway? (y/N): "
if /i "!CONTINUE!" NEQ "y" exit /b 1

:disk_ok
:: ── Pre-flight: Python check ─────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found on PATH.
    echo Install Python 3.11+ from https://python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    exit /b 1
)

:: Get Python version
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2,3 delims=." %%a in ("!PYVER!") do (
    set PYMAJ=%%a
    set PYMIN=%%b
    set PYPATCH=%%c
)

echo   Python version: !PYVER!

if !PYMAJ! LSS 3 goto :python_fail
if !PYMAJ! EQU 3 if !PYMIN! LSS 11 goto :python_fail
goto :python_ok

:python_fail
echo ERROR: Python 3.11+ required. Found Python !PYVER!.
echo Install from https://python.org/downloads/
exit /b 1

:python_ok
echo   Python OK (3.11+ required, found !PYVER!)
echo.

:: ── Create virtual environment ───────────────────────────────
echo [1/5] Creating virtual environment...
if exist .venv (
    echo   .venv already exists — removing and recreating...
    rmdir /s /q .venv
)
python -m venv .venv
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    exit /b 1
)
echo   .venv created.
echo.

:: ── Activate ─────────────────────────────────────────────────
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment.
    goto :cleanup_fail
)

:: ── Upgrade pip silently ─────────────────────────────────────
echo [2/5] Upgrading pip...
python -m pip install --upgrade pip -q
echo   pip upgraded.
echo.

:: ── Install dependencies ─────────────────────────────────────
echo [3/5] Installing dependencies (this may take a minute)...
pip install -r requirements-dev.txt -q
if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    goto :cleanup_fail
)
echo   Dependencies installed.
echo.

:: ── Lint check ───────────────────────────────────────────────
echo [4/5] Lint check (ruff)...
ruff check . --select E,W,F --ignore E501,E402,E701
if errorlevel 1 (
    echo ERROR: Lint check failed. Fix ruff errors before running tests.
    goto :cleanup_fail
)
echo   Lint OK.
echo.

:: ── Set UTF-8 mode (fixes Unicode symbols in CLI output on Windows) ──
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

:: ── 3-run test benchmark ─────────────────────────────────────
echo [5/5] Running test suite (3 passes for benchmark consistency)...
echo.

set OVERALL_PASS=1
set RUN1_STATUS=FAIL
set RUN2_STATUS=FAIL
set RUN3_STATUS=FAIL
set RUN1_START=
set RUN1_END=
set RUN2_START=
set RUN2_END=
set RUN3_START=
set RUN3_END=

:: ── Run 1 ────────────────────────────────────────────────────
echo ── Run 1 of 3 ──────────────────────────────────────────────
set RUN1_START=%TIME%
python -m pytest tests\ --ignore=tests\test_e2e.py -q --tb=short 2>&1 | tee test_run1.log
set RUN1_EXIT=!ERRORLEVEL!
set RUN1_END=%TIME%
if !RUN1_EXIT! EQU 0 (
    set RUN1_STATUS=PASS
) else (
    set OVERALL_PASS=0
    echo   [Run 1 log saved to test_run1.log]
)
echo.

:: ── Run 2 ────────────────────────────────────────────────────
echo ── Run 2 of 3 ──────────────────────────────────────────────
set RUN2_START=%TIME%
python -m pytest tests\ --ignore=tests\test_e2e.py -q --tb=short 2>&1 | tee test_run2.log
set RUN2_EXIT=!ERRORLEVEL!
set RUN2_END=%TIME%
if !RUN2_EXIT! EQU 0 (
    set RUN2_STATUS=PASS
) else (
    set OVERALL_PASS=0
    echo   [Run 2 log saved to test_run2.log]
)
echo.

:: ── Run 3 ────────────────────────────────────────────────────
echo ── Run 3 of 3 ──────────────────────────────────────────────
set RUN3_START=%TIME%
python -m pytest tests\ --ignore=tests\test_e2e.py -q --tb=short 2>&1 | tee test_run3.log
set RUN3_EXIT=!ERRORLEVEL!
set RUN3_END=%TIME%
if !RUN3_EXIT! EQU 0 (
    set RUN3_STATUS=PASS
) else (
    set OVERALL_PASS=0
    echo   [Run 3 log saved to test_run3.log]
)
echo.

:: ── Summary ──────────────────────────────────────────────────
echo ============================================================
echo   OpenDQV Windows Test Results
echo ============================================================
echo   Python:   !PYVER!
echo   Platform: Windows
echo.
echo   Run 1: !RUN1_STATUS!   [!RUN1_START! ^-^> !RUN1_END!]
echo   Run 2: !RUN2_STATUS!   [!RUN2_START! ^-^> !RUN2_END!]
echo   Run 3: !RUN3_STATUS!   [!RUN3_START! ^-^> !RUN3_END!]
echo.
if !OVERALL_PASS! EQU 1 (
    echo   Overall: ALL PASSED
) else (
    echo   Overall: FAILED -- check output above for details
)
echo ============================================================
echo.

if !OVERALL_PASS! EQU 1 (
    goto :cleanup_pass
) else (
    goto :cleanup_fail
)

:: ── Cleanup (pass) ───────────────────────────────────────────
:cleanup_pass
echo Cleaning up...
if exist .venv         rmdir /s /q .venv
if exist .coverage     del /f /q .coverage
if exist opendqv.db    del /f /q opendqv.db
if exist test_run1.log del /f /q test_run1.log
if exist test_run2.log del /f /q test_run2.log
if exist test_run3.log del /f /q test_run3.log
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
echo Cleanup complete. Repo is back to original state.
echo.
echo Report these Run 1/2/3 times in RT96.
echo.
exit /b 0

:: ── Cleanup (fail) ───────────────────────────────────────────
:cleanup_fail
echo Cleaning up after failure...
if exist .venv         rmdir /s /q .venv
if exist .coverage     del /f /q .coverage
if exist opendqv.db    del /f /q opendqv.db
if exist test_run1.log del /f /q test_run1.log
if exist test_run2.log del /f /q test_run2.log
if exist test_run3.log del /f /q test_run3.log
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
echo Cleanup complete.
echo.
exit /b 1
