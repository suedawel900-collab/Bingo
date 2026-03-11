# bingo_bot.py
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment
BOT_TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "🎯 Welcome to MK BINGO Bot!\n\n"
        "The bot is running properly.\n"
        "Full features coming soon!"
    )

async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check command"""
    await update.message.reply_text("✅ Bot is healthy!")

def main():
    """Start the bot"""
    if not BOT_TOKEN:
        logger.error("No BOT_TOKEN provided!")
        return
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("health", health_check))
    
    # Start bot
    logger.info("🤖 Bingo bot started successfully!")
    application.run_polling()

if __name__ == '__main__':
    main()