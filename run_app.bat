@echo off
echo Starting KishanX Trading System...
echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat
echo.
echo Starting Flask application...
python app.py
echo.
echo Application stopped. Press any key to exit.
pause
