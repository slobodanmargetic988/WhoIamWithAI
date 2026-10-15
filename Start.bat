@echo off
setlocal
cd /d "%~dp0" || exit /b 1

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 start.py %*
set "launcher_result=%errorlevel%"
goto finished

:try_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python
python start.py %*
set "launcher_result=%errorlevel%"
goto finished

:missing_python
echo Install Python 3.11 or later from https://www.python.org/downloads/windows/
echo Enable the Python launcher or Add Python to PATH during installation.
echo Then open Start.bat again. No administrator access or pip packages are needed.
set "launcher_result=1"

:finished
if "%~1"=="" pause
exit /b %launcher_result%
