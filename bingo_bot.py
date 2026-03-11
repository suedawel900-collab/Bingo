# bingo_bot.py
"""
MK BINGO Telegram Bot - Main bot file
"""
import os
import logging
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token from environment
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8578474198:AAGcqcyTihBMxV-gtqukkbU_SBk1EszG-7w")

# Simple in-memory database
users = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    user_id = user.id
    
    # Initialize new user
    if user_id not in users:
        users[user_id] = {
            'balance': 100,  # Welcome bonus
            'cards': [],
            'username': user.username,
            'first_name': user.first_name
        }
    
    welcome_text = (
        f"🎯 *WELCOME TO MK BINGO!* 🎯\n\n"
        f"👤 *Player:* {user.first_name}\n"
        f"💰 *Balance:* `{users[user_id]['balance']} ETB`\n"
        f"🎮 *Cards owned:* {len(users[user_id]['cards'])}\n\n"
        f"👇 *Choose an option:*"
    )
    
    keyboard = [
        [InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
         InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("🎮 Buy Card (10 ETB)", callback_data="buy_card"),
         InlineKeyboardButton("📊 My Cards", callback_data="my_cards")],
        [InlineKeyboardButton("🎯 Play Game", callback_data="play"),
         InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")],
        [InlineKeyboardButton("📈 Balance", callback_data="balance")]
    ]
    
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "deposit":
        await query.edit_message_text(
            "💰 *Deposit via TeleBirr*\n\n"
            "1. Send money to TeleBirr: `0922334455`\n"
            "2. Name: `MK BINGO`\n"
            "3. Send your transaction ID to @mk_admin\n\n"
            "Your balance will be updated within 5 minutes.",
            parse_mode="Markdown"
        )
    
    elif data == "withdraw":
        await query.edit_message_text(
            "💳 *Withdrawal*\n\n"
            "Minimum withdrawal: 20 ETB\n"
            "Contact @mk_admin to request withdrawal.\n\n"
            "Please provide:\n"
            "• Amount\n"
            "• TeleBirr number",
            parse_mode="Markdown"
        )
    
    elif data == "buy_card":
        if users[user_id]['balance'] < 10:
            await query.edit_message_text(
                "❌ *Insufficient Balance!*\n\n"
                f"Your balance: {users[user_id]['balance']} ETB\n"
                "Card price: 10 ETB\n\n"
                "Please deposit first.",
                parse_mode="Markdown"
            )
            return
        
        # Deduct balance and add card
        users[user_id]['balance'] -= 10
        card_number = len(users[user_id]['cards']) + 1
        users[user_id]['cards'].append(f"CARD_{card_number}")
        
        await query.edit_message_text(
            f"✅ *Card Purchased!*\n\n"
            f"Card #: {card_number}\n"
            f"Price: 10 ETB\n"
            f"New Balance: {users[user_id]['balance']} ETB\n\n"
            f"Use /start to see all options.",
            parse_mode="Markdown"
        )
    
    elif data == "my_cards":
        cards = users[user_id]['cards']
        if not cards:
            await query.edit_message_text("📭 You don't have any cards yet. Buy one first!")
        else:
            card_list = "\n".join([f"• {card}" for card in cards])
            await query.edit_message_text(
                f"📊 *Your Cards ({len(cards)}):*\n\n"
                f"{card_list}\n\n"
                f"Use /play to join a game!",
                parse_mode="Markdown"
            )
    
    elif data == "play":
        if not users[user_id]['cards']:
            await query.edit_message_text(
                "❌ You need cards to play!\n"
                "Buy cards first using the menu."
            )
        else:
            await query.edit_message_text(
                "🎯 *Game Started!*\n\n"
                "Waiting for other players...\n"
                "Game will start in 30 seconds.\n\n"
                "You will be notified when numbers are called.",
                parse_mode="Markdown"
            )
    
    elif data == "daily_bonus":
        users[user_id]['balance'] += 20
        await query.edit_message_text(
            f"🎁 *Daily Bonus Claimed!*\n\n"
            f"You received: 20 ETB\n"
            f"New Balance: {users[user_id]['balance']} ETB\n\n"
            f"Come back tomorrow for more!",
            parse_mode="Markdown"
        )
    
    elif data == "balance":
        await query.edit_message_text(
            f"💰 *Your Balance*\n\n"
            f"Current: {users[user_id]['balance']} ETB\n"
            f"Cards owned: {len(users[user_id]['cards'])}\n\n"
            f"Total spent: {users[user_id].get('total_spent', 0)} ETB",
            parse_mode="Markdown"
        )

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check command"""
    await update.message.reply_text("✅ Bot is running! Health OK.")

def main():
    """Main bot function"""
    logger.info("🤖 Starting MK BINGO Bot...")
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Start bot
    logger.info("✅ Bot started, polling for updates...")
    app.run_polling()

if __name__ == "__main__":
    main()