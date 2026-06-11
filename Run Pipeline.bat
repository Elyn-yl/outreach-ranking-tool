@echo off
cd /d "%~dp0"

python run_pipeline.py

echo.
echo Pipeline finished.
pause
