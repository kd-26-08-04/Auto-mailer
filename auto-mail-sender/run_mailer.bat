@echo off
title EISTATECH Auto Mailer
echo ===================================================
echo             EISTATECH AUTO MAILER
echo ===================================================
echo.
echo [1/3] Checking python libraries...
python -m pip install -r requirements.txt
echo.
echo [2/3] Starting web browser dashboard...
start http://127.0.0.1:5001
echo.
echo [3/3] Running backend mailing engine...
echo (Keep this terminal window open while using the mailer)
echo.
python web_app.py
pause
