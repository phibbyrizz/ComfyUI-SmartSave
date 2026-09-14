@echo off
setlocal

REM Resolve everything relative to THIS batch file, not the terminal's working directory.
set "TEST_DIR=%~dp0"
set "TEST_FILE=%TEST_DIR%test_auto_sort.py"
set "PYTHON_EXE=python"

REM Typical ComfyUI Easy Install / portable layout:
REM ComfyUI-SmartSave\tests\ -> up four levels -> python_embeded
if exist "%TEST_DIR%..\..\..\..\python_embeded\python.exe" (
    set "PYTHON_EXE=%TEST_DIR%..\..\..\..\python_embeded\python.exe"
) else if exist "%TEST_DIR%..\..\..\..\python_embedded\python.exe" (
    set "PYTHON_EXE=%TEST_DIR%..\..\..\..\python_embedded\python.exe"
)

echo Running SmartSave regression tests...
echo Test file: "%TEST_FILE%"
echo Python: "%PYTHON_EXE%"
echo.

if not exist "%TEST_FILE%" (
    echo ERROR: test_auto_sort.py was not found beside this batch file.
    echo Expected: "%TEST_FILE%"
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%TEST_FILE%"

echo.
if errorlevel 1 (
    echo REGRESSION TESTS FAILED.
) else (
    echo ALL REGRESSION TESTS PASSED.
)

echo.
pause
