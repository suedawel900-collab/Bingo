import os
import sys
import threading
import time
import logging
import subprocess
import signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_webapp():
    """Run FastAPI webapp using uvicorn"""
    import uvicorn
    port = int(os.getenv('PORT', 8080))
    logger.info(f"🌐 Starting webapp on port {port}")
    uvicorn.run(
        "webapp:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )

def run_bot():
    """Run Telegram bot"""
    import bot
    logger.info("🤖 Starting bot")
    bot.main()

if __name__ == "__main__":
    logger.info("🚀 Starting Bingo Bot services...")
    
    # Create threads for both services
    webapp_thread = threading.Thread(target=run_webapp, daemon=True)
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    
    # Start both services
    webapp_thread.start()
    bot_thread.start()
    
    # Handle shutdown gracefully
    def signal_handler(sig, frame):
        logger.info("🛑 Shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
            if not webapp_thread.is_alive():
                logger.error("❌ Webapp thread died! Restarting...")
                webapp_thread = threading.Thread(target=run_webapp, daemon=True)
                webapp_thread.start()
            if not bot_thread.is_alive():
                logger.error("❌ Bot thread died! Restarting...")
                bot_thread = threading.Thread(target=run_bot, daemon=True)
                bot_thread.start()
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
        sys.exit(0)