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
AUTO_START_DELAY = 20  # Auto-start game after 20 seconds
HOUSE_PERCENT = 0.20  # 20% house fee

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
PHONE_NUMBER, AMOUNT, PAYMENT_METHOD, REFERENCE = range(4)

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
        # Original BingoGame properties
        self.round_number = 1
        self.cards = {}  # user_id -> card_count (for Telegram tracking)
        self.called_numbers = []
        self.game_started = False
        self.total_pool = 0
        self.house_profit = 0
        self.users = {}  # user_id -> {balance, etc} (for Telegram tracking)
        self.withdraw_requests = {}
        
        # Payment requests tracking
        self.payment_requests = {}  # request_id -> payment details
        
        # Existing GameManager properties
        self.active_games = {}
        self.game_connections = {}
        self.taken_cards = {}
        self.game_winner = {}
        self.number_tasks = {}
        self.countdown_timers = {}
        self.bot_app = None
        self.user_connections = {}
        self.MAX_CONNECTIONS_PER_USER = 2
        
        # Auto-start timer
        self.auto_start_timer = None
        self.first_card_time = None
        
        # Sync with existing game structure
        self.game_id = 1  # Default game ID
    
    # ==================== PAYMENT SYSTEM ====================
    
    async def show_payment_methods(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show available payment methods"""
        user_id = update.effective_user.id
        
        keyboard = [
            [InlineKeyboardButton("📱 Telebirr", callback_data="pay_telebirr")],
            [InlineKeyboardButton("💳 CBE Birr", callback_data="pay_cbebirr")],
            [InlineKeyboardButton("◀️ Back", callback_data="menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "💰 **Select Payment Method**\n\n"
            "Choose your preferred payment method:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        return PAYMENT_METHOD
    
    async def handle_payment_method(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle payment method selection"""
        query = update.callback_query
        await query.answer()
        
        method = query.data.replace("pay_", "")
        context.user_data['payment_method'] = method
        
        method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])
        
        await query.edit_message_text(
            f"💰 **{method_info['name']} Deposit**\n\n"
            f"**Account:** `{method_info['account']}`\n"
            f"**Account Name:** {method_info['account_name']}\n\n"
            f"**Instructions:**\n"
            f"{method_info['instructions']}\n\n"
            f"📝 **Please enter the amount you want to deposit**\n"
            f"(Min: 10 ETB, Max: 1000 ETB)",
            parse_mode='Markdown'
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
            
            # Ask for phone number
            await update.message.reply_text(
                "📱 **Phone Number**\n\n"
                "Please enter your phone number (09xxxxxxxx):",
                parse_mode='Markdown'
            )
            return PHONE_NUMBER
            
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter a number.")
            return AMOUNT
    
    async def handle_phone_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle phone number input"""
        phone = update.message.text.strip()
        
        # Validate phone number
        phone = phone.replace(' ', '').replace('-', '').replace('+', '')
        
        if phone.startswith('07') and len(phone) == 10:
            phone = '09' + phone[2:]
        elif phone.startswith('09') and len(phone) == 10:
            pass
        elif phone.startswith('251') and len(phone) == 12:
            phone = '09' + phone[3:]
        elif phone.startswith('+251') and len(phone) == 13:
            phone = '09' + phone[4:]
        else:
            await update.message.reply_text(
                "❌ Please enter a valid phone number (09xxxxxxxx or 07xxxxxxxx)"
            )
            return PHONE_NUMBER
        
        context.user_data['phone'] = phone
        
        # Generate payment request
        method = context.user_data.get('payment_method', 'telebirr')
        amount_cents = context.user_data.get('amount')
        amount_etb = context.user_data.get('amount_etb')
        
        # Create payment request in database
        methods = db.get_payment_methods(type='mobile_money', active_only=True)
        if not methods:
            await update.message.reply_text("❌ No payment methods available")
            return ConversationHandler.END
        
        method_id = methods[0]['id'] if method == 'telebirr' else methods[0]['id']
        
        request_id = db.create_payment_request(
            user_id=update.effective_user.id,
            method_id=method_id,
            amount=amount_cents,
            sender_phone=phone
        )
        
        if not request_id:
            await update.message.reply_text("❌ Failed to create payment request")
            return ConversationHandler.END
        
        # Store in context
        context.user_data['payment_request_id'] = request_id
        
        method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])
        
        # Show payment instructions
        instructions = (
            f"**Payment Instructions**\n\n"
            f"1. Dial *127# for Telebirr or *847# for CBE Birr\n"
            f"2. Select 'Send Money'\n"
            f"3. Enter account number: **{method_info['account']}**\n"
            f"4. Enter amount: **{amount_etb:.0f} ETB**\n"
            f"5. Enter your PIN\n"
            f"6. Save the transaction reference number\n\n"
            f"After completing the payment, send the reference number here."
        )
        
        message = (
            f"💳 **Payment Request Created**\n\n"
            f"💰 **Amount:** {amount_etb:.0f} ETB\n"
            f"📱 **Phone:** {phone}\n"
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
    
    async def handle_payment_reference(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle payment reference submission"""
        reference = update.message.text.strip()
        user = update.effective_user
        
        request_id = context.user_data.get('payment_request_id')
        amount_etb = context.user_data.get('amount_etb', 0)
        phone = context.user_data.get('phone', 'Unknown')
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
                f"📱 **Phone:** {phone}\n"
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
                         f"📱 **Phone:** {phone}\n"
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
        for p in pending[:5]:  # Show first 5
            text += f"🆔 `{p['request_id']}`\n"
            text += f"👤 {p['first_name']} (ID: `{p['user_id']}`)\n"
            text += f"💰 {p['amount']/100:.2f} ETB\n"
            text += f"📱 {p['sender_phone']}\n"
            text += f"⏰ {p['created_at']}\n\n"
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="admin_pending"),
                InlineKeyboardButton("◀️ Admin Panel", callback_data="admin")
            ]])
        )
    
    # ==================== AUTO-START FUNCTIONALITY ====================
    
    async def start_auto_start_timer(self, game_id: int):
        """Start a timer to auto-start the game after 20 seconds"""
        if self.auto_start_timer:
            self.auto_start_timer.cancel()
        
        self.first_card_time = time.time()
        logger.info(f"Auto-start timer started - game will start in {AUTO_START_DELAY} seconds")
        
        async def auto_start():
            await asyncio.sleep(AUTO_START_DELAY)
            
            # Check if game hasn't started yet and there are players
            if not self.game_started and game_id in self.active_games:
                players = self.active_games[game_id]['players']
                if len(players) > 0:
                    logger.info(f"Auto-starting game {game_id} after {AUTO_START_DELAY} seconds")
                    await self.start_round(game_id)
                else:
                    logger.info("No players, not auto-starting")
        
        self.auto_start_timer = asyncio.create_task(auto_start())
    
    # ==================== EXISTING GAMEMANAGER METHODS ====================
    
    async def connect(self, game_id: int, websocket: WebSocket, user_id: int):
        # Check connection limit
        if self.user_connections.get(user_id, 0) >= self.MAX_CONNECTIONS_PER_USER:
            await websocket.close(code=1008, reason="Too many connections")
            return
        
        await websocket.accept()
        logger.info(f"User {user_id} connected to game {game_id}")
        
        # Track connection
        self.user_connections[user_id] = self.user_connections.get(user_id, 0) + 1
        
        user = db.get_or_create_user(user_id)
        
        if game_id not in self.game_connections:
            self.game_connections[game_id] = []
            self.taken_cards[game_id] = set()
            self.game_started = False
            self.game_winner[game_id] = None
            self.round_number = 1
            self.number_tasks[game_id] = None
            self.countdown_timers[game_id] = 15
            self.active_games[game_id] = {
                'called_numbers': [],
                'players': {},
                'prize_pool': 0,
                'total_cards_sold': 0,
                'last_winner': None,
                'countdown': 15,
                'next_number_time': None
            }
        
        self.game_connections[game_id].append(websocket)
        
        player_name = user.get('first_name', f"Player{user_id}")
        
        if user_id not in self.active_games[game_id]['players']:
            # New player
            self.active_games[game_id]['players'][user_id] = {
                'name': player_name,
                'cards': [],
                'card_ids': [],
                'marked': {},
                'ready': True,  # Auto-ready when they select cards
                'winner': False,
                'total_spent': 0,
                'cards_won': 0,
                'balance': user['balance']
            }
            logger.info(f"Created new player {user_id} in game {game_id} with balance {user['balance']}")
        
        active_games_count = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
        # Send initial state
        await websocket.send_json({
            'type': 'connected',
            'taken_cards': list(self.taken_cards[game_id]),
            'players': self.get_players(game_id),
            'round': self.round_number,
            'game_started': self.game_started,
            'winner': self.game_winner[game_id],
            'called_numbers': self.active_games[game_id]['called_numbers'],
            'countdown': self.countdown_timers[game_id],
            'balance': user['balance'] / 100,
            'active_games': active_games_count,
            'total_stake': total_stake / 100,
            'auto_start_delay': AUTO_START_DELAY,
            'auto_start_active': self.auto_start_timer is not None
        })
        
        # Send player's cards if any
        player = self.active_games[game_id]['players'][user_id]
        for card_id in player['card_ids']:
            card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
            if card_data:
                await websocket.send_json({
                    'type': 'your_card',
                    'card': card_data['card'],
                    'card_id': card_id,
                    'marked': player['marked'].get(card_id, [])
                })
        
        # Start countdown task
        asyncio.create_task(self.update_countdown(game_id))
        
        await self.broadcast(game_id, {
            'type': 'player_joined',
            'players': self.get_players(game_id)
        })
    
    async def update_countdown(self, game_id: int):
        try:
            while game_id in self.active_games:
                await asyncio.sleep(1)
                if game_id in self.countdown_timers:
                    if self.countdown_timers[game_id] > 0:
                        self.countdown_timers[game_id] -= 1
                    await self.broadcast(game_id, {
                        'type': 'countdown',
                        'time': self.countdown_timers[game_id]
                    })
                    if self.countdown_timers[game_id] <= 0 and self.game_started:
                        self.countdown_timers[game_id] = 15
        except:
            pass
    
    def disconnect(self, game_id: int, websocket: WebSocket, user_id: int):
        if game_id in self.game_connections:
            if websocket in self.game_connections[game_id]:
                self.game_connections[game_id].remove(websocket)
            
            # Decrement connection count
            if user_id in self.user_connections:
                self.user_connections[user_id] -= 1
                if self.user_connections[user_id] <= 0:
                    del self.user_connections[user_id]
            
            logger.info(f"User {user_id} disconnected from game {game_id}")
    
    async def broadcast(self, game_id: int, message: dict):
        if game_id in self.game_connections:
            for conn in self.game_connections[game_id][:]:
                try:
                    await conn.send_json(message)
                except:
                    if conn in self.game_connections[game_id]:
                        self.game_connections[game_id].remove(conn)
    
    def get_players(self, game_id: int):
        if game_id not in self.active_games:
            return []
        players = []
        for uid, data in self.active_games[game_id]['players'].items():
            players.append({
                'id': uid,
                'name': data['name'],
                'card_count': len(data['card_ids']),
                'ready': data['ready'],
                'winner': data['winner']
            })
        return players
    
    async def select_cards(self, game_id: int, user_id: int, card_ids: List[int]):
        logger.info(f"select_cards called - game:{game_id}, user:{user_id}, cards:{card_ids}")
        
        if game_id not in self.active_games:
            logger.error(f"Game {game_id} not found")
            return False, "Game not found", 0, None
        
        if self.game_started:
            logger.warning(f"Game {game_id} already started")
            return False, "Game already started", 0, None
        
        if user_id not in self.active_games[game_id]['players']:
            logger.error(f"User {user_id} not found in game {game_id}")
            return False, "Player not found", 0, None
        
        player = self.active_games[game_id]['players'][user_id]
        logger.info(f"Player current cards: {player['card_ids']}, balance: {player['balance']}")
        
        if len(player['card_ids']) + len(card_ids) > MAX_CARDS_PER_PLAYER:
            logger.warning(f"Max cards exceeded: {len(player['card_ids'])} + {len(card_ids)} > {MAX_CARDS_PER_PLAYER}")
            return False, f"Maximum {MAX_CARDS_PER_PLAYER} cards per player", 0, None
        
        # Check cards availability
        for card_id in card_ids:
            if card_id in self.taken_cards[game_id]:
                logger.warning(f"Card {card_id} already taken")
                return False, f"Card {card_id} already taken", 0, None
            
            # Check if card exists in BINGO_CARDS
            card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
            if not card_data:
                logger.error(f"Card {card_id} not found in BINGO_CARDS (total cards: {len(BINGO_CARDS)})")
                return False, f"Card {card_id} not found", 0, None
        
        total_cost = len(card_ids) * CARD_PRICE
        logger.info(f"Total cost: {total_cost}, player balance: {player['balance']}")
        
        # Check balance
        if player['balance'] < total_cost:
            logger.warning(f"Insufficient balance: {player['balance']} < {total_cost}")
            return False, f"Insufficient balance. Need {total_cost/100} ETB", total_cost, None
        
        # Add cards and deduct balance immediately
        was_empty = len(self.active_games[game_id]['players']) == 0 or all(len(p['card_ids']) == 0 for p in self.active_games[game_id]['players'].values())
        
        for card_id in card_ids:
            self.taken_cards[game_id].add(card_id)
            card_data = next(c for c in BINGO_CARDS if c['id'] == card_id)
            player['cards'].append(card_data['card'])
            player['card_ids'].append(card_id)
            player['marked'][card_id] = []
            logger.info(f"Added card {card_id} to player {user_id}")
        
        player['total_spent'] += total_cost
        player['balance'] -= total_cost  # Deduct balance immediately
        player['ready'] = True  # Auto-ready when cards are selected
        self.active_games[game_id]['total_cards_sold'] += len(card_ids)
        self.active_games[game_id]['prize_pool'] = self.active_games[game_id]['total_cards_sold'] * CARD_PRICE
        self.total_pool = self.active_games[game_id]['prize_pool']  # Sync with Telegram tracking
        
        logger.info(f"User {user_id} selected {len(card_ids)} cards, cost: {total_cost}, new balance: {player['balance']}")
        
        # Start auto-start timer if this is the first card
        if was_empty and not self.game_started:
            await self.start_auto_start_timer(game_id)
        
        # Update database
        db.update_balance(
            user_id=user_id,
            amount=-total_cost,
            transaction_type='game_fee',
            description=f'Selected cards for game #{game_id}'
        )
        
        # Broadcast player ready status
        await self.broadcast(game_id, {
            'type': 'player_ready',
            'players': self.get_players(game_id),
            'user_id': user_id
        })
        
        return True, f"Selected {len(card_ids)} cards", total_cost, player['balance']
    
    # ==================== AUTO-START ROUND ====================
    
    async def start_round(self, game_id: int = 1):
        """Start the round and begin drawing numbers"""
        if self.game_started:
            return
        
        if game_id not in self.active_games:
            return
        
        total_cards = self.active_games[game_id]['total_cards_sold']
        if total_cards == 0:
            logger.info("No cards sold, not starting game")
            return
        
        self.game_started = True
        logger.info(f"Round {self.round_number} started with {total_cards} cards")
        
        # Cancel auto-start timer if it exists
        if self.auto_start_timer:
            self.auto_start_timer.cancel()
            self.auto_start_timer = None
        
        # Broadcast game started to all players
        await self.broadcast(game_id, {
            'type': 'game_started',
            'round': self.round_number
        })
        
        asyncio.create_task(self.draw_numbers(game_id))
    
    async def draw_numbers(self, game_id: int = 1):
        """Draw numbers every 3 seconds"""
        numbers = list(range(1, 76))
        random.shuffle(numbers)
        
        for n in numbers:
            await asyncio.sleep(3)
            
            if not self.game_started or self.game_winner.get(game_id):
                break
            
            self.called_numbers.append(n)
            self.active_games[game_id]['called_numbers'].append(n)
            logger.info(f"Number called: {n}")
            
            # Broadcast number to all players
            await self.broadcast(game_id, {
                'type': 'number_called',
                'number': n,
                'called': self.active_games[game_id]['called_numbers']
            })
            
            # Check for winner
            winner = await self.check_winner(game_id, n)
            if winner:
                await self.finish_round(game_id, winner)
                break
    
    async def check_winner(self, game_id: int, last_number: int):
        """Check if someone has won"""
        if game_id not in self.active_games:
            return None
        
        called = set(self.active_games[game_id]['called_numbers'])
        
        for user_id, player in self.active_games[game_id]['players'].items():
            if player['winner']:
                continue
            
            for card_idx, card in enumerate(player['cards']):
                card_id = player['card_ids'][card_idx]
                marked = set(player['marked'].get(card_id, []))
                
                if card[2][2] == 'FREE':
                    marked.add('FREE')
                
                if self.check_card_bingo(card, marked):
                    logger.info(f"BINGO! User {user_id} with card {card_id} at number {last_number}")
                    return user_id
        
        return None
    
    def check_card_bingo(self, card, marked):
        # Check rows
        for row in range(5):
            bingo = True
            for col in range(5):
                val = card[col][row]
                if val != 'FREE' and val not in marked:
                    bingo = False
                    break
            if bingo:
                return True
        
        # Check columns
        for col in range(5):
            bingo = True
            for row in range(5):
                val = card[col][row]
                if val != 'FREE' and val not in marked:
                    bingo = False
                    break
            if bingo:
                return True
        
        # Check diagonals
        diag1 = all(card[i][i] == 'FREE' or card[i][i] in marked for i in range(5))
        diag2 = all(card[4-i][i] == 'FREE' or card[4-i][i] in marked for i in range(5))
        
        return diag1 or diag2
    
    async def finish_round(self, game_id: int, winner_id: int):
        """Finish the round and distribute prizes"""
        if game_id not in self.active_games:
            return
        
        prize_pool = self.active_games[game_id]['prize_pool']
        house_cut = prize_pool * HOUSE_PERCENT
        winner_prize = prize_pool - house_cut
        
        self.house_profit += house_cut
        
        # Update winner's balance
        if winner_id in self.active_games[game_id]['players']:
            player = self.active_games[game_id]['players'][winner_id]
            player['balance'] += winner_prize
            player['winner'] = True
            
            # Update database
            db.update_balance(
                user_id=winner_id,
                amount=winner_prize,
                transaction_type='game_win',
                description=f'Won round {self.round_number} in game #{game_id}'
            )
            
            # Update Telegram tracking
            self.register_user(winner_id)
            if winner_id in self.users:
                self.users[winner_id]['balance'] = player['balance']
        
        self.game_winner[game_id] = {
            'user_id': winner_id,
            'name': self.active_games[game_id]['players'][winner_id]['name'] if winner_id in self.active_games[game_id]['players'] else f"Player{winner_id}",
            'round': self.round_number
        }
        
        logger.info(f"Game {game_id} winner: {winner_id}, prize: {winner_prize/100} ETB")
        
        # Broadcast winner to all players
        await self.broadcast(game_id, {
            'type': 'game_won',
            'winner': self.game_winner[game_id],
            'prize': winner_prize / 100,
            'house_fee': house_cut / 100
        })
        
        # Send Telegram notification to winner
        if self.bot_app:
            try:
                await self.bot_app.bot.send_message(
                    chat_id=winner_id,
                    text=f"🎉 **CONGRATULATIONS!** 🎉\n\n"
                         f"You won round {self.round_number}!\n"
                         f"Prize: **{winner_prize/100} ETB**",
                    parse_mode='Markdown'
                )
            except:
                pass
        
        await asyncio.sleep(5)
        self.reset_round(game_id)
    
    def reset_round(self, game_id: int = 1):
        """Reset for next round"""
        self.round_number += 1
        self.called_numbers = []
        self.game_started = False
        self.auto_start_timer = None
        self.first_card_time = None
        
        if game_id in self.active_games:
            self.active_games[game_id]['called_numbers'] = []
            self.active_games[game_id]['prize_pool'] = 0
            self.active_games[game_id]['total_cards_sold'] = 0
            
            # Reset players
            for player in self.active_games[game_id]['players'].values():
                player['cards'] = []
                player['card_ids'] = []
                player['marked'] = {}
                player['ready'] = False
                player['winner'] = False
            
            # Clear taken cards
            self.taken_cards[game_id] = set()
        
        logger.info(f"Round {self.round_number} ready to start")
    
    def register_user(self, user_id):
        """Register user for Telegram tracking"""
        if user_id not in self.users:
            user_data = db.get_user(user_id)
            self.users[user_id] = {
                "balance": user_data['balance'] if user_data else 0
            }
    
    def mark_number(self, game_id: int, user_id: int, card_id: int, number: int):
        if game_id not in self.active_games:
            return False
        
        if not self.game_started:
            return False
        
        if self.game_winner.get(game_id):
            return False
        
        if user_id not in self.active_games[game_id]['players']:
            return False
        
        player = self.active_games[game_id]['players'][user_id]
        
        if card_id not in player['marked']:
            return False
        
        if number in player['marked'][card_id]:
            return False
        
        player['marked'][card_id].append(number)
        return True

# Initialize the integrated game manager
game_manager = IntegratedBingoGame()

# ==================== TELEGRAM BOT SETUP ====================

# Payment conversation handler
payment_conv = ConversationHandler(
    entry_points=[CommandHandler('deposit', game_manager.show_payment_methods)],
    states={
        PAYMENT_METHOD: [CallbackQueryHandler(game_manager.handle_payment_method, pattern='^pay_')],
        AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, game_manager.handle_deposit_amount)],
        PHONE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, game_manager.handle_phone_number)],
        REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, game_manager.handle_payment_reference)],
    },
    fallbacks=[CommandHandler('cancel', cancel_command)],
    name="payment_conversation",
    allow_reentry=True
)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation"""
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

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
        f"💰 Balance: **{balance:.2f} ETB**\n\n"
        f"Choose an option:",
        parse_mode='Markdown',
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
            "🎮 **Click to open game**\n\n"
            f"⏱️ Game will auto-start {AUTO_START_DELAY} seconds after first card is selected!\n\n"
            f"💰 Your balance: **{user_data['balance']/100:.2f} ETB**",
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
        await query.edit_message_text(
            "💰 **Deposit Methods**\n\n"
            "Choose your payment method:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Telebirr", callback_data="pay_telebirr")],
                [InlineKeyboardButton("💳 CBE Birr", callback_data="pay_cbebirr")],
                [InlineKeyboardButton("◀️ Cancel", callback_data="menu")]
            ])
        )
        return PAYMENT_METHOD
    
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
            "2. Choose your cards (1-1000)\n"
            "3. Game auto-starts **20 seconds** after first card is selected!\n"
            "4. Numbers are called automatically every 3 seconds\n"
            "5. Mark numbers as they are called\n"
            "6. Click BINGO when you win!\n\n"
            f"**Price per Card:** {CARD_PRICE/100} ETB\n"
            f"**House Fee:** 20%\n\n"
            "**Deposit Methods:**\n"
            "• 📱 **Telebirr** - Dial *127#\n"
            "• 💳 **CBE Birr** - Dial *847#\n\n"
            "**Commands:**\n"
            "/start - Main menu\n"
            "/deposit - Add funds\n"
            "/withdraw <amount> - Request withdrawal\n"
            "/balance - Check balance\n\n"
            "**Need help?** Contact @{ADMIN_USER_ID}"
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
            f"🎯 Main Menu\n💰 Balance: **{balance:.2f} ETB**",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

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
    
    # Store in database (you'll need to add a withdrawals table)
    # For now, store in memory
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
        f"⏳ Waiting for admin approval. You'll be notified once processed.",
        parse_mode='Markdown'
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
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(?!approve_withdraw_|reject_withdraw_).*$"))
    application.add_handler(CallbackQueryHandler(handle_withdraw_approval, pattern="^(approve_withdraw_|reject_withdraw_)"))
    
    # Add message handler for non-command messages
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
        "auto_start_delay": AUTO_START_DELAY
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
                {"id": 1, "name": "Full House", "description": "All numbers on card"},
                {"id": 2, "name": "Four Corners", "description": "All four corners"},
                {"id": 3, "name": "X Pattern", "description": "Both diagonals"},
                {"id": 4, "name": "Blackout", "description": "Entire card filled"}
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
        "auto_start_delay": AUTO_START_DELAY
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
                logger.info(f"Processing select_cards for user {user_id} with cards {data['card_ids']}")
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
                logger.info(f"Sent cards_selected response to user {user_id}: success={success}")
                
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
                    winner_id = await game_manager.check_winner(game_id, game_manager.active_games[game_id]['called_numbers'][-1] if game_manager.active_games[game_id]['called_numbers'] else 0)
                    if winner_id == user_id:
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