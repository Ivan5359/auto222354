@echo off
setlocal
cd /d "%~dp0"

if not exist telegram_bot_token.txt (
  echo Telegram bot token file not found.
  echo.
  echo Create a file named telegram_bot_token.txt in this folder.
  echo Paste your BotFather token inside it.
  echo.
  pause
  exit /b 1
)

python telegram_outreach_bot.py

echo.
echo Bot stopped.
pause
