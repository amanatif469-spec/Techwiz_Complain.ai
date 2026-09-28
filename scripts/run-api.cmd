@echo off
rem Start the SupportNova FastAPI backend on http://localhost:8000
rem Uses the project virtual environment when present (see documentation\INSTALLATION.md).
cd /d "%~dp0.."
set "PY=python"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -m uvicorn src.main:app --host 127.0.0.1 --port 8000
