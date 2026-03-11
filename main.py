# main.py
import os
import sys
import logging
import threading
import time
import signal
import asyncio
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
        server.handle_request()
    
    logger.info("🛑 Health server stopped")

def run_bot():
    """Run the Telegram bot in a separate thread with its own event loop"""
    try:
        # Import the bot
        import bingo_bot
        
        # Run bot main function (which creates its own event loop)
        bingo_bot.main()
        
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

# Start health server in a daemon thread
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Start bot in a daemon thread
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

logger.info("🚀 Application started successfully")

# Keep main thread alive and monitor bot thread
try:
    while running:
        time.sleep(1)
        
        # Check if bot thread died
        if not bot_thread.is_alive():
            logger.error("❌ Bot thread died! Restarting...")
            # Restart bot thread
            bot_thread = threading.Thread(target=run_bot, daemon=True)
            bot_thread.start()
            
except KeyboardInterrupt:
    logger.info("Keyboard interrupt received")
    running = False

logger.info("👋 Application stopped")
sys.exit(0)