# main.py
import os
import sys
import logging
import threading
import uvicorn
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Simple health check server
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
    """Run health check server on Railway PORT"""
    port = int(os.environ.get('PORT', 8000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"✅ Health server running on port {port}")
    server.serve_forever()

# Start health server in background
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Import and run your main application
try:
    # Try to import your FastAPI app if it exists
    from web_app import app
    import uvicorn
    
    # Run FastAPI in another thread
    def run_fastapi():
        port = int(os.environ.get('PORT', 8000))
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    
    api_thread = threading.Thread(target=run_fastapi, daemon=True)
    api_thread.start()
    logger.info("✅ FastAPI web app started")
    
except ImportError:
    logger.info("No FastAPI app found, running bot only")

# Keep main thread alive
try:
    while True:
        import time
        time.sleep(1)
except KeyboardInterrupt:
    logger.info("Shutting down...")
    sys.exit(0)
