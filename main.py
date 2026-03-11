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
    """Run the Telegram bot"""
    try:
        # Add the current directory to Python path
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        
        # Import the bot
        from bingo_bot import main as bot_main
        
        # Run bot in a separate thread
        bot_thread = threading.Thread(target=bot_main, daemon=True)
        bot_thread.start()
        logger.info("✅ Bot thread started")
        
        # Monitor bot thread
        while bot_thread.is_alive() and running:
            time.sleep(1)
            
    except ImportError as e:
        logger.error(f"❌ Failed to import bot: {e}")
        logger.info("Make sure bingo_bot.py exists in the current directory")
        logger.info("Running with minimal functionality...")
        
        # Keep running even without bot
        while running:
            time.sleep(1)
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        while running:
            time.sleep(1)

# Start health server
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Start bot
logger.info("🚀 Application started successfully")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Keep main thread alive
try:
    while running:
        time.sleep(1)
except KeyboardInterrupt:
    logger.info("Keyboard interrupt received")
    running = False

logger.info("👋 Application stopped")
sys.exit(0)