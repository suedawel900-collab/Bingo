# main.py
import os
import sys
import logging
import threading
import time
import signal
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global flag to keep running
running = True

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    global running
    logger.info("🛑 Shutdown signal received, stopping gracefully...")
    running = False

# Register signal handlers
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
        pass  # Suppress logs

def run_health_server():
    """Run health check server"""
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"✅ Health server running on port {port}")
    
    # Run server in a non-blocking way
    server.timeout = 1
    while running:
        server.handle_request()
    
    logger.info("🛑 Health server stopped")

def run_bot():
    """Run the Telegram bot"""
    try:
        from bingo_bot import main as bot_main
        
        # Run bot in a way that can be interrupted
        bot_thread = threading.Thread(target=bot_main, daemon=True)
        bot_thread.start()
        logger.info("✅ Bot thread started")
        
        # Keep bot thread alive
        while bot_thread.is_alive() and running:
            time.sleep(1)
            
    except ImportError as e:
        logger.error(f"❌ Failed to import bot: {e}")
        logger.info("Running with minimal functionality...")
        
        # Even without bot, keep running
        while running:
            time.sleep(1)
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")

# Start health server in background
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Start bot
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

logger.info("🚀 Application started successfully")

# Keep main thread alive
try:
    while running:
        time.sleep(1)
except KeyboardInterrupt:
    logger.info("Keyboard interrupt received")
    running = False

logger.info("👋 Application stopped")
sys.exit(0)