#!/bin/bash

echo "🚀 Setting up Bingo Bot..."

# Create directories
mkdir -p templates static

echo "✅ Directory structure created"

# Install dependencies
pip install -r requirements.txt

echo "✅ Dependencies installed"

# Initialize database
python -c "from models import Database; Database()"

echo "✅ Database initialized"

echo ""
echo "🎯 Setup complete! Next steps:"
echo "1. Update .env with your API keys"
echo "2. Run: python bot.py & uvicorn webapp:app --reload --port 8001 & uvicorn payments:app --reload --port 8000"
echo "3. Set Telegram webhook: https://api.telegram.org/bot<TOKEN>/setWebhook?url=<YOUR_URL>/webhook"
echo ""
echo "📱 Your bot is ready to deploy!"