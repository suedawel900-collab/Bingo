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

# Global bot application
bot_app = None

def setup_bot_application():
    """Setup Telegram bot application"""
    from bot import setup_handlers, BOT_TOKEN
    
    application = Application.builder().token(BOT_TOKEN).build()
    setup_handlers(application)
    return application

@app.on_event("startup")
async def startup_event():
    """Initialize bot on startup"""
    global bot_app
    logger.info("🚀 Starting up...")
    bot_app = setup_bot_application()
    await bot_app.initialize()
    logger.info("✅ Bot initialized")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global bot_app
    if bot_app:
        await bot_app.shutdown()
    logger.info("👋 Shutting down...")

@app.post("/webhook")
async def webhook(request: Request):
    """Handle Telegram webhook"""
    global bot_app
    if not bot_app:
        return {"status": "error", "message": "Bot not initialized"}
    
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "bingo-bot",
        "timestamp": time.time()
    }

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Bingo Bot",
        "version": "1.0.0",
        "status": "running"
    }

def run_webhook_server():
    """Run FastAPI server for webhooks"""
    port = int(os.getenv('PORT', 8080))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )

def run_polling_bot():
    """Run bot in polling mode (fallback)"""
    from bot import main as bot_main
    bot_main()

if __name__ == "__main__":
    # Check environment
    USE_WEBHOOK = os.getenv('USE_WEBHOOK', 'true').lower() == 'true'
    
    if USE_WEBHOOK:
        logger.info("🌐 Starting in WEBHOOK mode")
        run_webhook_server()
    else:
        logger.info("🔄 Starting in POLLING mode")
        run_polling_bot()