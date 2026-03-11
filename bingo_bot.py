# bingo_bot.py
"""
MK BINGO Telegram Bot - Complete Production Version
Includes: Card purchases, Web App integration, Admin controls, Database persistence
"""

import os
import sys
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8578474198:AAGcqcyTihBMxV-gtqukkbU_SBk1EszG-7w")
ADMIN_IDS = [6975815871]  # Your admin user ID
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://web-production-9a8f.up.railway.app")  # Your Railway URL

# Game settings
CARD_PRICE = 10  # ETB per card
DAILY_BONUS = 20  # ETB
WELCOME_BONUS = 100  # ETB
MIN_WITHDRAWAL = 20  # ETB

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
class Database:
    """JSON-based database with auto-save"""
    
    def __init__(self, filename="bot_database.json"):
        self.filename = filename
        self.data = self._load()
        self._last_save = datetime.now()
        self._auto_save_interval = 60  # Auto-save every 60 seconds
        
    def _load(self) -> dict:
        """Load data from file"""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"✅ Database loaded: {len(data.get('users', {}))} users")
                    return data
        except Exception as e:
            logger.error(f"Error loading database: {e}")
        
        # Default structure
        return {
            "users": {},
            "games": [],
            "withdrawals": [],
            "transactions": [],
            "statistics": {
                "total_users": 0,
                "total_cards_sold": 0,
                "total_revenue": 0,
                "total_withdrawals": 0,
                "created_at": datetime.now().isoformat()
            },
            "settings": {
                "card_price": CARD_PRICE,
                "daily_bonus": DAILY_BONUS,
                "welcome_bonus": WELCOME_BONUS,
                "min_withdrawal": MIN_WITHDRAWAL
            }
        }
    
    def save(self, force=False):
        """Save data to file"""
        now = datetime.now()
        if not force and (now - self._last_save).seconds < self._auto_save_interval:
            return
        
        try:
            # Create backup before saving
            if os.path.exists(self.filename):
                backup_name = f"{self.filename}.backup"
                if not os.path.exists(backup_name):
                    import shutil
                    shutil.copy2(self.filename, backup_name)
            
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            
            self._last_save = now
            logger.debug("💾 Database saved")
        except Exception as e:
            logger.error(f"Error saving database: {e}")
    
    def get_user(self, user_id: int, user_data=None) -> dict:
        """Get or create user"""
        user_id_str = str(user_id)
        
        if user_id_str not in self.data["users"]:
            # New user
            self.data["users"][user_id_str] = {
                "user_id": user_id,
                "username": user_data.username if user_data else None,
                "first_name": user_data.first_name if user_data else None,
                "balance": WELCOME_BONUS,
                "cards": [],
                "joined_at": datetime.now().isoformat(),
                "last_active": datetime.now().isoformat(),
                "games_played": 0,
                "games_won": 0,
                "total_spent": 0,
                "total_won": 0,
                "daily_bonus_claimed": None,
                "is_admin": user_id in ADMIN_IDS,
                "is_banned": False,
                "notes": ""
            }
            self.data["statistics"]["total_users"] += 1
            self.save()
            logger.info(f"🆕 New user registered: {user_id}")
        
        # Update last active
        self.data["users"][user_id_str]["last_active"] = datetime.now().isoformat()
        if user_data:
            self.data["users"][user_id_str]["username"] = user_data.username
            self.data["users"][user_id_str]["first_name"] = user_data.first_name
        
        return self.data["users"][user_id_str]
    
    def update_user(self, user_id: int, **kwargs):
        """Update user fields"""
        user_id_str = str(user_id)
        if user_id_str in self.data["users"]:
            self.data["users"][user_id_str].update(kwargs)
            self.save()
    
    def add_transaction(self, user_id: int, amount: float, type: str, status: str = "completed", details: dict = None):
        """Record a transaction"""
        transaction = {
            "id": len(self.data["transactions"]) + 1,
            "user_id": user_id,
            "amount": amount,
            "type": type,  # deposit, withdrawal, purchase, bonus, win
            "status": status,
            "details": details or {},
            "created_at": datetime.now().isoformat()
        }
        self.data["transactions"].append(transaction)
        self.save()
        return transaction
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        user = self.get_user(user_id)
        return user.get("is_admin", False) or user_id in ADMIN_IDS
    
    def is_banned(self, user_id: int) -> bool:
        """Check if user is banned"""
        user = self.get_user(user_id)
        return user.get("is_banned", False)

# Initialize database
db = Database()

# ==================== BINGO CARD GENERATOR ====================
def generate_bingo_card() -> list:
    """Generate a 5x5 bingo card with numbers 1-75"""
    import random
    
    card = []
    # Column ranges: B:1-15, I:16-30, N:31-45, G:46-60, O:61-75
    col_ranges = [(1, 15), (16, 30), (31, 45), (46, 60), (61, 75)]
    
    for col in range(5):
        numbers = random.sample(range(col_ranges[col][0], col_ranges[col][1] + 1), 5)
        card.append(numbers)
    
    # Free space in center
    card[2][2] = "FREE"
    
    return card

# ==================== ERROR HANDLER ====================
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

# ==================== COMMAND HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - Main menu"""
    user = update.effective_user
    db_user = db.get_user(user.id, user)
    
    # Check if banned
    if db_user.get("is_banned"):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    
    # Welcome message
    welcome_text = (
        f"🎯 *WELCOME TO MK BINGO!* 🎯\n\n"
        f"👤 *Player:* {user.first_name}\n"
        f"💰 *Balance:* `{db_user['balance']} ETB`\n"
        f"🎮 *Cards owned:* {len(db_user['cards'])}\n"
        f"📊 *Games played:* {db_user['games_played']}\n"
        f"🏆 *Games won:* {db_user['games_won']}\n\n"
        f"👇 *Choose an option:*"
    )
    
    # Main menu keyboard
    keyboard = [
        [InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
         InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("🎮 Buy Card", callback_data="buy_card"),
         InlineKeyboardButton("📊 My Cards", callback_data="my_cards")],
        [InlineKeyboardButton("🎯 Play Game", callback_data="play"),
         InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")],
        [InlineKeyboardButton("📈 Balance", callback_data="balance"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    
    # Admin button
    if db.is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    # Web app button if user has cards
    if db_user['cards']:
        keyboard.append([InlineKeyboardButton("🌐 Open Bingo Web App", web_app={"url": WEB_APP_URL})])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check command"""
    stats = db.data["statistics"]
    await update.message.reply_text(
        f"✅ *Bot is healthy!*\n\n"
        f"👥 Users: {stats['total_users']}\n"
        f"🎮 Cards sold: {stats.get('total_cards_sold', 0)}\n"
        f"💰 Revenue: {stats.get('total_revenue', 0)} ETB\n"
        f"⏰ Uptime: Online",
        parse_mode=ParseMode.MARKDOWN
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Statistics command (admin only)"""
    user_id = update.effective_user.id
    
    if not db.is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    stats = db.data["statistics"]
    users = db.data["users"]
    
    # Calculate stats
    active_today = sum(
        1 for u in users.values() 
        if datetime.fromisoformat(u['last_active']).date() == datetime.now().date()
    )
    
    total_balance = sum(u.get('balance', 0) for u in users.values())
    total_cards = sum(len(u.get('cards', [])) for u in users.values())
    
    text = (
        f"📊 *Bot Statistics*\n\n"
        f"👥 *Users:*\n"
        f"• Total: {stats['total_users']}\n"
        f"• Active today: {active_today}\n"
        f"• Online now: {active_today}\n\n"
        f"💰 *Economy:*\n"
        f"• Total balance: {total_balance} ETB\n"
        f"• Cards sold: {stats.get('total_cards_sold', 0)}\n"
        f"• Cards owned: {total_cards}\n"
        f"• Revenue: {stats.get('total_revenue', 0)} ETB\n\n"
        f"💳 *Withdrawals:*\n"
        f"• Total: {stats.get('total_withdrawals', 0)} ETB\n"
        f"• Pending: {len([w for w in db.data.get('withdrawals', []) if w['status'] == 'pending'])}\n"
    )
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ==================== BUTTON HANDLERS ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_id = user.id
    
    # Get or create user
    db_user = db.get_user(user_id, user)
    
    # Check if banned
    if db_user.get("is_banned"):
        await query.edit_message_text("❌ You are banned from using this bot.")
        return
    
    data = query.data
    logger.info(f"Button pressed: {data} by user {user_id}")
    
    try:
        # ===== DEPOSIT =====
        if data == "deposit":
            text = (
                "💰 *Deposit via TeleBirr*\n\n"
                "1. Send money to TeleBirr:\n"
                "   📱 `0922334455`\n"
                "   👤 Name: `MK BINGO`\n\n"
                "2. After sending, take a screenshot\n"
                "3. Send the screenshot to @mk_admin\n\n"
                "⏱️ Your balance will be updated within 5 minutes.\n\n"
                "*Minimum deposit: 10 ETB*"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        
        # ===== WITHDRAW =====
        elif data == "withdraw":
            if db_user['balance'] < MIN_WITHDRAWAL:
                await query.edit_message_text(
                    f"❌ Minimum withdrawal is {MIN_WITHDRAWAL} ETB\n"
                    f"Your balance: {db_user['balance']} ETB",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            text = (
                "💳 *Withdrawal Request*\n\n"
                f"Your balance: {db_user['balance']} ETB\n"
                f"Minimum withdrawal: {MIN_WITHDRAWAL} ETB\n\n"
                "To request withdrawal, send:\n"
                "• Amount\n"
                "• TeleBirr number\n\n"
                "Example: `50 0922334455`\n\n"
                "Send to @mk_admin"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        
        # ===== BUY CARD =====
        elif data == "buy_card":
            if db_user['balance'] < CARD_PRICE:
                await query.edit_message_text(
                    f"❌ *Insufficient Balance!*\n\n"
                    f"Your balance: {db_user['balance']} ETB\n"
                    f"Card price: {CARD_PRICE} ETB\n\n"
                    f"Please deposit first.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Generate new card
            card_numbers = generate_bingo_card()
            card_id = len(db_user['cards']) + 1
            
            new_card = {
                "id": card_id,
                "numbers": card_numbers,
                "purchased_at": datetime.now().isoformat(),
                "marked": []
            }
            
            # Update user
            db_user['balance'] -= CARD_PRICE
            db_user['cards'].append(new_card)
            db_user['total_spent'] = db_user.get('total_spent', 0) + CARD_PRICE
            
            # Update statistics
            db.data["statistics"]["total_cards_sold"] = db.data["statistics"].get("total_cards_sold", 0) + 1
            db.data["statistics"]["total_revenue"] = db.data["statistics"].get("total_revenue", 0) + CARD_PRICE
            
            # Record transaction
            db.add_transaction(user_id, CARD_PRICE, "purchase", details={"card_id": card_id})
            
            # Create card preview
            card_preview = format_card_preview(card_numbers)
            
            text = (
                f"✅ *Card Purchased!*\n\n"
                f"🎫 Card #{card_id}\n"
                f"💵 Price: {CARD_PRICE} ETB\n"
                f"💰 New Balance: {db_user['balance']} ETB\n\n"
                f"*Your Card:*\n{card_preview}\n\n"
                f"View all cards in the web app:"
            )
            
            keyboard = [[
                InlineKeyboardButton("🌐 Open Web App", web_app={"url": WEB_APP_URL})
            ]]
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        # ===== MY CARDS =====
        elif data == "my_cards":
            if not db_user['cards']:
                text = "📭 You don't have any cards yet.\nBuy one using the button below!"
                keyboard = [
                    [InlineKeyboardButton("🎮 Buy Card", callback_data="buy_card")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]
            else:
                text = (
                    f"📊 *You have {len(db_user['cards'])} cards*\n\n"
                    f"Click below to view and manage your cards in the web app!"
                )
                keyboard = [
                    [InlineKeyboardButton("🌐 Open Web App", web_app={"url": WEB_APP_URL})],
                    [InlineKeyboardButton("🎮 Buy More", callback_data="buy_card")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        # ===== PLAY GAME =====
        elif data == "play":
            if not db_user['cards']:
                await query.edit_message_text(
                    "❌ You need cards to play!\n"
                    "Buy cards first using the menu.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎮 Buy Card", callback_data="buy_card")
                    ]])
                )
            else:
                text = (
                    "🎯 *Game Lobby*\n\n"
                    f"🎮 Your cards: {len(db_user['cards'])}\n"
                    f"👥 Players waiting: Coming soon!\n\n"
                    "Full multiplayer game coming soon!\n"
                    "For now, practice with your cards in the web app."
                )
                keyboard = [[
                    InlineKeyboardButton("🌐 Practice in Web App", web_app={"url": WEB_APP_URL})
                ]]
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        # ===== DAILY BONUS =====
        elif data == "daily_bonus":
            today = datetime.now().date().isoformat()
            last_claimed = db_user.get('daily_bonus_claimed')
            
            if last_claimed == today:
                await query.edit_message_text(
                    "❌ You've already claimed your daily bonus today!\n"
                    "Come back tomorrow.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Give bonus
            db_user['balance'] += DAILY_BONUS
            db_user['daily_bonus_claimed'] = today
            
            # Record transaction
            db.add_transaction(user_id, DAILY_BONUS, "bonus", details={"type": "daily"})
            
            await query.edit_message_text(
                f"🎁 *Daily Bonus Claimed!*\n\n"
                f"You received: {DAILY_BONUS} ETB\n"
                f"New Balance: {db_user['balance']} ETB\n\n"
                f"Come back tomorrow for more!",
                parse_mode=ParseMode.MARKDOWN
            )
        
        # ===== BALANCE =====
        elif data == "balance":
            text = (
                f"💰 *Your Balance*\n\n"
                f"Current: {db_user['balance']} ETB\n"
                f"Cards owned: {len(db_user['cards'])}\n"
                f"Total spent: {db_user.get('total_spent', 0)} ETB\n"
                f"Total won: {db_user.get('total_won', 0)} ETB\n\n"
                f"Net profit: {db_user.get('total_won', 0) - db_user.get('total_spent', 0)} ETB"
            )
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        
        # ===== HELP =====
        elif data == "help":
            help_text = (
                "❓ *Help & Support*\n\n"
                "*Commands:*\n"
                "/start - Main menu\n"
                "/health - Check bot status\n\n"
                "*Features:*\n"
                "💰 *Deposit* - Add funds via TeleBirr\n"
                "💳 *Withdraw* - Request withdrawal\n"
                "🎮 *Buy Card* - Purchase bingo cards (10 ETB)\n"
                "📊 *My Cards* - View your cards in web app\n"
                "🎯 *Play* - Join games\n"
                "🎁 *Daily Bonus* - Free ETB every day\n\n"
                "*Support:* @mk_admin"
            )
            await query.edit_message_text(help_text, parse_mode=ParseMode.MARKDOWN)
        
        # ===== BACK TO MENU =====
        elif data == "back_to_menu":
            # Recreate main menu
            keyboard = [
                [InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
                 InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")],
                [InlineKeyboardButton("🎮 Buy Card", callback_data="buy_card"),
                 InlineKeyboardButton("📊 My Cards", callback_data="my_cards")],
                [InlineKeyboardButton("🎯 Play Game", callback_data="play"),
                 InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")],
                [InlineKeyboardButton("📈 Balance", callback_data="balance"),
                 InlineKeyboardButton("❓ Help", callback_data="help")]
            ]
            
            if db.is_admin(user_id):
                keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
            
            if db_user['cards']:
                keyboard.append([InlineKeyboardButton("🌐 Open Web App", web_app={"url": WEB_APP_URL})])
            
            await query.edit_message_text(
                f"🎯 *Main Menu*\n\nBalance: {db_user['balance']} ETB",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        # ===== ADMIN PANEL =====
        elif data == "admin_panel":
            if not db.is_admin(user_id):
                await query.edit_message_text("❌ Unauthorized")
                return
            
            # Get pending withdrawals
            pending_withdrawals = len([w for w in db.data.get('withdrawals', []) if w['status'] == 'pending'])
            
            text = (
                f"⚙️ *Admin Panel*\n\n"
                f"👥 Users: {db.data['statistics']['total_users']}\n"
                f"💰 Pending withdrawals: {pending_withdrawals}\n"
                f"🎮 Cards sold: {db.data['statistics'].get('total_cards_sold', 0)}\n"
                f"📊 Revenue: {db.data['statistics'].get('total_revenue', 0)} ETB\n\n"
                f"Select an option:"
            )
            
            keyboard = [
                [InlineKeyboardButton("👥 View Users", callback_data="admin_users")],
                [InlineKeyboardButton("💰 Pending Withdrawals", callback_data="admin_withdrawals")],
                [InlineKeyboardButton("📊 Full Stats", callback_data="admin_stats")],
                [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ]
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        # ===== ADMIN USERS =====
        elif data == "admin_users":
            if not db.is_admin(user_id):
                return
            
            users = db.data["users"]
            text = "👥 *Recent Users:*\n\n"
            
            # Show last 10 active users
            sorted_users = sorted(
                users.items(),
                key=lambda x: x[1].get('last_active', ''),
                reverse=True
            )[:10]
            
            for uid, u in sorted_users:
                status = "🟢" if datetime.fromisoformat(u['last_active']).date() == datetime.now().date() else "⚪"
                text += f"{status} `{uid}`: {u.get('first_name', 'Unknown')} - {u['balance']} ETB ({len(u['cards'])} cards)\n"
            
            text += f"\nTotal: {len(users)} users"
            
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        
        # ===== ADMIN WITHDRAWALS =====
        elif data == "admin_withdrawals":
            if not db.is_admin(user_id):
                return
            
            withdrawals = [w for w in db.data.get('withdrawals', []) if w['status'] == 'pending']
            
            if not withdrawals:
                await query.edit_message_text("No pending withdrawals.")
                return
            
            text = "💰 *Pending Withdrawals:*\n\n"
            for w in withdrawals[:5]:
                text += f"ID: {w['id']} | User: {w['user_id']} | Amount: {w['amount']} ETB | Phone: {w['phone']}\n"
            
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        
        # ===== ADMIN BROADCAST =====
        elif data == "admin_broadcast":
            if not db.is_admin(user_id):
                return
            
            await query.edit_message_text(
                "📢 *Send Broadcast*\n\n"
                "Reply with the message you want to send to all users:",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['awaiting_broadcast'] = True
        
        # ===== ADMIN STATS =====
        elif data == "admin_stats":
            if not db.is_admin(user_id):
                return
            
            # Call stats function
            await stats(update, context)
    
    except Exception as e:
        logger.error(f"Error in button_handler: {e}")
        await query.edit_message_text(
            "❌ An error occurred. Please try again."
        )

# ==================== MESSAGE HANDLERS ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Check if awaiting broadcast
    if context.user_data.get('awaiting_broadcast') and db.is_admin(user_id):
        # Send broadcast to all users
        sent = 0
        failed = 0
        
        await update.message.reply_text("📨 Sending broadcast...")
        
        for uid, user_data in db.data["users"].items():
            try:
                await context.bot.send_message(
                    int(uid),
                    f"📢 *Announcement*\n\n{text}",
                    parse_mode=ParseMode.MARKDOWN
                )
                sent += 1
                await asyncio.sleep(0.05)  # Rate limit
            except:
                failed += 1
        
        await update.message.reply_text(
            f"✅ Broadcast complete!\n"
            f"Sent: {sent}\n"
            f"Failed: {failed}"
        )
        
        context.user_data.pop('awaiting_broadcast', None)
        return
    
    # Handle withdrawal format
    if len(text.split()) == 2 and text.split()[0].isdigit() and text.split()[1].startswith('09'):
        amount, phone = text.split()
        amount = float(amount)
        
        db_user = db.get_user(user_id)
        
        if amount < MIN_WITHDRAWAL:
            await update.message.reply_text(f"❌ Minimum withdrawal is {MIN_WITHDRAWAL} ETB")
            return
        
        if amount > db_user['balance']:
            await update.message.reply_text("❌ Insufficient balance")
            return
        
        # Create withdrawal request
        withdrawal = {
            "id": len(db.data.get('withdrawals', [])) + 1,
            "user_id": user_id,
            "amount": amount,
            "phone": phone,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "username": db_user.get('username')
        }
        
        db.data.setdefault('withdrawals', []).append(withdrawal)
        
        # Lock funds
        db_user['balance'] -= amount
        db.save()
        
        # Notify admins
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"💰 *New Withdrawal Request*\n\n"
                    f"User: {user_id}\n"
                    f"Amount: {amount} ETB\n"
                    f"Phone: {phone}\n"
                    f"Request ID: {withdrawal['id']}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        
        await update.message.reply_text(
            f"✅ *Withdrawal Request Submitted!*\n\n"
            f"Amount: {amount} ETB\n"
            f"Phone: {phone}\n"
            f"Status: ⏳ Pending\n\n"
            f"You will be notified once approved.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    else:
        await update.message.reply_text(
            "Use /start to see the main menu.\n"
            "For withdrawals, send: `<amount> <phone>`\n"
            "Example: `50 0922334455`"
        )

# ==================== UTILITY FUNCTIONS ====================
def format_card_preview(card_numbers: list) -> str:
    """Format card for preview in Telegram"""
    preview = "╔════╦════╦════╦════╦════╗\n"
    preview += "║  B ║  I ║  N ║  G ║  O ║\n"
    preview += "╠════╬════╬════╬════╬════╣\n"
    
    for row in range(5):
        preview += "║"
        for col in range(5):
            val = card_numbers[col][row]
            if val == "FREE":
                preview += " 😊 ║"
            else:
                preview += f"{str(val):>4}║"
        preview += "\n"
        if row < 4:
            preview += "╠════╬════╬════╬════╬════╣\n"
    
    preview += "╚════╩════╩════╩════╩════╝"
    return f"```\n{preview}\n```"

# ==================== MAIN FUNCTION ====================
async def main_async():
    """Async main function"""
    logger.info("🤖 Starting MK BINGO Bot...")
    logger.info(f"📱 Web App URL: {WEB_APP_URL}")
    logger.info(f"👑 Admin IDs: {ADMIN_IDS}")
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("stats", stats))
    
    # Add callback query handler
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Add message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Start bot
    logger.info("✅ Bot started, polling for updates...")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    logger.info("🚀 Bot is running!")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
            # Auto-save database every minute
            db.save()
    except asyncio.CancelledError:
        logger.info("Bot stopping...")
    finally:
        # Stop gracefully
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        db.save(force=True)
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
        import traceback
        traceback.print_exc()
    finally:
        loop.close()

if __name__ == "__main__":
    main()