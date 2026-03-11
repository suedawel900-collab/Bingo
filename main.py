# main.py
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

# Global flag
running = True

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    global running
    logger.info("🛑 Shutdown signal received, stopping gracefully...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

class HealthHandler(BaseHTTPRequestHandler):
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
        pass

def run_health_server():
    """Run health check server"""
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"✅ Health server running on port {port}")
    
    server.timeout = 1
    while running:
        try:
            server.handle_request()
        except:
            pass
    
    logger.info("🛑 Health server stopped")

def run_web_app():
    """Run the FastAPI web app"""
    try:
        import web_app
        port = int(os.environ.get('PORT', 8000))
        uvicorn.run(web_app.app, host="0.0.0.0", port=port, log_level="info")
    except Exception as e:
        logger.error(f"❌ Web app error: {e}")
        logger.error("Make sure web_app.py exists in the current directory")

def run_bot():
    """Run the Telegram bot"""
    try:
        import bingo_bot
        bingo_bot.main()
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

# Start health server
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Start web app
logger.info("🚀 Starting web app...")
web_thread = threading.Thread(target=run_web_app, daemon=True)
web_thread.start()

# Start bot
logger.info("🚀 Starting bot...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

logger.info("✅ All services started successfully")

# Monitor threads
try:
    while running:
        time.sleep(5)
        
        # Check if threads died
        if not web_thread.is_alive():
            logger.error("❌ Web app thread died! Restarting...")
            web_thread = threading.Thread(target=run_web_app, daemon=True)
            web_thread.start()
        
        if not bot_thread.is_alive():
            logger.error("❌ Bot thread died! Restarting...")
            bot_thread = threading.Thread(target=run_bot, daemon=True)
            bot_thread.start()
        
        if not health_thread.is_alive():
            logger.error("❌ Health server died! Restarting...")
            health_thread = threading.Thread(target=run_health_server, daemon=True)
            health_thread.start()
            
except KeyboardInterrupt:
    logger.info("Keyboard interrupt received")
    running = False

logger.info("👋 Application stopped")
sys.exit(0)