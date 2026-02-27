#!/bin/bash

# Start webapp on main port
python -m uvicorn webapp:app --host 0.0.0.0 --port ${PORT:-8080} &
WEBAPP_PID=$!

# Start bot
python bot.py &
BOT_PID=$!

# Handle shutdown
trap "kill $WEBAPP_PID $BOT_PID; exit" SIGINT SIGTERM

# Wait for processes
wait $WEBAPP_PID
wait $BOT_PID