# main.py
import os
import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Main entry point for Railway deployment"""
    logger.info("🚀 Starting Bingo Bot Application...")
    
    # Check for required environment variables
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        logger.error("❌ BOT_TOKEN environment variable not set!")
        sys.exit(1)
    
    logger.info(f"✅ Bot token found: {bot_token[:5]}...")
    
    # Import and run your bot here
    try:
        # Import your bot module
        from bingo_bot import main as bot_main
        
        # Run the bot
        bot_main()
    except ImportError as e:
        logger.error(f"❌ Failed to import bot module: {e}")
        logger.info("Creating a simple health server instead...")
        run_health_server()

def run_health_server():
    """Run a simple HTTP server for health checks"""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading
    
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
            # Suppress log messages
            pass
    
    # Start HTTP server in a separate thread
    def run_server():
        server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
        logger.info("✅ Health check server running on port 8000")
        server.serve_forever()
    
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # Keep the main thread alive
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")

if __name__ == "__main__":
    main()