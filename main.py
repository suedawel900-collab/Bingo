import os
import json
import random
import asyncio
import logging
import time
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Dict, Set, List, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)

from models import Database

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN', '8578474198:AAGcqcyTihBMxV-gtqukkbU_SBk1EszG-7w')
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID', '8741250511')
RAILWAY_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'bingo-production-a078.up.railway.app')
# Ensure URL has https://
if not RAILWAY_URL.startswith('http'):
    BASE_URL = f"https://{RAILWAY_URL}"
else:
    BASE_URL = RAILWAY_URL

CARD_PRICE = 1000  # 10 ETB in cents
MAX_CARDS_PER_PLAYER = 20
WELCOME_BONUS = 1000  # 10 ETB welcome bonus
AUTO_START_DELAY = 30  # 30 seconds
HOUSE_PERCENT = 0.20  # 20% house fee
ROUND_RESET_DELAY = 10  # Wait 10 seconds before resetting for next round

# Payment methods
PAYMENT_METHODS = {
    "telebirr": {
        "name": "Telebirr",
        "account": "0953933030",
        "account_name": "Bingo Bot",
        "instructions": "Dial *127# and send money to 0953933030"
    },
    "cbebirr": {
        "name": "CBE Birr",
        "account": "0953933030",
        "account_name": "Bingo Bot",
        "instructions": "Dial *847# and send money to 0953933030"
    }
}

# Conversation states
METHOD, AMOUNT, REFERENCE = range(3)

logger.info(f"✅ Using BASE_URL: {BASE_URL}")

# Initialize database
db = Database()

# ==================== Generate 1000 cards ====================
CARDS_FILE = "static/bingo_cards.json"

def generate_default_cards():
    """Generate 1000 bingo cards with proper BINGO format"""
    cards = []
    for i in range(1, 1001):  # Generate 1000 cards (1-1000)
        card = []
        # Generate 5 columns (B, I, N, G, O)
        for col in range(5):
            column = []
            min_num = col * 15 + 1
            max_num = (col + 1) * 15
            # Generate 5 unique numbers for each column
            numbers = random.sample(range(min_num, max_num + 1), 5)
            column.extend(numbers)
            card.append(column)
        # Set FREE space in the center (row 2, col 2)
        card[2][2] = "FREE"
        cards.append({"id": i, "card": card})
    
    logger.info(f"✅ Generated {len(cards)} cards (1-1000)")
    return cards

# Load cards with validation
try:
    if os.path.exists(CARDS_FILE):
        with open(CARDS_FILE, 'r') as f:
            BINGO_CARDS = json.load(f)
            logger.info(f"✅ Loaded {len(BINGO_CARDS)} cards from file")
            
            # Validate that we have 1000 cards
            if len(BINGO_CARDS) < 1000:
                logger.warning(f"Only {len(BINGO_CARDS)} cards found, regenerating to 1000...")
                BINGO_CARDS = generate_default_cards()
                os.makedirs("static", exist_ok=True)
                with open(CARDS_FILE, 'w') as f:
                    json.dump(BINGO_CARDS, f)
                logger.info(f"✅ Regenerated and saved {len(BINGO_CARDS)} cards")
    else:
        BINGO_CARDS = generate_default_cards()
        os.makedirs("static", exist_ok=True)
        with open(CARDS_FILE, 'w') as f:
            json.dump(BINGO_CARDS, f)
        logger.info(f"✅ Generated and saved {len(BINGO_CARDS)} default cards")
except Exception as e:
    logger.error(f"Error loading cards: {e}")
    BINGO_CARDS = generate_default_cards()
    logger.info(f"✅ Generated {len(BINGO_CARDS)} cards as fallback")

# Templates
templates = Jinja2Templates(directory="templates")
os.makedirs("static", exist_ok=True)

# ==================== INTEGRATED BINGO GAME CLASS ====================
class IntegratedBingoGame:
    def __init__(self):
        self.round_number = 1
        self.cards = {}
        self.called_numbers = []
        self.game_started = False
        self.total_pool = 0
        self.house_profit = 0
        self.users = {}
        self.withdraw_requests = {}
        self.payment_requests = {}
        self.active_games = {}
        self.game_connections = {}
        self.taken_cards = {}
        self.game_winner = {}
        self.number_tasks = {}
        self.countdown_timers = {}
        self.bot_app = None
        self.user_connections = {}
        self.MAX_CONNECTIONS_PER_USER = 2
        self.auto_start_timer = None
        self.first_card_time = None
        self.reset_timer = None
        self.stop_number_generation = False
        self.current_game_id = 1
        self.game_id = 1
    
    # ==================== PAYMENT SYSTEM ====================
    
    async def show_payment_methods(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show available payment methods"""
        keyboard = [
            [InlineKeyboardButton("📱 Telebirr", callback_data="pay_telebirr")],
            [InlineKeyboardButton("💳 CBE Birr", callback_data="pay_cbebirr")],
            [InlineKeyboardButton("◀️ Back", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "💰 Select Payment Method\n\n"
            "Choose your preferred payment method:",
            reply_markup=reply_markup
        )
        return METHOD
    
    async def handle_payment_method(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle payment method selection"""
        query = update.callback_query
        await query.answer()
        
        method = query.data.replace("pay_", "")
        context.user_data['payment_method'] = method
        
        method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])
        
        await query.edit_message_text(
            f"💰 {method_info['name']} Deposit\n\n"
            f"Account: {method_info['account']}\n"
            f"Account Name: {method_info['account_name']}\n\n"
            f"Instructions:\n"
            f"{method_info['instructions']}\n\n"
            f"📝 Please enter the amount you want to deposit\n"
            f"(Min: 10 ETB, Max: 1000 ETB)"
        )
        return AMOUNT
    
    async def handle_deposit_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle deposit amount input"""
        try:
            amount = float(update.message.text.strip())
            
            # Validate amount (10-1000 ETB)
            if amount < 10:
                await update.message.reply_text("❌ Minimum deposit is 10 ETB")
                return AMOUNT
            if amount > 1000:
                await update.message.reply_text("❌ Maximum deposit is 1000 ETB")
                return AMOUNT
            
            amount_cents = int(amount * 100)
            context.user_data['amount'] = amount_cents
            context.user_data['amount_etb'] = amount
            
            method = context.user_data.get('payment_method', 'telebirr')
            method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])
            
            # Create payment request
            methods = db.get_payment_methods(type='mobile_money', active_only=True)
            if not methods:
                await update.message.reply_text("❌ No payment methods available")
                return ConversationHandler.END
            
            method_id = methods[0]['id'] if method == 'telebirr' else methods[0]['id']
            
            request_id = db.create_payment_request(
                user_id=update.effective_user.id,
                method_id=method_id,
                amount=amount_cents,
                sender_phone=""
            )
            
            if not request_id:
                await update.message.reply_text("❌ Failed to create payment request")
                return ConversationHandler.END
            
            context.user_data['payment_request_id'] = request_id
            
            # Show payment instructions
            instructions = (
                f"**Payment Instructions**\n\n"
                f"1. Dial *127# for Telebirr or *847# for CBE Birr\n"
                f"2. Select 'Send Money'\n"
                f"3. Enter account number: `{method_info['account']}`\n"
                f"4. Enter amount: **{amount:.0f} ETB**\n"
                f"5. Enter your PIN\n"
                f"6. Save the transaction reference number\n\n"
                f"After completing the payment, send the reference number here."
            )
            
            message = (
                f"💳 **Payment Request Created**\n\n"
                f"💰 **Amount:** {amount:.0f} ETB\n"
                f"💳 **Method:** {method_info['name']}\n"
                f"🆔 **Request ID:** `{request_id}`\n\n"
                f"{instructions}"
            )
            
            await update.message.reply_text(
                message,
                parse_mode='Markdown'
            )
            
            await update.message.reply_text(
                "📝 **Enter Transaction Reference**\n\n"
                "Please enter the reference number you received after payment:",
                parse_mode='Markdown'
            )
            
            return REFERENCE
            
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter a number.")
            return AMOUNT
    
    async def handle_payment_reference(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle payment reference submission"""
        reference = update.message.text.strip()
        user = update.effective_user
        
        request_id = context.user_data.get('payment_request_id')
        amount_etb = context.user_data.get('amount_etb', 0)
        method = context.user_data.get('payment_method', 'telebirr')
        
        if not request_id:
            # Try to find pending request
            pending = db.get_user_payment_requests(user.id, limit=1)
            if pending and pending[0]['status'] == 'pending':
                request_id = pending[0]['request_id']
            else:
                await update.message.reply_text(
                    "❌ Session expired. Please start over with /deposit"
                )
                return ConversationHandler.END
        
        # Add payment proof
        success = db.add_payment_proof(
            request_id=request_id,
            proof_type='text',
            proof_data=reference
        )
        
        if success:
            method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])
            
            await update.message.reply_text(
                f"✅ **Payment Report Submitted!**\n\n"
                f"💰 **Amount:** {amount_etb:.0f} ETB\n"
                f"💳 **Method:** {method_info['name']}\n"
                f"🆔 **Request ID:** `{request_id}`\n"
                f"🔢 **Reference:** `{reference}`\n\n"
                f"⏳ Admin will verify your payment shortly.\n"
                f"You'll be notified once your balance is updated.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Main Menu", callback_data="menu")
                ]])
            )
            
            # Send notification to admin with approve/reject buttons
            if ADMIN_USER_ID:
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Approve", callback_data=f"approve_payment_{request_id}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"reject_payment_{request_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=f"💰 **New Payment Request**\n\n"
                         f"👤 **User:** {user.first_name}\n"
                         f"🆔 **User ID:** `{user.id}`\n"
                         f"💰 **Amount:** {amount_etb:.0f} ETB\n"
                         f"💳 **Method:** {method_info['name']}\n"
                         f"🆔 **Request ID:** `{request_id}`\n"
                         f"🔢 **Reference:** `{reference}`\n\n"
                         f"Please verify and approve/reject:",
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
        else:
            await update.message.reply_text(
                "❌ Failed to save reference. Please contact admin.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📞 Contact Admin", url=f"tg://user?id={ADMIN_USER_ID}")
                ]])
            )
        
        context.user_data.clear()
        return ConversationHandler.END
    
    async def handle_payment_approval(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle payment approval from admin"""
        query = update.callback_query
        await query.answer()
        
        if str(update.effective_user.id) != ADMIN_USER_ID:
            await query.edit_message_text("❌ Unauthorized")
            return
        
        data = query.data.split('_')
        action = data[0]  # approve or reject
        request_id = data[2]
        
        # Get payment request from database
        request = db.get_payment_request(request_id)
        
        if not request:
            await query.edit_message_text("❌ Payment request not found")
            return
        
        if action == "approve":
            # Update payment request status
            db.update_payment_request_status(
                request_id=request_id,
                status='completed',
                admin_notes=f"Approved by admin {update.effective_user.id}"
            )
            
            # Add balance to user
            result = db.update_balance(
                user_id=request['user_id'],
                amount=request['amount'],
                transaction_type='deposit',
                description=f'Payment via {PAYMENT_METHODS["telebirr"]["name"]} - {request_id}'
            )
            
            if result:
                # Notify user
                await context.bot.send_message(
                    chat_id=request['user_id'],
                    text=f"✅ **Payment Approved!**\n\n"
                         f"Your payment of **{request['amount']/100:.2f} ETB** has been approved.\n"
                         f"New balance: **{result['new_balance']/100:.2f} ETB**\n\n"
                         f"Thank you for using Bingo Bot!",
                    parse_mode='Markdown'
                )
                
                await query.edit_message_text(
                    f"✅ **Payment Approved**\n\n"
                    f"Request ID: `{request_id}`\n"
                    f"Amount: {request['amount']/100:.2f} ETB\n"
                    f"User: {request['first_name']} (ID: `{request['user_id']}`)\n\n"
                    f"Balance updated successfully!",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text("❌ Failed to update balance")
        
        else:  # reject
            db.update_payment_request_status(
                request_id=request_id,
                status='rejected',
                admin_notes=f"Rejected by admin {update.effective_user.id}"
            )
            
            # Notify user
            await context.bot.send_message(
                chat_id=request['user_id'],
                text=f"❌ **Payment Rejected**\n\n"
                     f"Your payment of **{request['amount']/100:.2f} ETB** has been rejected.\n"
                     f"Please contact admin if you believe this is an error.\n\n"
                     f"Admin: @{ADMIN_USER_ID}",
                parse_mode='Markdown'
            )
            
            await query.edit_message_text(
                f"❌ **Payment Rejected**\n\n"
                f"Request ID: `{request_id}`\n"
                f"Amount: {request['amount']/100:.2f} ETB\n"
                f"User: {request['first_name']} (ID: `{request['user_id']}`)",
                parse_mode='Markdown'
            )
    
    async def show_pending_payments(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show pending payments for admin"""
        if str(update.effective_user.id) != ADMIN_USER_ID:
            return
        
        query = update.callback_query
        await query.answer()
        
        pending = db.get_pending_payment_requests(limit=20)
        
        if not pending:
            await query.edit_message_text(
                "📊 No pending payments.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="admin")
                ]])
            )
            return
        
        text = "📊 **Pending Payments**\n\n"
        for p in pending[:5]:
            text += f"🆔 `{p['request_id']}`\n"
            text += f"👤 {p['first_name']} (ID: `{p['user_id']}`)\n"
            text += f"💰 {p['amount']/100:.2f} ETB\n"
            text += f"⏰ {p['created_at']}\n\n"
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="admin_pending"),
                InlineKeyboardButton("◀️ Admin Panel", callback_data="admin")
            ]])
        )

# Initialize the integrated game manager
game_manager = IntegratedBingoGame()

# ==================== TELEGRAM BOT SETUP ====================

# Define cancel command
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation"""
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# Payment conversation handler
payment_conv = ConversationHandler(
    entry_points=[CommandHandler('deposit', game_manager.show_payment_methods)],
    states={
        METHOD: [CallbackQueryHandler(game_manager.handle_payment_method, pattern='^pay_')],
        AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, game_manager.handle_deposit_amount)],
        REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, game_manager.handle_payment_reference)],
    },
    fallbacks=[CommandHandler('cancel', cancel_command)],
    name="payment_conversation",
    allow_reentry=True
)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_data = db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    
    balance = user_data['balance'] / 100
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    
    if str(user.id) == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎯 Welcome, {user.first_name}!\n"
        f"💰 Balance: {balance:.2f} ETB\n\n"
        f"Choose an option:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data
    
    # Handle payment approvals
    if data.startswith('approve_payment_') or data.startswith('reject_payment_'):
        await game_manager.handle_payment_approval(update, context)
        return
    
    # Handle payment method selection
    if data == "pay_telebirr" or data == "pay_cbebirr":
        return await game_manager.handle_payment_method(update, context)
    
    if data == "play":
        user_data = db.get_user(user.id)
        if not user_data or user_data['balance'] < CARD_PRICE:
            await query.edit_message_text(
                f"❌ Insufficient balance. Need {CARD_PRICE/100} ETB minimum.\n\n"
                f"Use /deposit to add funds.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💳 Deposit", callback_data="deposit"),
                    InlineKeyboardButton("◀️ Back", callback_data="menu")
                ]])
            )
            return
        
        webapp_url = f"{BASE_URL}/game?user_id={user.id}&game_id=1"
        
        await query.edit_message_text(
            f"🎮 **Click to open game**\n\n"
            f"⏱️ Game will auto-start **{AUTO_START_DELAY} seconds** after first card is selected!\n\n"
            f"💰 Your balance: **{user_data['balance']/100:.2f} ETB**\n\n"
            f"🏆 **WINNING PATTERN:** Complete **ONE ROW** to win!\n"
            f"⏹️ Numbers stop automatically when someone wins!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Open Game", web_app={'url': webapp_url})
            ]])
        )
    
    elif data == "balance":
        user_data = db.get_user(user.id)
        balance = user_data['balance'] / 100 if user_data else 0
        active_games_count = db.get_active_games_count(user.id)
        total_stake = db.get_total_stake(user.id) / 100
        
        # Get pending payments
        pending_payments = db.get_user_payment_requests(user.id, limit=5)
        pending_count = len([p for p in pending_payments if p['status'] == 'pending'])
        
        text = (
            f"💰 **Your Balance**\n\n"
            f"**Current:** {balance:.2f} ETB\n"
            f"**Active Games:** {active_games_count}\n"
            f"**Total Stake:** {total_stake:.2f} ETB\n"
            f"**Games Played:** {user_data['games_played']}\n"
            f"**Games Won:** {user_data['games_won']}\n"
        )
        
        if pending_count > 0:
            text += f"\n⏳ **Pending Payments:** {pending_count}"
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Deposit", callback_data="deposit"),
                InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    
    elif data == "deposit":
        keyboard = [
            [InlineKeyboardButton("📱 Telebirr", callback_data="pay_telebirr")],
            [InlineKeyboardButton("💳 CBE Birr", callback_data="pay_cbebirr")],
            [InlineKeyboardButton("◀️ Cancel", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "💰 **Deposit Methods**\n\n"
            "Choose your payment method:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return METHOD
    
    elif data == "withdraw":
        await query.edit_message_text(
            "📤 **Withdrawal**\n\n"
            "To withdraw funds, please use the /withdraw command followed by the amount.\n\n"
            "Example: `/withdraw 50` for 50 ETB\n\n"
            "Your withdrawal request will be sent to admin for approval.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    
    elif data == "help":
        help_text = (
            "❓ **Bingo Bot Help**\n\n"
            "**How to Play:**\n"
            "1. Click 'Play Bingo' to open the game\n"
            "2. Choose your cards (1-1000) - you can buy up to 20 cards!\n"
            "3. Game auto-starts **30 seconds** after first card is selected!\n"
            "4. Numbers are called automatically every 3 seconds\n"
            "5. Mark numbers on your cards as they are called\n"
            "6. Complete **ONE ROW** to win!\n"
            "7. Numbers stop immediately when someone wins!\n"
            "8. Winner gets **90%** of the prize pool!\n"
            "9. Game resets automatically after 10 seconds for next round\n\n"
            f"**Price per Card:** {CARD_PRICE/100} ETB\n"
            f"**House Fee:** 20%\n\n"
            "**Deposit Methods:**\n"
            "• 📱 **Telebirr** - Dial *127#\n"
            "• 💳 **CBE Birr** - Dial *847#\n\n"
            "**Commands:**\n"
            "/start - Main menu\n"
            "/deposit - Add funds\n"
            "/withdraw <amount> - Request withdrawal\n"
            "/balance - Check balance\n"
            "/cancel - Cancel current operation\n\n"
            f"**Need help?** Contact admin"
        )
        await query.edit_message_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    
    elif data == "admin" and str(user.id) == ADMIN_USER_ID:
        stats = db.get_system_stats()
        pending_payments = len(db.get_pending_payment_requests(limit=100))
        
        text = (
            f"👑 **Admin Panel**\n\n"
            f"**System Stats:**\n"
            f"👥 Total Users: {stats.get('total_users', 0)}\n"
            f"💰 Total Balance: {stats.get('total_balance', 0)/100:.2f} ETB\n"
            f"📥 Total Deposits: {stats.get('total_deposits', 0)/100:.2f} ETB\n"
            f"📤 Total Withdrawals: {stats.get('total_withdrawals', 0)/100:.2f} ETB\n"
            f"🎮 Active Games: {stats.get('active_games', 0)}\n\n"
            f"⏳ **Pending Approvals:** {pending_payments}\n\n"
            f"Select an option:"
        )
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Pending Payments", callback_data="admin_pending")],
                [InlineKeyboardButton("📊 System Stats", callback_data="admin_stats")],
                [InlineKeyboardButton("◀️ Main Menu", callback_data="menu")]
            ])
        )
    
    elif data == "admin_pending" and str(user.id) == ADMIN_USER_ID:
        await game_manager.show_pending_payments(update, context)
    
    elif data == "admin_stats" and str(user.id) == ADMIN_USER_ID:
        stats = db.get_system_stats()
        
        await query.edit_message_text(
            f"📊 **System Statistics**\n\n"
            f"**Users:** {stats.get('total_users', 0)}\n"
            f"**Total Balance:** {stats.get('total_balance', 0)/100:.2f} ETB\n"
            f"**Total Deposits:** {stats.get('total_deposits', 0)/100:.2f} ETB\n"
            f"**Total Withdrawals:** {stats.get('total_withdrawals', 0)/100:.2f} ETB\n"
            f"**Active Games:** {stats.get('active_games', 0)}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats"),
                InlineKeyboardButton("◀️ Back", callback_data="admin")
            ]])
        )
    
    elif data == "menu":
        user_data = db.get_user(user.id)
        balance = user_data['balance'] / 100 if user_data else 0
        
        keyboard = [
            [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
            [InlineKeyboardButton("💰 Balance", callback_data="balance")],
            [InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("📤 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        if str(user.id) == ADMIN_USER_ID:
            keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])
        
        await query.edit_message_text(
            f"🎯 **Main Menu**\n💰 Balance: **{balance:.2f} ETB**",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (for payment references)"""
    # This is now handled by the conversation handler
    pass

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal requests"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /withdraw <amount>\n"
            "Example: `/withdraw 50` for 50 ETB",
            parse_mode='Markdown'
        )
        return
    
    try:
        amount = float(context.args[0])
        if amount < 10:
            await update.message.reply_text("❌ Minimum withdrawal is 10 ETB")
            return
        if amount > 10000:
            await update.message.reply_text("❌ Maximum withdrawal is 10000 ETB")
            return
        
        amount_cents = int(amount * 100)
    except:
        await update.message.reply_text("❌ Invalid amount")
        return
    
    user_data = db.get_user(user_id)
    if not user_data or user_data['balance'] < amount_cents:
        await update.message.reply_text("❌ Insufficient balance")
        return
    
    # Create withdrawal request
    request_id = str(uuid.uuid4())[:8].upper()
    
    # Store in memory
    game_manager.withdraw_requests[request_id] = {
        'user_id': user_id,
        'amount': amount_cents,
        'amount_etb': amount,
        'status': 'pending',
        'username': update.effective_user.first_name,
        'created_at': datetime.now().isoformat()
    }
    
    await update.message.reply_text(
        f"✅ Withdrawal request #{request_id} created for {amount:.2f} ETB\n\n"
        f"⏳ Waiting for admin approval. You'll be notified once processed."
    )
    
    # Notify admin
    if ADMIN_USER_ID:
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_withdraw_{request_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_withdraw_{request_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"📤 **Withdrawal Request**\n\n"
                 f"👤 **User:** {update.effective_user.first_name}\n"
                 f"🆔 **User ID:** `{user_id}`\n"
                 f"💰 **Amount:** {amount:.2f} ETB\n"
                 f"🆔 **Request ID:** `{request_id}`\n"
                 f"💰 **User Balance:** {user_data['balance']/100:.2f} ETB",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def handle_withdraw_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal approval/rejection"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    data = query.data.split('_')
    action = data[0]  # approve or reject
    request_id = data[2]
    
    request = game_manager.withdraw_requests.get(request_id)
    
    if not request:
        await query.edit_message_text("❌ Withdrawal request not found")
        return
    
    if request['status'] != 'pending':
        await query.edit_message_text(f"❌ Request already {request['status']}")
        return
    
    if action == "approve":
        # Deduct from user balance
        result = db.update_balance(
            user_id=request['user_id'],
            amount=-request['amount'],
            transaction_type='withdrawal',
            description=f'Withdrawal request #{request_id}'
        )
        
        if result:
            request['status'] = 'approved'
            
            # Notify user
            await context.bot.send_message(
                chat_id=request['user_id'],
                text=f"✅ **Withdrawal Approved!**\n\n"
                     f"Amount: **{request['amount_etb']:.2f} ETB**\n"
                     f"Request ID: #{request_id}\n\n"
                     f"Funds have been sent to your account.",
                parse_mode='Markdown'
            )
            
            await query.edit_message_text(
                f"✅ **Withdrawal Approved**\n\n"
                f"Request ID: `{request_id}`\n"
                f"Amount: {request['amount_etb']:.2f} ETB\n"
                f"User: {request['username']}",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Failed to process withdrawal")
    
    else:  # reject
        request['status'] = 'rejected'
        
        # Notify user
        await context.bot.send_message(
            chat_id=request['user_id'],
            text=f"❌ **Withdrawal Rejected**\n\n"
                 f"Amount: **{request['amount_etb']:.2f} ETB**\n"
                 f"Request ID: #{request_id}\n\n"
                 f"Please contact admin for more information.",
            parse_mode='Markdown'
        )
        
        await query.edit_message_text(
            f"❌ **Withdrawal Rejected**\n\n"
            f"Request ID: `{request_id}`\n"
            f"Amount: {request['amount_etb']:.2f} ETB\n"
            f"User: {request['username']}",
            parse_mode='Markdown'
        )

async def setup_bot():
    """Initialize bot with webhook mode"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add conversation handler for deposits
    application.add_handler(payment_conv)
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("withdraw", withdraw_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    
    # Add callback query handlers
    application.add_handler(CallbackQueryHandler(handle_withdraw_approval, pattern="^(approve_withdraw_|reject_withdraw_)"))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Initialize
    await application.initialize()
    await application.start()
    
    # Set webhook
    webhook_url = f"{BASE_URL}/webhook"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"🤖 Webhook set to {webhook_url}")
    
    return application

# ==================== LIFESPAN MANAGEMENT ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting up...")
    game_manager.bot_app = await setup_bot()
    yield
    # Shutdown
    logger.info("🛑 Shutting down...")
    
    # Close all WebSocket connections
    for connections in game_manager.game_connections.values():
        for conn in connections:
            try:
                await conn.close()
            except:
                pass
    
    if game_manager.bot_app:
        await game_manager.bot_app.stop()
        await game_manager.bot_app.shutdown()

# Create FastAPI app
app = FastAPI(title="Bingo Game", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== WEBHOOK ENDPOINT ====================

@app.post("/webhook")
async def webhook(request: Request):
    """Telegram webhook endpoint"""
    data = await request.json()
    update = Update.de_json(data, game_manager.bot_app.bot)
    await game_manager.bot_app.process_update(update)
    return {"ok": True}

# ==================== FASTAPI ROUTES ====================

@app.get("/")
async def root():
    return {
        "status": "online", 
        "cards": len(BINGO_CARDS),
        "price_per_card": CARD_PRICE / 100,
        "max_cards_per_player": MAX_CARDS_PER_PLAYER,
        "auto_start_delay": AUTO_START_DELAY,
        "round_reset_delay": ROUND_RESET_DELAY,
        "winning_pattern": "One Complete Row"
    }

@app.get("/health")
async def health():
    """Health check for Railway"""
    return {"status": "healthy"}

# ==================== API ENDPOINTS ====================

@app.get("/api/patterns")
async def list_patterns():
    """Return list of all bingo patterns"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, description FROM patterns ORDER BY id")
        rows = cursor.fetchall()
        conn.close()
        
        patterns = []
        for row in rows:
            patterns.append({
                "id": row[0],
                "name": row[1],
                "description": row[2]
            })
        
        # If no patterns in DB, return defaults
        if not patterns:
            patterns = [
                {"id": 1, "name": "One Row", "description": "Complete any single row"},
                {"id": 2, "name": "Full House", "description": "All numbers on card"},
                {"id": 3, "name": "Four Corners", "description": "All four corners"},
                {"id": 4, "name": "X Pattern", "description": "Both diagonals"}
            ]
        
        return patterns
    except Exception as e:
        logger.error(f"Error fetching patterns: {e}")
        return []

@app.get("/api/user/{user_id}")
async def get_user_info(user_id: int):
    """Get user information"""
    try:
        user = db.get_user(user_id)
        if not user:
            user = db.get_or_create_user(user_id)
        
        active_games_count = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
        return {
            "user_id": user_id,
            "balance": user['balance'],
            "balance_etb": user['balance'] / 100,
            "active_games": active_games_count,
            "total_stake": total_stake,
            "total_stake_etb": total_stake / 100,
            "games_played": user['games_played'],
            "games_won": user['games_won'],
            "phone_number": user.get('phone_number', '')
        }
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/cards")
async def get_cards_list():
    """Return list of all cards"""
    return {
        "total": len(BINGO_CARDS),
        "cards": [{"id": c["id"]} for c in BINGO_CARDS],
        "price_per_card": CARD_PRICE / 100
    }

@app.get("/api/card/{card_id}")
async def get_card_by_id(card_id: int):
    """Get specific card by ID"""
    card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
    if card:
        return card
    return JSONResponse(content={"error": "Card not found"}, status_code=404)

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    user = db.get_or_create_user(user_id)
    
    return templates.TemplateResponse("bingo.html", {
        "request": request,
        "user_id": user_id,
        "game_id": game_id,
        "admin_id": ADMIN_USER_ID,
        "price_per_card": CARD_PRICE / 100,
        "max_cards": MAX_CARDS_PER_PLAYER,
        "initial_balance": user['balance'] / 100,
        "initial_active_games": db.get_active_games_count(user_id),
        "initial_stake": db.get_total_stake(user_id) / 100,
        "auto_start_delay": AUTO_START_DELAY,
        "round_reset_delay": ROUND_RESET_DELAY,
        "winning_pattern": "Complete ONE ROW to win! Numbers stop automatically!"
    })

# ==================== WEBSOCKET ENDPOINT ====================

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    await game_manager.connect(game_id, websocket, user_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            logger.info(f"WebSocket message: {data['type']} from user {user_id}")
            
            if data['type'] == 'select_cards':
                success, message, cost, new_balance = await game_manager.select_cards(
                    game_id, user_id, data['card_ids']
                )
                
                await websocket.send_json({
                    'type': 'cards_selected',
                    'success': success,
                    'message': message,
                    'cost': cost,
                    'new_balance': new_balance,
                    'card_ids': data['card_ids'] if success else []
                })
                
                if success:
                    for card_id in data['card_ids']:
                        card = next(c for c in BINGO_CARDS if c['id'] == card_id)
                        await websocket.send_json({
                            'type': 'your_card',
                            'card': card['card'],
                            'card_id': card_id
                        })
            
            elif data['type'] == 'mark_number':
                if not game_manager.game_started:
                    await websocket.send_json({
                        'type': 'error',
                        'message': 'Game has not started yet!'
                    })
                    continue
                    
                success = game_manager.mark_number(
                    game_id, user_id, data['card_id'], data['number']
                )
                if success:
                    await websocket.send_json({
                        'type': 'number_marked',
                        'card_id': data['card_id'],
                        'number': data['number']
                    })
            
            elif data['type'] == 'claim_bingo':
                card_id = data.get('card_id')
                if card_id:
                    winner_id = await game_manager.check_winner_row_only(game_id, game_manager.active_games[game_id]['called_numbers'][-1] if game_manager.active_games[game_id]['called_numbers'] else 0)
                    if winner_id == user_id:
                        game_manager.stop_number_generation = True
                        await game_manager.finish_round(game_id, user_id)
                    else:
                        await websocket.send_json({
                            'type': 'error',
                            'message': 'Not a valid bingo or someone else won first'
                        })
            
            elif data['type'] == 'ping':
                await websocket.send_json({'type': 'pong'})
    
    except WebSocketDisconnect:
        game_manager.disconnect(game_id, websocket, user_id)
        logger.info(f"User {user_id} disconnected from game {game_id}")
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}")
        game_manager.disconnect(game_id, websocket, user_id)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)