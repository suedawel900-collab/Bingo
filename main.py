# main.py
"""
MK BINGO - Main Entry Point
Runs health server on port 8080, web app on port 8000, and Telegram bot
"""

import os
import sys
import logging
import threading
import time
import signal
import uvicorn
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
running = True

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global running
    logger.info("🛑 Shutdown signal received, stopping gracefully...")
    running = False

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

class HealthHandler(BaseHTTPRequestHandler):
    """Simple health check server for Railway"""
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress log messages
        pass

def run_health_server():
    """Run health check server on port 8080"""
    try:
        port = 8080  # Fixed port for health checks
        server = HTTPServer(('0.0.0.0', port), HealthHandler)
        logger.info(f"✅ Health server running on port {port}")
        
        # Set timeout to allow checking running flag
        server.timeout = 1
        
        while running:
            try:
                server.handle_request()
            except Exception as e:
                if running:
                    logger.error(f"Health server error: {e}")
                pass
        
        server.server_close()
        logger.info("🛑 Health server stopped")
    except Exception as e:
        logger.error(f"Failed to start health server: {e}")

def run_web_app():
    """Run the FastAPI web app on port 8000"""
    try:
        # Import here to avoid circular imports
        import web_app
        
        port = 8000  # FIXED: Use port 8000 for web app, NOT 8080
        logger.info(f"🌐 Starting web app on port {port}")
        
        # Run uvicorn with the app
        uvicorn.run(
            web_app.app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            access_log=False
        )
    except ImportError as e:
        logger.error(f"❌ Failed to import web_app: {e}")
        logger.error("Make sure web_app.py exists in the current directory")
    except Exception as e:
        logger.error(f"❌ Web app error: {e}")
        import traceback
        traceback.print_exc()

def run_bot():
    """Run the Telegram bot"""
    try:
        import bingo_bot
        bingo_bot.main()
    except ImportError as e:
        logger.error(f"❌ Failed to import bingo_bot: {e}")
        logger.error("Make sure bingo_bot.py exists in the current directory")
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Main entry point"""
    logger.info("🚀 Starting MK BINGO Application...")
    
    # Start health server in a daemon thread
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Start web app in a daemon thread
    web_thread = threading.Thread(target=run_web_app, daemon=True)
    web_thread.start()
    
    # Start bot in a daemon thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    logger.info("✅ All services started successfully")
    logger.info("📱 Health check: http://localhost:8080/health")
    logger.info("🌐 Web app: http://localhost:8000/bingo")
    logger.info("🤖 Telegram bot: Running")
    
    # Monitor threads and keep main thread alive
    try:
        while running:
            time.sleep(5)
            
            # Check if threads are alive and restart if needed
            if not health_thread.is_alive():
                logger.error("❌ Health server died! Restarting...")
                health_thread = threading.Thread(target=run_health_server, daemon=True)
                health_thread.start()
            
            if not web_thread.is_alive():
                logger.error("❌ Web app died! Restarting...")
                web_thread = threading.Thread(target=run_web_app, daemon=True)
                web_thread.start()
            
            if not bot_thread.is_alive():
                logger.error("❌ Bot died! Restarting...")
                bot_thread = threading.Thread(target=run_bot, daemon=True)
                bot_thread.start()
                
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        running = False
    
    logger.info("👋 Application stopped")
    sys.exit(0)

if __name__ == "__main__":
    main()