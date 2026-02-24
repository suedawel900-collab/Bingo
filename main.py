import os
import sys
import threading
import time
import logging
import uvicorn
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "online", "service": "Bingo Bot", "message": "Bot is running"}

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": time.time()}

def run_bot():
    """Run the Telegram bot"""
    try:
        import bot
        bot.main()
    except Exception as e:
        logger.error(f"Bot error: {e}")

def run_webapp():
    """Run the webapp server"""
    try:
        port = int(os.getenv('PORT', 8080))
        uvicorn.run(
            "webapp:app",
            host="0.0.0.0",
            port=port,
            log_level="info"
        )
    except Exception as e:
        logger.error(f"Webapp error: {e}")

if __name__ == "__main__":
    logger.info("🚀 Starting Bingo Bot services...")
    
    # Start bot in separate thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Run webapp in main thread
    run_webapp()