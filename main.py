import os
import sys
import asyncio
import threading
import logging
import uvicorn
from concurrent.futures import ThreadPoolExecutor
import signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global flag for shutdown
shutdown_flag = False

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    global shutdown_flag
    logger.info("🛑 Shutdown signal received...")
    shutdown_flag = True
    sys.exit(0)

def run_webapp():
    """Run FastAPI webapp"""
    try:
        port = int(os.getenv('PORT', 8080))
        logger.info(f"🌐 Starting webapp on port {port}")
        uvicorn.run(
            "webapp:app",
            host="0.0.0.0",
            port=port,
            log_level="info"
        )
    except Exception as e:
        logger.error(f"❌ Webapp error: {e}")
        if not shutdown_flag:
            sys.exit(1)

def run_bot():
    """Run Telegram bot with proper event loop"""
    try:
        logger.info("🤖 Starting bot")
        
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Import bot module
        import bot
        
        # Run bot in the event loop
        loop.run_until_complete(bot.async_main())
        
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        if not shutdown_flag:
            sys.exit(1)
    finally:
        try:
            loop.close()
        except:
            pass

async def run_bot_async():
    """Async wrapper for bot"""
    import bot
    await bot.async_main()

def main():
    """Main entry point"""
    logger.info("🚀 Starting Bingo Bot services...")
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start webapp in a separate thread
    webapp_thread = threading.Thread(target=run_webapp, daemon=True)
    webapp_thread.start()
    
    # Run bot in main thread with asyncio
    try:
        asyncio.run(run_bot_async())
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()