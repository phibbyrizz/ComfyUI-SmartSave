@echo off
setlocal

cd /d "%~dp0"

:: 1. Check for standard ComfyUI portable embedded python
if exist "..\..\..\python_embeded\python.exe" (
    "..\..\..\python_embeded\python.exe" auto_sort.py
    goto end
)

:: 2. Fallback to system Python
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python auto_sort.py
    goto end
)

echo [ERROR] Python environment not detected.
echo Please run from within a ComfyUI installation or ensure Python is added to PATH.

:end
pause