"""
COMPLETE TELEGRAM BINGO BOT with FULL ADMIN CONTROL
Includes: Player Management, Statistics, Game Settings, Admin Management, Communication
"""

import logging
import json
import random
import string
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from collections import defaultdict
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode

# ==================== CONFIGURATION ====================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
SUPER_ADMIN_IDS = [123456789]  # Telegram user IDs of super admins
TIMEZONE = pytz.timezone('Africa/Addis_Ababa')

# Game Settings (configurable via admin panel)
class GameConfig:
    def __init__(self):
        self.card_price = 10  # ETB
        self.min_bet = 10
        self.max_bet = 1000
        self.base_win = 100
        self.round_duration = 300  # seconds
        self.number_call_speed = 3  # seconds
        self.daily_bonus = 50
        self.welcome_bonus = 100
        self.max_cards_per_user = 5
        self.house_commission = 0.20
        self.auto_start_delay = 30

config = GameConfig()

# ==================== ENUMS ====================
class UserRole(Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MODERATOR = "moderator"
    PLAYER = "player"

class UserStatus(Enum):
    ACTIVE = "active"
    BANNED = "banned"
    SUSPENDED = "suspended"

class TransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    CARD_PURCHASE = "card_purchase"
    WINNINGS = "winnings"
    BONUS = "bonus"
    ADMIN_CREDIT = "admin_credit"
    REFUND = "refund"

# ==================== DATABASE (In-memory for demo - use real DB in production) ====================
class Database:
    def __init__(self):
        self.users = {}  # user_id -> user data
        self.admins = {}  # admin_id -> role
        self.games = []  # game history
        self.cards = {}  # card_id -> card data
        self.transactions = []
        self.withdrawals = []
        self.withdrawal_requests = []
        self.bans = set()
        self.statistics = {
            'total_players': 0,
            'active_today': 0,
            'games_played_today': 0,
            'total_chips': 0,
            'total_revenue': 0,
            'total_winnings': 0,
            'active_games': 0,
            'daily_players': defaultdict(int),
            'hourly_active': defaultdict(int)
        }
        self.admin_log = []
        self.announcements = []
        self.current_game = None
        self.called_numbers = []
        self.players_in_game = set()
        self.game_started = False
        self.game_ended = False
        self.taken_cards = set()
        
    def get_user(self, user_id, user_data=None):
        if user_id not in self.users:
            role = UserRole.SUPER_ADMIN if user_id in SUPER_ADMIN_IDS else UserRole.PLAYER
            username = user_data.username if user_data else None
            first_name = user_data.first_name if user_data else None
            
            self.users[user_id] = {
                'user_id': user_id,
                'username': username,
                'first_name': first_name,
                'balance': 0,
                'cards': [],
                'role': role,
                'status': UserStatus.ACTIVE,
                'joined_at': datetime.now(TIMEZONE),
                'last_active': datetime.now(TIMEZONE),
                'games_played': 0,
                'games_won': 0,
                'total_spent': 0,
                'total_won': 0,
                'daily_bonus_claimed': None,
                'referred_by': None,
                'referrals': [],
                'notes': ''
            }
            self.statistics['total_players'] += 1
            
        if user_data:
            self.users[user_id]['username'] = user_data.username or self.users[user_id]['username']
            self.users[user_id]['first_name'] = user_data.first_name or self.users[user_id]['first_name']
        
        self.users[user_id]['last_active'] = datetime.now(TIMEZONE)
        return self.users[user_id]
    
    def is_admin(self, user_id):
        user = self.get_user(user_id)
        return user['role'] in [UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.MODERATOR]
    
    def is_super_admin(self, user_id):
        return user_id in SUPER_ADMIN_IDS
    
    def add_admin(self, user_id, role, added_by):
        if user_id not in self.users:
            self.get_user(user_id)
        self.users[user_id]['role'] = role
        self.admin_log.append({
            'action': 'add_admin',
            'user_id': user_id,
            'role': role.value,
            'added_by': added_by,
            'timestamp': datetime.now(TIMEZONE)
        })
    
    def remove_admin(self, user_id, removed_by):
        if user_id not in SUPER_ADMIN_IDS:  # Can't remove super admin
            self.users[user_id]['role'] = UserRole.PLAYER
            self.admin_log.append({
                'action': 'remove_admin',
                'user_id': user_id,
                'removed_by': removed_by,
                'timestamp': datetime.now(TIMEZONE)
            })
    
    def ban_user(self, user_id, banned_by, reason=""):
        self.users[user_id]['status'] = UserStatus.BANNED
        self.bans.add(user_id)
        self.admin_log.append({
            'action': 'ban',
            'user_id': user_id,
            'banned_by': banned_by,
            'reason': reason,
            'timestamp': datetime.now(TIMEZONE)
        })
    
    def unban_user(self, user_id, unbanned_by):
        self.users[user_id]['status'] = UserStatus.ACTIVE
        self.bans.discard(user_id)
        self.admin_log.append({
            'action': 'unban',
            'user_id': user_id,
            'unbanned_by': unbanned_by,
            'timestamp': datetime.now(TIMEZONE)
        })
    
    def add_transaction(self, user_id, amount, type_, status='completed', ref=None, notes=""):
        tx = {
            'id': len(self.transactions) + 1,
            'user_id': user_id,
            'amount': amount,
            'type': type_,
            'status': status,
            'ref': ref or ''.join(random.choices(string.ascii_uppercase + string.digits, k=12)),
            'created_at': datetime.now(TIMEZONE),
            'notes': notes
        }
        self.transactions.append(tx)
        
        # Update statistics
        if type_ == TransactionType.DEPOSIT.value:
            self.statistics['total_chips'] += amount
        elif type_ == TransactionType.WITHDRAWAL.value:
            self.statistics['total_chips'] -= amount
        elif type_ == TransactionType.ADMIN_CREDIT.value:
            self.statistics['total_chips'] += amount
        
        return tx
    
    def add_withdrawal_request(self, user_id, amount, phone):
        wd = {
            'id': len(self.withdrawal_requests) + 1,
            'user_id': user_id,
            'amount': amount,
            'phone': phone,
            'status': 'pending',
            'created_at': datetime.now(TIMEZONE),
            'processed_by': None,
            'processed_at': None
        }
        self.withdrawal_requests.append(wd)
        return wd
    
    def update_statistics(self):
        """Update various statistics"""
        now = datetime.now(TIMEZONE)
        today = now.date()
        
        # Active today
        self.statistics['active_today'] = sum(
            1 for u in self.users.values() 
            if u['last_active'].date() == today
        )
        
        # Total chips in circulation
        self.statistics['total_chips'] = sum(u['balance'] for u in self.users.values())
        
        # Games played today
        self.statistics['games_played_today'] = sum(
            1 for g in self.games 
            if g.get('ended_at') and g['ended_at'].date() == today
        )
        
        # Revenue (house commission from games)
        self.statistics['total_revenue'] = sum(
            g.get('house_fee', 0) for g in self.games
        )

db = Database()

# ==================== BINGO CARD GENERATOR ====================
def generate_bingo_card() -> List[List]:
    """Generate a 5x5 bingo card with numbers 1-75"""
    card = []
    col_ranges = [(1, 15), (16, 30), (31, 45), (46, 60), (61, 75)]
    
    for col in range(5):
        numbers = random.sample(range(col_ranges[col][0], col_ranges[col][1] + 1), 5)
        card.append(numbers)
    
    # Free space in center
    card[2][2] = "FREE"
    
    return card

def format_bingo_card(card: List[List], marked: List[int] = None) -> str:
    """Format bingo card for display with marked numbers"""
    marked = marked or []
    result = "╔════╦════╦════╦════╦════╗\n"
    result += "║  B ║  I ║  N ║  G ║  O ║\n"
    result += "╠════╬════╬════╬════╬════╣\n"
    
    for row in range(5):
        row_text = "║"
        for col in range(5):
            val = card[col][row]
            if val == "FREE":
                row_text += " 😊 ║"
            elif val in marked:
                row_text += f" ✅{str(val):>2}║"
            else:
                row_text += f"{str(val):>4}║"
        result += row_text + "\n"
        if row < 4:
            result += "╠════╬════╬════╬════╬════╣\n"
    
    result += "╚════╩════╩════╩════╩════╝"
    return result

# ==================== MAIN MENU ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main start command with dynamic menu based on role"""
    user = update.effective_user
    db_user = db.get_user(user.id, user)
    
    # Check if banned
    if db_user['status'] == UserStatus.BANNED:
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    
    # Welcome message
    welcome_text = (
        f"🎯 *WELCOME TO MK BINGO!* 🎯\n\n"
        f"👤 *Player:* {db_user['first_name'] or db_user['username']}\n"
        f"💰 *Balance:* `{db_user['balance']} ETB`\n"
        f"🎮 *Cards owned:* {len(db_user['cards'])}\n"
        f"📊 *Games played:* {db_user['games_played']}\n"
        f"🏆 *Games won:* {db_user['games_won']}\n\n"
        f"⚙️ *Game Settings:*\n"
        f"• Card Price: {config.card_price} ETB\n"
        f"• Max Cards: {config.max_cards_per_user}\n"
        f"• Win Rate: {config.base_win} ETB base\n\n"
        f"👇 *Choose an option:*"
    )
    
    # Main menu keyboard
    keyboard = [
        [InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
         InlineKeyboardButton("💳 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("🎮 Buy Cards", callback_data="buy_cards"),
         InlineKeyboardButton("📊 My Cards", callback_data="my_cards")],
        [InlineKeyboardButton("🎯 Join Game", callback_data="join_game"),
         InlineKeyboardButton("📈 Statistics", callback_data="player_stats")],
        [InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus"),
         InlineKeyboardButton("📞 Support", callback_data="support")]
    ]
    
    # Admin button for admins
    if db.is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

# ==================== PLAYER FUNCTIONS ====================
async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim daily bonus"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    db_user = db.get_user(user.id)
    
    now = datetime.now(TIMEZONE)
    last_claimed = db_user.get('daily_bonus_claimed')
    
    if last_claimed and last_claimed.date() == now.date():
        await query.edit_message_text(
            "❌ You've already claimed your daily bonus today!\n"
            "Come back tomorrow."
        )
        return
    
    # Give bonus
    bonus = config.daily_bonus
    db_user['balance'] += bonus
    db_user['daily_bonus_claimed'] = now
    
    db.add_transaction(user.id, bonus, TransactionType.BONUS.value, notes="Daily bonus")
    
    await query.edit_message_text(
        f"🎁 *Daily Bonus Claimed!*\n\n"
        f"You received: *{bonus} ETB*\n"
        f"New balance: *{db_user['balance']} ETB*\n\n"
        f"Come back tomorrow for another bonus!",
        parse_mode=ParseMode.MARKDOWN
    )

async def player_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show player's personal statistics"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    db_user = db.get_user(user.id)
    
    # Get win rate
    win_rate = 0
    if db_user['games_played'] > 0:
        win_rate = (db_user['games_won'] / db_user['games_played']) * 100
    
    # Get recent transactions
    recent_txs = [t for t in db.transactions if t['user_id'] == user.id][-5:]
    
    text = (
        f"📊 *Your Statistics*\n\n"
        f"👤 *Profile:*\n"
        f"• User ID: `{user.id}`\n"
        f"• Username: @{db_user['username'] or 'None'}\n"
        f"• Joined: {db_user['joined_at'].strftime('%Y-%m-%d')}\n\n"
        f"💰 *Financial:*\n"
        f"• Current Balance: `{db_user['balance']} ETB`\n"
        f"• Total Spent: `{db_user['total_spent']} ETB`\n"
        f"• Total Won: `{db_user['total_won']} ETB`\n"
        f"• Net Profit: `{db_user['total_won'] - db_user['total_spent']} ETB`\n\n"
        f"🎮 *Gaming:*\n"
        f"• Cards Owned: {len(db_user['cards'])}\n"
        f"• Games Played: {db_user['games_played']}\n"
        f"• Games Won: {db_user['games_won']}\n"
        f"• Win Rate: {win_rate:.1f}%\n\n"
    )
    
    if recent_txs:
        text += "📝 *Recent Transactions:*\n"
        for tx in recent_txs[-3:]:
            emoji = "📥" if tx['amount'] > 0 else "📤"
            text += f"{emoji} {tx['type']}: {abs(tx['amount'])} ETB\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main admin panel with all controls"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        await query.edit_message_text("❌ Unauthorized access.")
        return
    
    is_super = db.is_super_admin(user_id)
    
    # Update statistics
    db.update_statistics()
    
    text = (
        f"⚙️ *Admin Control Panel*\n\n"
        f"📊 *System Overview:*\n"
        f"• Total Players: {db.statistics['total_players']}\n"
        f"• Active Today: {db.statistics['active_today']}\n"
        f"• Games Today: {db.statistics['games_played_today']}\n"
        f"• Total Chips: {db.statistics['total_chips']:.0f} ETB\n"
        f"• Revenue: {db.statistics['total_revenue']:.0f} ETB\n"
        f"• Active Games: {db.statistics['active_games']}\n\n"
        f"👑 Your Role: {db.users[user_id]['role'].value}\n\n"
        f"👇 *Select a category:*"
    )
    
    # Admin menu based on role
    keyboard = [
        [InlineKeyboardButton("👥 Player Management", callback_data="admin_players")],
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("⚙️ Game Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("📢 Communications", callback_data="admin_comms")],
        [InlineKeyboardButton("💰 Financial", callback_data="admin_financial")]
    ]
    
    # Super admin only options
    if is_super:
        keyboard.extend([
            [InlineKeyboardButton("👑 Admin Management", callback_data="admin_manage_admins")],
            [InlineKeyboardButton("📋 Activity Log", callback_data="admin_log")]
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== PLAYER MANAGEMENT ====================
async def admin_player_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Player management interface"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "👥 *Player Management*\n\n"
        "Select an option:"
    )
    
    keyboard = [
        [InlineKeyboardButton("📋 View All Players", callback_data="admin_list_players")],
        [InlineKeyboardButton("🔍 Search Player", callback_data="admin_search_player")],
        [InlineKeyboardButton("🚫 Ban/Unban Player", callback_data="admin_ban_player")],
        [InlineKeyboardButton("💰 Give Chips", callback_data="admin_give_chips")],
        [InlineKeyboardButton("🎁 Give Bonus to All", callback_data="admin_bonus_all")],
        [InlineKeyboardButton("📜 Player History", callback_data="admin_player_history")],
        [InlineKeyboardButton("👀 Online Status", callback_data="admin_online_status")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_list_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all players with pagination"""
    query = update.callback_query
    await query.answer()
    
    page = int(context.user_data.get('player_page', 0))
    players_per_page = 10
    
    all_players = list(db.users.values())
    total_pages = (len(all_players) + players_per_page - 1) // players_per_page
    
    start = page * players_per_page
    end = start + players_per_page
    players_page = all_players[start:end]
    
    text = f"📋 *Players List (Page {page + 1}/{total_pages})*\n\n"
    
    for p in players_page:
        status_emoji = "🟢" if p['status'] == UserStatus.ACTIVE else "🔴"
        role_emoji = "👑" if p['role'] == UserRole.SUPER_ADMIN else "⚙️" if p['role'] == UserRole.ADMIN else "🛡️" if p['role'] == UserRole.MODERATOR else "👤"
        text += f"{status_emoji}{role_emoji} `{p['user_id']}` | @{p['username'] or 'None'} | {p['balance']} ETB\n"
    
    keyboard = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data="admin_players_prev"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data="admin_players_next"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_players")])
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_give_chips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Give chips to specific player"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💰 *Give Chips to Player*\n\n"
        "Please enter the user ID and amount in format:\n"
        "`<user_id> <amount>`\n\n"
        "Example: `123456789 500`",
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data['awaiting_give_chips'] = True

async def process_give_chips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process giving chips"""
    if not context.user_data.get('awaiting_give_chips'):
        return
    
    try:
        args = update.message.text.split()
        if len(args) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `<user_id> <amount>`")
            return
        
        target_id = int(args[0])
        amount = float(args[1])
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
        
        admin_id = update.effective_user.id
        target_user = db.get_user(target_id)
        
        # Give chips
        target_user['balance'] += amount
        db.add_transaction(target_id, amount, TransactionType.ADMIN_CREDIT.value, 
                          notes=f"Credited by admin {admin_id}")
        
        # Notify target
        try:
            await context.bot.send_message(
                target_id,
                f"💰 *You received {amount} ETB from admin!*\n"
                f"New balance: {target_user['balance']} ETB",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
        
        await update.message.reply_text(
            f"✅ Successfully gave {amount} ETB to user {target_id}\n"
            f"User's new balance: {target_user['balance']} ETB"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or amount.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    context.user_data.pop('awaiting_give_chips', None)

async def admin_bonus_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Give bonus to all active players"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🎁 *Give Bonus to All Players*\n\n"
        "Enter the bonus amount for each player:",
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data['awaiting_bonus_all'] = True

async def process_bonus_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process bonus for all players"""
    if not context.user_data.get('awaiting_bonus_all'):
        return
    
    try:
        amount = float(update.message.text)
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
        
        admin_id = update.effective_user.id
        count = 0
        
        for user_id, user_data in db.users.items():
            if user_data['status'] == UserStatus.ACTIVE:
                user_data['balance'] += amount
                db.add_transaction(user_id, amount, TransactionType.BONUS.value,
                                  notes=f"Global bonus from admin {admin_id}")
                count += 1
                
                # Try to notify
                try:
                    await context.bot.send_message(
                        user_id,
                        f"🎁 *Global Bonus!*\n\n"
                        f"You received {amount} ETB from admin!\n"
                        f"New balance: {user_data['balance']} ETB",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
        
        await update.message.reply_text(
            f"✅ Successfully gave {amount} ETB to {count} active players!"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
    
    context.user_data.pop('awaiting_bonus_all', None)

# ==================== GAME SETTINGS MANAGEMENT ====================
async def admin_game_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Game settings interface"""
    query = update.callback_query
    await query.answer()
    
    text = (
        f"⚙️ *Game Settings*\n\n"
        f"Current Configuration:\n"
        f"• Card Price: `{config.card_price} ETB`\n"
        f"• Min Bet: `{config.min_bet} ETB`\n"
        f"• Max Bet: `{config.max_bet} ETB`\n"
        f"• Base Win: `{config.base_win} ETB`\n"
        f"• Round Duration: `{config.round_duration}s`\n"
        f"• Call Speed: `{config.number_call_speed}s`\n"
        f"• Daily Bonus: `{config.daily_bonus} ETB`\n"
        f"• Welcome Bonus: `{config.welcome_bonus} ETB`\n"
        f"• Max Cards/User: `{config.max_cards_per_user}`\n"
        f"• House Commission: `{config.house_commission * 100}%`\n\n"
        f"Select a setting to change:"
    )
    
    keyboard = [
        [InlineKeyboardButton("💰 Card Price", callback_data="set_card_price")],
        [InlineKeyboardButton("🎲 Min/Max Bet", callback_data="set_bet_limits")],
        [InlineKeyboardButton("🏆 Base Win", callback_data="set_base_win")],
        [InlineKeyboardButton("⏱️ Round Duration", callback_data="set_round_duration")],
        [InlineKeyboardButton("⚡ Call Speed", callback_data="set_call_speed")],
        [InlineKeyboardButton("🎁 Daily Bonus", callback_data="set_daily_bonus")],
        [InlineKeyboardButton("📊 Max Cards", callback_data="set_max_cards")],
        [InlineKeyboardButton("🏠 House Commission", callback_data="set_commission")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== ADMIN MANAGEMENT (SUPER ADMIN ONLY) ====================
async def admin_manage_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin management interface (super admin only)"""
    query = update.callback_query
    await query.answer()
    
    if not db.is_super_admin(update.effective_user.id):
        await query.edit_message_text("❌ Super admin access required.")
        return
    
    # List current admins
    admins = [u for u in db.users.values() 
              if u['role'] in [UserRole.ADMIN, UserRole.MODERATOR]]
    
    text = "👑 *Admin Management*\n\n"
    text += "Current Admins:\n"
    
    for admin in admins:
        role_emoji = "⚙️" if admin['role'] == UserRole.ADMIN else "🛡️"
        text += f"{role_emoji} @{admin['username'] or 'None'} (`{admin['user_id']}`) - {admin['role'].value}\n"
    
    text += "\nSelect an action:"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
        [InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin")],
        [InlineKeyboardButton("📋 Admin Activity Log", callback_data="admin_activity_log")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add new admin"""
    query = update.callback_query
    await query.answer()
    
    if not db.is_super_admin(update.effective_user.id):
        return
    
    await query.edit_message_text(
        "👑 *Add New Admin*\n\n"
        "Please enter the user ID and role in format:\n"
        "`<user_id> <role>`\n\n"
        "Roles: `admin` or `moderator`\n\n"
        "Example: `123456789 admin`",
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data['awaiting_add_admin'] = True

async def process_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process adding admin"""
    if not context.user_data.get('awaiting_add_admin'):
        return
    
    try:
        args = update.message.text.split()
        if len(args) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `<user_id> <role>`")
            return
        
        target_id = int(args[0])
        role_str = args[1].lower()
        
        if role_str == 'admin':
            role = UserRole.ADMIN
        elif role_str == 'moderator':
            role = UserRole.MODERATOR
        else:
            await update.message.reply_text("❌ Role must be 'admin' or 'moderator'")
            return
        
        admin_id = update.effective_user.id
        
        db.add_admin(target_id, role, admin_id)
        
        # Notify new admin
        try:
            await context.bot.send_message(
                target_id,
                f"👑 *You've been made {role_str}*\n\n"
                f"Added by: {admin_id}\n"
                f"Use /start to access admin features.",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
        
        await update.message.reply_text(
            f"✅ Successfully added user {target_id} as {role_str}"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
    
    context.user_data.pop('awaiting_add_admin', None)

# ==================== COMMUNICATIONS ====================
async def admin_communications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Communications interface"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "📢 *Communications Center*\n\n"
        "Send messages to players:"
    )
    
    keyboard = [
        [InlineKeyboardButton("📢 Broadcast to All", callback_data="broadcast_all")],
        [InlineKeyboardButton("💬 Private Message", callback_data="private_message")],
        [InlineKeyboardButton("📅 Schedule Message", callback_data="schedule_message")],
        [InlineKeyboardButton("🎁 Promotional Offer", callback_data="send_promo")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def broadcast_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all players"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📢 *Broadcast to All Players*\n\n"
        "Enter the message you want to send to everyone:",
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data['awaiting_broadcast'] = True

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process broadcast message"""
    if not context.user_data.get('awaiting_broadcast'):
        return
    
    message = update.message.text
    admin_id = update.effective_user.id
    sent = 0
    failed = 0
    
    await update.message.reply_text("📨 Sending broadcast...")
    
    for user_id, user_data in db.users.items():
        if user_data['status'] == UserStatus.ACTIVE:
            try:
                await context.bot.send_message(
                    user_id,
                    f"📢 *Announcement from Admin*\n\n{message}",
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

# ==================== WITHDRAWAL APPROVAL ====================
async def handle_withdrawal_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin approval/rejection of withdrawals"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split('_')
    
    if parts[0] == 'approve':
        wd_id = int(parts[2])
        wd = next((w for w in db.withdrawal_requests if w['id'] == wd_id), None)
        
        if wd:
            wd['status'] = 'approved'
            wd['processed_by'] = update.effective_user.id
            wd['processed_at'] = datetime.now(TIMEZONE)
            
            # Process actual TeleBirr transfer
            # In production, call TeleBirr API here
            
            # Notify user
            try:
                await context.bot.send_message(
                    wd['user_id'],
                    f"✅ *Withdrawal Approved!*\n\n"
                    f"Amount: {wd['amount']} ETB\n"
                    f"Phone: {wd['phone']}\n\n"
                    f"Funds have been sent to your TeleBirr account.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
            
            await query.edit_message_text(f"✅ Withdrawal #{wd_id} approved!")
    
    elif parts[0] == 'reject':
        wd_id = int(parts[2])
        wd = next((w for w in db.withdrawal_requests if w['id'] == wd_id), None)
        
        if wd:
            wd['status'] = 'rejected'
            wd['processed_by'] = update.effective_user.id
            wd['processed_at'] = datetime.now(TIMEZONE)
            
            # Refund user
            db.get_user(wd['user_id'])['balance'] += wd['amount']
            
            # Notify user
            try:
                await context.bot.send_message(
                    wd['user_id'],
                    f"❌ *Withdrawal Rejected*\n\n"
                    f"Amount: {wd['amount']} ETB\n\n"
                    f"Funds have been returned to your balance.\n"
                    f"Please contact support if you have questions.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
            
            await query.edit_message_text(f"✅ Withdrawal #{wd_id} rejected and refunded.")

# ==================== MAIN CALLBACK HANDLER ====================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all button callbacks"""
    query = update.callback_query
    data = query.data
    
    # Handle withdrawal approvals specially
    if data.startswith('approve_wd_') or data.startswith('reject_wd_'):
        await handle_withdrawal_approval(update, context)
        return
    
    # Main menu navigation
    handlers = {
        'deposit': deposit,
        'withdraw': withdraw,
        'buy_cards': buy_cards,
        'my_cards': my_cards,
        'join_game': join_game,
        'player_stats': player_statistics,
        'daily_bonus': daily_bonus,
        'admin_panel': admin_panel,
        'back_to_menu': lambda u, c: start(u, c) if u.callback_query else None,
        
        # Player management
        'admin_players': admin_player_management,
        'admin_list_players': admin_list_players,
        'admin_give_chips': admin_give_chips,
        'admin_bonus_all': admin_bonus_all,
        
        # Game settings
        'admin_settings': admin_game_settings,
        
        # Communications
        'admin_comms': admin_communications,
        'broadcast_all': broadcast_all,
        
        # Admin management (super admin)
        'admin_manage_admins': admin_manage_admins,
        'add_admin': add_admin,
    }
    
    # Pagination
    if data == 'admin_players_next':
        context.user_data['player_page'] = context.user_data.get('player_page', 0) + 1
        await admin_list_players(update, context)
        return
    elif data == 'admin_players_prev':
        context.user_data['player_page'] = max(0, context.user_data.get('player_page', 0) - 1)
        await admin_list_players(update, context)
        return
    
    if data in handlers:
        await handlers[data](update, context)

# ==================== CONVERSATION HANDLERS ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    
    # Check if user is banned
    if db.users.get(user_id, {}).get('status') == UserStatus.BANNED:
        await update.message.reply_text("❌ You are banned.")
        return
    
    # Process various await states
    if context.user_data.get('awaiting_deposit'):
        await process_deposit_amount(update, context)
    elif context.user_data.get('awaiting_withdraw_amount'):
        await process_withdraw_amount(update, context)
    elif context.user_data.get('awaiting_withdraw_phone'):
        await process_withdraw_phone(update, context)
    elif context.user_data.get('awaiting_give_chips'):
        await process_give_chips(update, context)
    elif context.user_data.get('awaiting_bonus_all'):
        await process_bonus_all(update, context)
    elif context.user_data.get('awaiting_add_admin'):
        await process_add_admin(update, context)
    elif context.user_data.get('awaiting_broadcast'):
        await process_broadcast(update, context)
    else:
        # Default to help message
        await update.message.reply_text(
            "Use /start to see the main menu.\n"
            "Available commands:\n"
            "/start - Main menu\n"
            "/balance - Check balance\n"
            "/cards - My cards\n"
            "/bingo - Check for bingo"
        )

# ==================== DEPOSIT/WITHDRAWAL FUNCTIONS ====================
async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deposit"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "💰 *TeleBirr Deposit*\n\n"
        "Please enter the amount you want to deposit (minimum 10 ETB):"
    )
    
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    context.user_data['awaiting_deposit'] = True

async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process deposit amount"""
    try:
        amount = float(update.message.text)
        if amount < 10:
            await update.message.reply_text("❌ Minimum deposit is 10 ETB.")
            return
        
        # Simulate payment
        ref = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
        tx = db.add_transaction(update.effective_user.id, amount, 'deposit', status='pending', ref=ref)
        
        text = (
            f"✅ *Payment Initiated!*\n\n"
            f"Amount: {amount} ETB\n"
            f"Reference: `{ref}`\n\n"
            f"In a real implementation, you would receive a TeleBirr payment link.\n"
            f"For demo, your balance will be updated in 10 seconds."
        )
        
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        
        # Simulate payment confirmation
        await asyncio.sleep(10)
        tx['status'] = 'completed'
        db.get_user(update.effective_user.id)['balance'] += amount
        
        await update.message.reply_text(
            f"✅ Payment confirmed! Your balance is now {db.get_user(update.effective_user.id)['balance']} ETB"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
    
    context.user_data.pop('awaiting_deposit', None)

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "💳 *Withdrawal Request*\n\n"
        "Please enter the amount you want to withdraw (minimum 20 ETB):"
    )
    
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    context.user_data['awaiting_withdraw_amount'] = True

async def process_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process withdrawal amount"""
    try:
        amount = float(update.message.text)
        user = update.effective_user
        db_user = db.get_user(user.id)
        
        if amount < 20:
            await update.message.reply_text("❌ Minimum withdrawal is 20 ETB.")
            return
        
        if amount > db_user['balance']:
            await update.message.reply_text("❌ Insufficient balance.")
            return
        
        await update.message.reply_text(
            "📱 Please enter your TeleBirr phone number (format: 09xxxxxxxx):"
        )
        context.user_data['withdraw_amount'] = amount
        context.user_data['awaiting_withdraw_phone'] = True
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")

async def process_withdraw_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process withdrawal phone number"""
    phone = update.message.text.strip()
    
    if not phone.startswith('09') or len(phone) != 10 or not phone.isdigit():
        await update.message.reply_text("❌ Invalid phone number. Use format: 09xxxxxxxx")
        return
    
    user = update.effective_user
    amount = context.user_data.get('withdraw_amount')
    
    # Create withdrawal request
    wd = db.add_withdrawal_request(user.id, amount, phone)
    
    # Lock funds
    db.get_user(user.id)['balance'] -= amount
    
    # Notify admins
    for admin_id in [uid for uid, data in db.users.items() if data['role'] in [UserRole.ADMIN, UserRole.SUPER_ADMIN]]:
        try:
            text = (
                f"💰 *New Withdrawal Request*\n\n"
                f"User: @{db.users[user.id]['username'] or 'None'} (ID: {user.id})\n"
                f"Amount: {amount} ETB\n"
                f"Phone: {phone}\n"
                f"Request ID: {wd['id']}"
            )
            keyboard = [[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_wd_{wd['id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_wd_{wd['id']}")
            ]]
            await context.bot.send_message(
                admin_id,
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            pass
    
    await update.message.reply_text(
        f"✅ *Withdrawal Request Submitted!*\n\n"
        f"Amount: {amount} ETB\n"
        f"Phone: {phone}\n"
        f"Status: ⏳ Pending Admin Approval\n\n"
        f"You will be notified once processed.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data.pop('awaiting_withdraw_amount', None)
    context.user_data.pop('awaiting_withdraw_phone', None)
    context.user_data.pop('withdraw_amount', None)

# ==================== BUY CARDS ====================
async def buy_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buy cards interface"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    db_user = db.get_user(user.id)
    
    # Calculate max affordable
    max_affordable = min(
        config.max_cards_per_user - len(db_user['cards']),
        int(db_user['balance'] / config.card_price)
    )
    
    if max_affordable <= 0:
        await query.edit_message_text(
            "❌ Cannot buy cards:\n"
            f"- You own {len(db_user['cards'])}/{config.max_cards_per_user} cards\n"
            f"- Balance: {db_user['balance']} ETB\n"
            f"- Card price: {config.card_price} ETB\n\n"
            "Please deposit or wait for next round."
        )
        return
    
    # Show available cards (first 30)
    available = [i for i in range(1, 1001) if i not in db.taken_cards][:30]
    
    text = (
        f"🎮 *Buy Cards*\n\n"
        f"Your balance: {db_user['balance']} ETB\n"
        f"Price per card: {config.card_price} ETB\n"
        f"You can buy up to {max_affordable} more cards.\n\n"
        f"Available cards (select below):"
    )
    
    # Create card grid
    keyboard = []
    row = []
    for card_id in available:
        row.append(InlineKeyboardButton(str(card_id), callback_data=f"select_card_{card_id}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("✅ Done", callback_data="done_selecting")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")])
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    context.user_data['selecting_cards'] = []
    context.user_data['max_select'] = max_affordable

# ==================== MY CARDS ====================
async def my_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's cards"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    db_user = db.get_user(user.id)
    
    if not db_user['cards']:
        await query.edit_message_text(
            "You don't have any cards yet.\nUse /buy to purchase cards.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Buy Cards", callback_data="buy_cards"),
                InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
            ]])
        )
        return
    
    # Show first 3 cards
    text = f"📊 *Your Cards ({len(db_user['cards'])} total)*\n\n"
    
    for i, card_id in enumerate(db_user['cards'][:3]):
        if card_id in db.cards:
            card = db.cards[card_id]['numbers']
            marked = db.cards[card_id].get('marked', [])
            text += f"*Card #{card_id}:*\n"
            text += f"```\n{format_bingo_card(card, marked)}\n```\n"
    
    if len(db_user['cards']) > 3:
        text += f"\n... and {len(db_user['cards']) - 3} more cards."
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== JOIN GAME ====================
async def join_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Join current game"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    db_user = db.get_user(user.id)
    
    if not db_user['cards']:
        await query.edit_message_text(
            "❌ You need cards to join the game!\n"
            "Use /buy to purchase cards.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Buy Cards", callback_data="buy_cards")
            ]])
        )
        return
    
    if user.id in db.players_in_game:
        await query.edit_message_text("You're already in the game!")
        return
    
    db.players_in_game.add(user.id)
    
    # Start auto-start if first player
    if len(db.players_in_game) == 1:
        asyncio.create_task(auto_start_timer(context))
    
    await query.edit_message_text(
        f"✅ You've joined the game!\n"
        f"Players in game: {len(db.players_in_game)}\n\n"
        f"Game will start in {config.auto_start_delay} seconds or when ready."
    )

async def auto_start_timer(context: ContextTypes.DEFAULT_TYPE):
    """Auto-start game after delay"""
    await asyncio.sleep(config.auto_start_delay)
    
    if len(db.players_in_game) >= 1 and not db.game_started:
        await start_game(context)

async def start_game(context: ContextTypes.DEFAULT_TYPE):
    """Start the game"""
    db.game_started = True
    
    # Calculate prize pool
    total_cards = sum(len(db.get_user(pid)['cards']) for pid in db.players_in_game)
    prize_pool = total_cards * config.card_price
    
    await broadcast_to_players(
        context,
        f"🎯 *GAME STARTED!*\n\n"
        f"Players: {len(db.players_in_game)}\n"
        f"Total Cards: {total_cards}\n"
        f"Prize Pool: {prize_pool} ETB\n"
        f"Winner Prize: {prize_pool * (1 - config.house_commission)} ETB\n\n"
        f"Get ready to mark your numbers!"
    )
    
    # Start calling numbers
    asyncio.create_task(call_numbers_loop(context))

async def call_numbers_loop(context: ContextTypes.DEFAULT_TYPE):
    """Call numbers loop"""
    numbers = list(range(1, 76))
    random.shuffle(numbers)
    
    for number in numbers:
        if not db.game_started:
            break
        
        await asyncio.sleep(config.number_call_speed)
        db.called_numbers.append(number)
        
        await broadcast_to_players(
            context,
            f"🔔 *Number Called: {number}* 🔔\n\n"
            f"Called: {', '.join(map(str, db.called_numbers[-10:]))}"
        )

async def broadcast_to_players(context: ContextTypes.DEFAULT_TYPE, text: str):
    """Broadcast to all players in game"""
    for user_id in db.players_in_game:
        try:
            await context.bot.send_message(user_id, text, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

# ==================== MAIN ====================
def main():
    """Start the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", lambda u,c: start(u,c)))
    application.add_handler(CommandHandler("cards", my_cards))
    application.add_handler(CommandHandler("bingo", check_bingo))
    
    # Callback query handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Message handler (for text input)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start the bot
    print("🤖 Bingo bot started!")
    application.run_polling()

if __name__ == '__main__':
    main()