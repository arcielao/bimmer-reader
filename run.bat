@echo off
rem Bimmer Reader launcher - uses 32-bit Python (EDIABAS needs it).
rem   run.bat          talk to the car
rem   run.bat --demo   try it without the car
cd /d "%~dp0"

py -3-32 -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo.
  echo  32-bit Python wasn't found.
  echo  Install "Windows installer (32-bit)" from https://www.python.org/downloads/windows/
  echo  and keep "py launcher" ticked during setup. Then run this again.
  echo.
  pause
  exit /b 1
)

py -3-32 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pydiabas') else 1)" >nul 2>&1
if errorlevel 1 (
  echo Installing pydiabas...
  py -3-32 -m pip install --upgrade pydiabas
)

py -3-32 bimmer_reader.py %*
if errorlevel 1 pause
