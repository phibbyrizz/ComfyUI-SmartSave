@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=python"

REM Prefer the Python bundled with common ComfyUI portable/easy-install layouts.
if exist "..\..\..\python_embeded\python.exe" set "PYTHON_EXE=..\..\..\python_embeded\python.exe"
if exist "..\..\..\python_embedded\python.exe" set "PYTHON_EXE=..\..\..\python_embedded\python.exe"

echo ============================================================
echo SmartSave Auto-Sort - APPLY MODE
echo ============================================================
echo This WILL move and rename files according to the current plan.
echo Run auto_sort_preview.bat first if you have not reviewed the plan.
echo.

"%PYTHON_EXE%" auto_sort.py --apply --report auto_sort_apply_plan.json

echo.
if errorlevel 1 (
    echo Auto-Sort apply encountered an error.
) else (
    echo Auto-Sort apply completed.
    echo You can run auto_sort_preview.bat again to verify zero remaining moves.
)

echo.
pause
