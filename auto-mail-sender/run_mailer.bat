@echo off
title EISTATECH Auto Mailer
echo ===================================================
echo             EISTATECH SEQUENCE MAILER
echo ===================================================
echo.
echo [1/4] Checking python libraries...
python -m pip install -r requirements.txt
echo.
echo [2/4] Starting background sequence worker...
start "Sequence Worker" cmd /k python sequence_worker.py
echo.
echo [3/4] Starting web browser dashboard...
start http://127.0.0.1:5001
echo.
echo [4/4] Running web app...
echo The sequence worker runs in a separate window.
echo You can close the browser after launching a sequence.
echo.
set ENABLE_INLINE_WORKER=0
python web_app.py
pause
