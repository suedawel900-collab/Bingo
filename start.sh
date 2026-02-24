#!/bin/bash

# Start webapp in background
python -m uvicorn webapp:app --host 0.0.0.0 --port ${PORT:-8080} &
WEBAPP_PID=$!

# Start bot in foreground
python bot.py &
BOT_PID=$!

# Handle shutdown
trap "kill $WEBAPP_PID $BOT_PID; exit" SIGINT SIGTERM

# Wait for processes
wait $WEBAPP_PID
wait $BOT_PID