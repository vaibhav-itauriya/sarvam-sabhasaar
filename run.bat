@echo off
cd /d "%~dp0"
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo SabhaSaar is already running on port 8000, so this window will not start a second copy.
  echo Opening http://localhost:8000 in your browser.
  echo If you want to restart it, close the other SabhaSaar window first, then run this again.
  start http://localhost:8000
  pause
  exit /b
)
echo Installing packages (only slow the first time)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo Could not run pip. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
  pause
  exit /b
)
echo.
echo SabhaSaar is starting. Keep this window open while you use it.
echo Your browser will open http://localhost:8000
start "" cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8000"
python -m uvicorn app.main:app --port 8000
pause
