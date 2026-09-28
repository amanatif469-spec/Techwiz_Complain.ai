@echo off
rem Start the SupportNova React frontend on http://localhost:5173
cd /d "%~dp0..\frontend"
npx vite --port 5173 --strictPort
