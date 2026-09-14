@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"

REM Prefer the Python bundled with common ComfyUI portable/easy-install layouts.
if exist "..\..\..\python_embeded\python.exe" set "PYTHON_EXE=..\..\..\python_embeded\python.exe"
if exist "..\..\..\python_embedded\python.exe" set "PYTHON_EXE=..\..\..\python_embedded\python.exe"

echo ============================================================
echo SmartSave Auto-Sort - PREVIEW MODE
echo ============================================================
echo No files will be moved or renamed.
echo.

"%PYTHON_EXE%" auto_sort.py --report auto_sort_preview.json

echo.
if errorlevel 1 (
    echo Auto-Sort preview encountered an error.
) else (
    echo Preview complete. No files were moved.
    echo Review the proposed moves above before running auto_sort_apply.bat.
)

echo.
pause
