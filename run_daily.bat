@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -u pipeline.py >> "data\daily_run.log" 2>&1
