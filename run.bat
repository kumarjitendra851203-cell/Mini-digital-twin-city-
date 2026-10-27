@echo off
cd /d "%~dp0"
where py >nul 2>&1 && (set PY=py) || (set PY=python)
if not exist .venv (%PY% -m venv .venv)
call .venv\Scripts\activate
pip install -r requirements.txt
start "" http://127.0.0.1:5000
python app.py
pause
