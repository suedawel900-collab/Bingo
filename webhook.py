import os
import requests
import logging
from bot import main as bot_main

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
BASE_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'bingo-production-a078.up.railway.app')
WEBHOOK_URL = f"https://{BASE_URL}/webhook"

def set_webhook():
    """Set Telegram webhook"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    data = {
        "url": WEBHOOK_URL,
        "allowed_updates": ["message", "callback_query", "chat_member"]
    }
    
    response = requests.post(url, json=data)
    if response.status_code == 200:
        logger.info(f"✅ Webhook set to: {WEBHOOK_URL}")
        logger.info(f"Response: {response.json()}")
    else:
        logger.error(f"❌ Failed to set webhook: {response.text}")

def delete_webhook():
    """Delete existing webhook"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
    response = requests.post(url)
    if response.status_code == 200:
        logger.info("✅ Webhook deleted")
    else:
        logger.error(f"❌ Failed to delete webhook: {response.text}")

def get_webhook_info():
    """Get current webhook info"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
    response = requests.get(url)
    if response.status_code == 200:
        info = response.json()
        logger.info(f"📡 Webhook info: {info}")
        return info
    return None

if __name__ == "__main__":
    # First delete any existing webhook
    delete_webhook()
    # Set new webhook
    set_webhook()
    # Verify
    get_webhook_info()