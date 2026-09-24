@echo off
REM Sayso: local push-to-talk dictation.
REM   run.cmd              tray app, hold Right Ctrl to dictate
REM   run.cmd --devices    list microphones
REM   run.cmd --self-test  prove the whole chain works
cd /d "%~dp0"
uv run python -m sayso %*
