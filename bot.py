# bingo_bot.py
import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token from environment
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not set!")
    BOT_TOKEN = "8578474198:AAGcqcyTihBMxV-gtqukkbU_SBk1EszG-7w"  # Fallback

# Store user data (use database in production)
user_balances = {}
user_cards = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when /start is issued."""
    user = update.effective_user
    user_id = user.id
    
    # Initialize user if new
    if user_id not in user_balances:
        user_balances[user_id] = 100  # Welcome bonus
        user_cards[user_id] = []
    
    welcome_text = (
        f"🎯 *WELCOME TO MK BINGO!* 🎯\n\n"
        f"👤 *Player:* {user.first_name}\n"
        f"💰 *Balance:* `{user_balances[user_id]} ETB`\n"
        f"🎮 *Cards owned:* {len(user_cards[user_id])}\n\n"
        f"👇 *Choose an option:*"
    )
    
    keyboard = [
        [InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
         InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("🎮 Buy Cards", callback_data="buy_cards"),
         InlineKeyboardButton("📊 My Cards", callback_data="my_cards")],
        [InlineKeyboardButton("🎯 Play Game", callback_data="play_game"),
         InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")]
    ]
    
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if query.data == "deposit":
        await query.edit_message_text(
            "💰 *Deposit via TeleBirr*\n\n"
            "To deposit, send money to:\n"
            "📱 *0922334455*\n"
            "💳 *Account: MK BINGO*\n\n"
            "Your balance will update automatically after payment.",
            parse_mode="Markdown"
        )
        
    elif query.data == "withdraw":
        await query.edit_message_text(
            "💳 *Withdrawal Request*\n\n"
            "Please contact @mk_admin to request withdrawal.\n"
            "Minimum withdrawal: 20 ETB",
            parse_mode="Markdown"
        )
        
    elif query.data == "buy_cards":
        if user_balances[user_id] < 10:
            await query.edit_message_text(
                "❌ Insufficient balance!\n"
                "Minimum balance needed: 10 ETB",
                parse_mode="Markdown"
            )
            return
            
        user_balances[user_id] -= 10
        user_cards[user_id].append(f"CARD_{len(user_cards[user_id])+1}")
        
        await query.edit_message_text(
            f"✅ *Card Purchased!*\n\n"
            f"Card #: {len(user_cards[user_id])}\n"
            f"Price: 10 ETB\n"
            f"Remaining Balance: {user_balances[user_id]} ETB\n\n"
            f"Use /start to see your cards.",
            parse_mode="Markdown"
        )
        
    elif query.data == "my_cards":
        cards = user_cards.get(user_id, [])
        if not cards:
            await query.edit_message_text("You don't have any cards yet!")
        else:
            card_list = "\n".join([f"• {card}" for card in cards])
            await query.edit_message_text(
                f"📊 *Your Cards:*\n{card_list}\n\nTotal: {len(cards)} cards",
                parse_mode="Markdown"
            )
            
    elif query.data == "play_game":
        if not user_cards.get(user_id):
            await query.edit_message_text("Buy cards first using /start!")
        else:
            await query.edit_message_text(
                "🎯 *Game Started!*\n\n"
                "Numbers will be called soon...",
                parse_mode="Markdown"
            )
            
    elif query.data == "daily_bonus":
        user_balances[user_id] += 20
        await query.edit_message_text(
            f"🎁 *Daily Bonus Claimed!*\n\n"
            f"You received: 20 ETB\n"
            f"New Balance: {user_balances[user_id]} ETB",
            parse_mode="Markdown"
        )

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check command"""
    await update.message.reply_text("✅ Bot is healthy!")

def main():
    """Start the bot"""
    logger.info("🤖 Starting MK BINGO Bot...")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("health", health))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start bot with polling (simpler for Railway)
    logger.info("✅ Bot started, polling for updates...")
    application.run_polling()

if __name__ == "__main__":
    main()