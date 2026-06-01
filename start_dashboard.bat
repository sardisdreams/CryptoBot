@echo off
title CryptoBot Dashboard
cd /d F:\CryptoBot
call .venv\Scripts\activate
echo Starting Dashboard at http://localhost:5000
python dashboard.py
pause
