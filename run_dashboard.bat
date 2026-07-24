@echo off
rem Launcher for the financial dashboard (Streamlit).
rem Uses the PORT env var when provided (preview servers); defaults to 8502.
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8502
".venv_dashboard\Scripts\python.exe" -m streamlit run dashboard_app.py --server.headless true --server.port %PORT%
