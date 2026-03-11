# bingo_bot.py
"""
MK BINGO Telegram Bot - Production Ready Version
"""
import os
import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, Any
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

# Simple file-based database (to persist between restarts)
DB_FILE = "bot_database.json"

class Database:
    """Simple JSON database for bot data"""
    def __init__(self, filename=DB_FILE):
        self.filename = filename
        self.data = self.load()
    
    def load(self):
        """Load data from file"""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading database: {e}")
        
        # Default structure
        return {
            "users": {},
            "games": [],
            "statistics": {
                "total_users": 0,
                "total_transactions": 0
            }
        }
    
    def save(self):
        """Save data to file"""
        try:
            with open(self.filename, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving database: {e}")
    
    def get_user(self, user_id, user_data=None):
        """Get or create user"""
        user_id_str = str(user_id)
        
        if user_id_str not in self.data["users"]:
            self.data["users"][user_id_str] = {
                'balance': 100,  # Welcome bonus
                'cards': [],
                'username': user_data.username if user_data else None,
                'first_name': user_data.first_name if user_data else None,
                'joined_at': datetime.now().isoformat(),
                'last_active': datetime.now().isoformat(),
                'total_spent': 0,
                'total_won': 0,
                'games_played': 0
            }
            self.data["statistics"]["total_users"] += 1
            self.save()
        
        # Update last active
        self.data["users"][user_id_str]['last_active'] = datetime.now().isoformat()
        if user_data:
            self.data["users"][user_id_str]['username'] = user_data.username
            self.data["users"][user_id_str]['first_name'] = user_data.first_name
        
        self.save()
        return self.data["users"][user_id_str]

# Initialize database
db = Database()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully"""
    logger.error(f"Update {update} caused error {context.error}")
    
    # Try to notify user
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "❌ An error occurred. Please try again or contact support."
            )
        except:
            pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    user_id = user.id
    
    # Get or create user
    db_user = db.get_user(user_id, user)
    
    welcome_text = (
        f"🎯 *WELCOME TO MK BINGO!* 🎯\n\n"
        f"👤 *Player:* {user.first_name}\n"
        f"💰 *Balance:* `{db_user['balance']} ETB`\n"
        f"🎮 *Cards owned:* {len(db_user['cards'])}\n\n"
        f"👇 *Choose an option:*"
    )
    
    keyboard = [
        [InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
         InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("🎮 Buy Card (10 ETB)", callback_data="buy_card"),
         InlineKeyboardButton("📊 My Cards", callback_data="my_cards")],
        [InlineKeyboardButton("🎯 Play Game", callback_data="play"),
         InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")],
        [InlineKeyboardButton("📈 Balance", callback_data="balance"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
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
    user = update.effective_user
    
    # Get or create user (ensure user exists in database)
    db_user = db.get_user(user_id, user)
    
    data = query.data
    logger.info(f"Button pressed: {data} by user {user_id}")
    
    try:
        if data == "deposit":
            await query.edit_message_text(
                "💰 *Deposit via TeleBirr*\n\n"
                "1. Send money to TeleBirr: `0922334455`\n"
                "2. Name: `MK BINGO`\n"
                "3. Amount: Enter the amount you want to deposit\n"
                "4. Send your transaction ID to @mk_admin\n\n"
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
            if db_user['balance'] < 10:
                await query.edit_message_text(
                    "❌ *Insufficient Balance!*\n\n"
                    f"Your balance: {db_user['balance']} ETB\n"
                    "Card price: 10 ETB\n\n"
                    "Please deposit first.",
                    parse_mode="Markdown"
                )
                return
            
            # Deduct balance and add card
            db_user['balance'] -= 10
            card_number = len(db_user['cards']) + 1
            db_user['cards'].append({
                'id': card_number,
                'purchased_at': datetime.now().isoformat(),
                'numbers': []  # Will store bingo numbers
            })
            db_user['total_spent'] = db_user.get('total_spent', 0) + 10
            db.save()
            
            await query.edit_message_text(
                f"✅ *Card Purchased!*\n\n"
                f"Card #: {card_number}\n"
                f"Price: 10 ETB\n"
                f"New Balance: {db_user['balance']} ETB\n\n"
                f"Use /start to see all options.",
                parse_mode="Markdown"
            )
        
        elif data == "my_cards":
            cards = db_user['cards']
            if not cards:
                await query.edit_message_text(
                    "📭 You don't have any cards yet.\n"
                    "Use the 'Buy Card' button to purchase one!"
                )
            else:
                card_list = "\n".join([f"• Card #{c['id']}" for c in cards])
                await query.edit_message_text(
                    f"📊 *Your Cards ({len(cards)}):*\n\n"
                    f"{card_list}\n\n"
                    f"Use /play to join a game!",
                    parse_mode="Markdown"
                )
        
        elif data == "play":
            if not db_user['cards']:
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
            # Check if already claimed today
            last_bonus = db_user.get('last_bonus')
            today = datetime.now().date().isoformat()
            
            if last_bonus == today:
                await query.edit_message_text(
                    "❌ You've already claimed your daily bonus today!\n"
                    "Come back tomorrow.",
                    parse_mode="Markdown"
                )
                return
            
            # Give bonus
            db_user['balance'] += 20
            db_user['last_bonus'] = today
            db.save()
            
            await query.edit_message_text(
                f"🎁 *Daily Bonus Claimed!*\n\n"
                f"You received: 20 ETB\n"
                f"New Balance: {db_user['balance']} ETB\n\n"
                f"Come back tomorrow for more!",
                parse_mode="Markdown"
            )
        
        elif data == "balance":
            await query.edit_message_text(
                f"💰 *Your Balance*\n\n"
                f"Current: {db_user['balance']} ETB\n"
                f"Cards owned: {len(db_user['cards'])}\n"
                f"Total spent: {db_user.get('total_spent', 0)} ETB\n"
                f"Total won: {db_user.get('total_won', 0)} ETB",
                parse_mode="Markdown"
            )
        
        elif data == "help":
            help_text = (
                "❓ *Help & Support*\n\n"
                "• *Deposit*: Send money via TeleBirr\n"
                "• *Buy Cards*: Purchase bingo cards (10 ETB each)\n"
                "• *Play*: Join active games\n"
                "• *Daily Bonus*: Get free ETB every day\n\n"
                "For support: @mk_admin"
            )
            await query.edit_message_text(help_text, parse_mode="Markdown")
    
    except Exception as e:
        logger.error(f"Error in button_handler: {e}")
        await query.edit_message_text(
            "❌ An error occurred. Please try again or contact support."
        )

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check command"""
    stats = db.data["statistics"]
    await update.message.reply_text(
        f"✅ *Bot is healthy!*\n\n"
        f"Users: {stats['total_users']}\n"
        f"Uptime: Running smoothly",
        parse_mode="Markdown"
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot statistics (admin only)"""
    user_id = update.effective_user.id
    
    # Simple admin check (you can expand this)
    if str(user_id) not in ["6975815871"]:  # Add your admin IDs
        await update.message.reply_text("❌ Unauthorized")
        return
    
    stats = db.data["statistics"]
    users = db.data["users"]
    
    text = (
        f"📊 *Bot Statistics*\n\n"
        f"Total Users: {stats['total_users']}\n"
        f"Active Users: {len([u for u in users.values() if u.get('cards')])}\n"
        f"Total Cards: {sum(len(u.get('cards', [])) for u in users.values())}\n"
        f"Total Balance: {sum(u.get('balance', 0) for u in users.values())} ETB\n"
    )
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def main_async():
    """Async main function"""
    logger.info("🤖 Starting MK BINGO Bot...")
    
    # Create application with custom settings
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Start bot
    logger.info("✅ Bot started, polling for updates...")
    
    # Use run_polling with proper settings to avoid conflicts
    await app.initialize()
    await app.start()
    
    # Start with drop_pending_updates=True to clear old updates
    await app.updater.start_polling(drop_pending_updates=True)
    
    # Keep running
    logger.info("🚀 Bot is running!")
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Bot stopping...")
    finally:
        # Stop gracefully
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        db.save()
        logger.info("👋 Bot stopped")

def main():
    """Entry point for bot"""
    # Create new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        loop.close()

if __name__ == "__main__":
    main()