import os
import json
import random
import asyncio
import logging
import time
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Dict, Set, List, Any, Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from telegram.error import BadRequest, TelegramError

from models import Database

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set")

ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')
if not ADMIN_USER_ID:
    raise ValueError("ADMIN_USER_ID environment variable not set")
try:
    ADMIN_USER_ID = int(ADMIN_USER_ID)
except ValueError:
    raise ValueError("ADMIN_USER_ID must be an integer")

RAILWAY_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'bingo-production-a078.up.railway.app')

if not RAILWAY_URL.startswith('http'):
    BASE_URL = f"https://{RAILWAY_URL}"
else:
    BASE_URL = RAILWAY_URL

CARD_PRICE = 1000
MAX_CARDS_PER_PLAYER = 20
WELCOME_BONUS = 1000
AUTO_START_DELAY = 30
HOUSE_PERCENT = 0.20
ROUND_RESET_DELAY = 10

# Payment methods (both use the same account number)
PAYMENT_METHODS = {
    "telebirr": {
        "name": "Telebirr",
        "account": "0953933030",
        "account_name": "sued awel",
        "instructions": "Dial *127# and send money to 0983994214"
    },
    "cbebirr": {
        "name": "CBE Birr",
        "account": "0953933030",
        "account_name": "sued awel",
        "instructions": "Dial *847# and send money to 0983994214"
    }
}

# Conversation states
SELECT_METHOD, SELECT_AMOUNT, WAIT_TRANSACTION = range(3)
WITHDRAW_AMOUNT, WITHDRAW_PHONE = range(3, 5)

logger.info(f"✅ Using BASE_URL: {BASE_URL}")

db = Database()

# ==================== Cards ====================
CARDS_FILE = "static/bingo_cards.json"

def generate_default_cards():
    cards = []
    for i in range(1, 1001):
        card = []
        for col in range(5):
            column = []
            min_num = col * 15 + 1
            max_num = (col + 1) * 15
            numbers = random.sample(range(min_num, max_num + 1), 5)
            column.extend(numbers)
            card.append(column)
        card[2][2] = "FREE"
        cards.append({"id": i, "card": card})
    logger.info(f"✅ Generated {len(cards)} cards (1-1000)")
    return cards

try:
    if os.path.exists(CARDS_FILE):
        with open(CARDS_FILE, 'r') as f:
            BINGO_CARDS = json.load(f)
        logger.info(f"✅ Loaded {len(BINGO_CARDS)} cards from file")
        if len(BINGO_CARDS) < 1000:
            BINGO_CARDS = generate_default_cards()
            os.makedirs("static", exist_ok=True)
            with open(CARDS_FILE, 'w') as f:
                json.dump(BINGO_CARDS, f)
    else:
        BINGO_CARDS = generate_default_cards()
        os.makedirs("static", exist_ok=True)
        with open(CARDS_FILE, 'w') as f:
            json.dump(BINGO_CARDS, f)
except Exception as e:
    logger.error(f"Error loading cards: {e}")
    BINGO_CARDS = generate_default_cards()

templates = Jinja2Templates(directory="templates")
os.makedirs("static", exist_ok=True)

# ==================== Deposit Handlers (HTML version, robust) ====================
async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} started deposit via /deposit")
    keyboard = [
        [InlineKeyboardButton("📱 Telebirr", callback_data="method_telebirr")],
        [InlineKeyboardButton("💳 CBE Birr", callback_data="method_cbebirr")],
        [InlineKeyboardButton("◀️ Cancel", callback_data="cancel_deposit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "💰 <b>Deposit Menu</b>\n\nChoose your payment method:",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    return SELECT_METHOD

async def deposit_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    logger.info(f"User {user_id} started deposit via button")

    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Callback answer failed: {e}")

    keyboard = [
        [InlineKeyboardButton("📱 Telebirr", callback_data="method_telebirr")],
        [InlineKeyboardButton("💳 CBE Birr", callback_data="method_cbebirr")],
        [InlineKeyboardButton("◀️ Cancel", callback_data="cancel_deposit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(
            "💰 <b>Deposit Menu</b>\n\nChoose your payment method:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to edit message: {e}, sending new")
        await context.bot.send_message(
            chat_id=user_id,
            text="💰 <b>Deposit Menu</b>\n\nChoose your payment method:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    return SELECT_METHOD

async def method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    logger.info(f"Method callback from user {user_id}: {query.data}")

    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Callback query expired: {e}")

    if query.data == "cancel_deposit":
        try:
            await query.delete_message()
        except Exception as e:
            logger.error(f"Failed to delete cancel message: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Deposit cancelled.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Main Menu", callback_data="menu")
            ]])
        )
        return ConversationHandler.END

    # Store payment method
    method = query.data.replace("method_", "")
    context.user_data['payment_method'] = method
    method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])

    # --- Expanded amount buttons ---
    amounts = [50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 1000, 5000, 10000]
    amount_buttons = []
    row = []
    for amt in amounts:
        row.append(InlineKeyboardButton(f"{amt} ETB", callback_data=f"amount_{amt}"))
        if len(row) == 3:
            amount_buttons.append(row)
            row = []
    if row:
        amount_buttons.append(row)
    amount_buttons.append([InlineKeyboardButton("◀️ Cancel", callback_data="cancel_deposit")])

    reply_markup = InlineKeyboardMarkup(amount_buttons)

    try:
        await query.edit_message_text(
            f"💰 <b>{method_info['name']} Deposit</b>\n\n"
            f"<b>Choose an amount:</b>",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Edit failed, sending new: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"💰 <b>{method_info['name']} Deposit</b>\n\n<b>Choose an amount:</b>",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    return SELECT_AMOUNT

async def amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    logger.info(f"Amount callback from user {user_id}: {query.data}")

    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Callback query answer failed: {e}")

    if query.data == "cancel_deposit":
        try:
            await query.delete_message()
        except Exception as e:
            logger.error(f"Failed to delete cancel message: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Deposit cancelled.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Main Menu", callback_data="menu")
            ]])
        )
        return ConversationHandler.END

    # Parse amount
    amount_str = query.data.replace("amount_", "")
    try:
        amount = int(amount_str)
    except ValueError:
        await query.edit_message_text("❌ Invalid amount. Please try again.")
        return SELECT_AMOUNT

    # Validate amount (now up to 10000)
    if amount < 10 or amount > 10000:
        await query.edit_message_text("❌ Amount must be between 10 and 10000 ETB. Please choose again.")
        return SELECT_AMOUNT

    # Retrieve payment method from user_data
    method = context.user_data.get('payment_method')
    if not method:
        logger.warning(f"User {user_id} had no payment_method in user_data – restarting")
        await query.edit_message_text(
            "❌ Session expired. Please start over with /deposit",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Main Menu", callback_data="menu")
            ]])
        )
        return ConversationHandler.END

    # Store amount
    context.user_data['deposit_amount'] = amount
    method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])

    # Delete the old message with buttons (clean up)
    try:
        await query.delete_message()
        logger.info(f"Deleted old amount selection message for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to delete old message: {e}")

    # Send a brand new message with account instructions – using HTML
    text = (f"💰 <b>{method_info['name']} Deposit</b>\n\n"
            f"💵 Amount: <b>{amount} ETB</b>\n"
            f"🏦 Account: <code>{method_info['account']}</code>\n"
            f"Account Name: {method_info['account_name']}\n\n"
            f"<b>Instructions:</b>\n"
            f"{method_info['instructions']}\n\n"
            f"✅ After sending the money, please <b>send the transaction ID</b> here.\n\n"
            f"<i>Example: <code>TRX123456</code></i>")
    await context.bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode='HTML'
    )
    logger.info(f"Sent new instructions for amount {amount} to user {user_id}")

    # Move to waiting for transaction ID
    return WAIT_TRANSACTION

async def transaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    trx_id = update.message.text.strip()
    logger.info(f"Transaction ID from user {user_id}: {trx_id}")

    # Retrieve stored data
    amount = context.user_data.get('deposit_amount')
    method = context.user_data.get('payment_method')

    if not amount or not method:
        await update.message.reply_text(
            "❌ Session expired. Please start over with /deposit"
        )
        return ConversationHandler.END

    method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])

    # Get payment method ID from database
    methods = db.get_payment_methods(type='mobile_money', active_only=True)
    if not methods:
        await update.message.reply_text("❌ No payment methods available")
        return ConversationHandler.END
    method_id = methods[0]['id']

    # Create payment request
    try:
        request_id = db.create_payment_request(
            user_id=user_id,
            method_id=method_id,
            amount=amount * 100,  # convert to cents
            sender_phone=""
        )
    except Exception as e:
        logger.error(f"Database error creating payment request: {e}")
        await update.message.reply_text("❌ Failed to create payment request. Please try again later.")
        return ConversationHandler.END

    if not request_id:
        await update.message.reply_text("❌ Failed to create payment request. Please try again.")
        return ConversationHandler.END

    # Store transaction ID as proof
    try:
        db.add_payment_proof(request_id, 'text', trx_id)
    except Exception as e:
        logger.error(f"Failed to add payment proof: {e}")
        # Non-critical, continue

    # Notify user – using HTML
    await update.message.reply_text(
        f"✅ <b>Payment Report Submitted!</b>\n\n"
        f"💰 <b>Amount:</b> {amount} ETB\n"
        f"💳 <b>Method:</b> {method_info['name']}\n"
        f"🆔 <b>Request ID:</b> <code>{request_id}</code>\n"
        f"🔢 <b>Transaction ID:</b> <code>{trx_id}</code>\n\n"
        f"⏳ Admin will verify your payment shortly.\n"
        f"You'll be notified once your balance is updated.",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Main Menu", callback_data="menu")
        ]])
    )

    # Notify admin with approve/reject buttons
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_payment_{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_payment_{request_id}")
        ]
    ]
    try:
        admin_message = await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"💰 <b>New Payment Request</b>\n\n"
                 f"👤 <b>User:</b> {update.effective_user.first_name}\n"
                 f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
                 f"💰 <b>Amount:</b> {amount} ETB\n"
                 f"💳 <b>Method:</b> {method_info['name']}\n"
                 f"🆔 <b>Request ID:</b> <code>{request_id}</code>\n"
                 f"🔢 <b>Transaction ID:</b> <code>{trx_id}</code>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.bot_data[f"admin_msg_{request_id}"] = {
            'chat_id': ADMIN_USER_ID,
            'message_id': admin_message.message_id
        }
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

    # Clear user_data and end conversation
    context.user_data.clear()
    return ConversationHandler.END

async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"User {update.effective_user.id} cancelled deposit")
    await update.message.reply_text(
        "❌ Deposit cancelled.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ==================== Withdrawal Handlers ====================
async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = db.get_user(user.id) or db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    balance = user_data['balance'] / 100
    await update.message.reply_text(
        f"💸 <b>Withdrawal</b>\n\nYour current balance: <b>{balance:.2f} ETB</b>\n"
        f"Minimum withdrawal: <b>10 ETB</b>\n\nPlease enter the amount you want to withdraw (10-{balance:.2f} ETB):",
        parse_mode='HTML'
    )
    return WITHDRAW_AMOUNT

async def withdraw_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    logger.info(f"User {user.id} started withdrawal via button")

    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Callback answer failed: {e}")

    user_data = db.get_user(user.id) or db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    balance = user_data['balance'] / 100

    try:
        await query.edit_message_text(
            f"💸 <b>Withdrawal</b>\n\nYour current balance: <b>{balance:.2f} ETB</b>\n"
            f"Minimum withdrawal: <b>10 ETB</b>\n\nPlease enter the amount you want to withdraw (10-{balance:.2f} ETB):",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Edit failed, sending new: {e}")
        await context.bot.send_message(
            chat_id=user.id,
            text=f"💸 <b>Withdrawal</b>\n\nYour current balance: <b>{balance:.2f} ETB</b>\n"
                 f"Minimum withdrawal: <b>10 ETB</b>\n\nPlease enter the amount you want to withdraw (10-{balance:.2f} ETB):",
            parse_mode='HTML'
        )
    return WITHDRAW_AMOUNT

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        amount = float(text)
        user_data = db.get_user(user_id)
        balance_cents = user_data['balance']
        balance_etb = balance_cents / 100

        if amount < 10:
            await update.message.reply_text("❌ Minimum withdrawal is 10 ETB. Please enter a valid amount:")
            return WITHDRAW_AMOUNT
        if amount > balance_etb:
            await update.message.reply_text(
                f"❌ Insufficient balance. Your balance is {balance_etb:.2f} ETB.\nPlease enter a lower amount:"
            )
            return WITHDRAW_AMOUNT

        amount_cents = int(amount * 100)
        context.user_data['withdraw_amount'] = amount_cents
        context.user_data['withdraw_amount_etb'] = amount

        await update.message.reply_text(
            "📱 <b>Enter your phone number</b> (the one registered with your mobile money):\nExample: <code>0953933030</code>",
            parse_mode='HTML'
        )
        return WITHDRAW_PHONE
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number:")
        return WITHDRAW_AMOUNT

async def withdraw_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    user = update.effective_user
    amount_cents = context.user_data.get('withdraw_amount')
    amount_etb = context.user_data.get('withdraw_amount_etb')

    if not amount_cents:
        await update.message.reply_text("❌ Session expired. Please start over with /withdraw")
        return ConversationHandler.END

    request_id = db.create_withdrawal_request(user.id, amount_cents, phone)
    if not request_id:
        await update.message.reply_text("❌ Failed to create withdrawal request. Please try again.")
        return ConversationHandler.END

    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_withdraw_{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_withdraw_{request_id}")
        ]
    ]
    try:
        admin_message = await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"💸 <b>New Withdrawal Request</b>\n\n"
                 f"👤 <b>User:</b> {user.first_name}\n"
                 f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
                 f"💰 <b>Amount:</b> {amount_etb:.2f} ETB\n"
                 f"📱 <b>Phone:</b> {phone}\n"
                 f"🆔 <b>Request ID:</b> <code>{request_id}</code>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.bot_data[f"admin_withdraw_msg_{request_id}"] = {
            'chat_id': ADMIN_USER_ID,
            'message_id': admin_message.message_id
        }
    except Exception as e:
        logger.error(f"Failed to notify admin about withdrawal: {e}")

    await update.message.reply_text(
        f"✅ <b>Withdrawal Request Submitted!</b>\n\n"
        f"💰 Amount: {amount_etb:.2f} ETB\n"
        f"📱 Phone: {phone}\n"
        f"🆔 Request ID: <code>{request_id}</code>\n\n"
        f"⏳ Admin will process your request shortly.",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Main Menu", callback_data="menu")
        ]])
    )

    context.user_data.clear()
    return ConversationHandler.END

async def withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Withdrawal cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ==================== Broadcast Command (Admin only) ====================
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to send a message to all users."""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /broadcast <message>\n\nExample: /broadcast New game starting soon!"
        )
        return

    message = ' '.join(args)

    all_users = db.get_all_user_ids()
    if not all_users:
        await update.message.reply_text("No users found.")
        return

    sent = 0
    failed = 0
    for uid in all_users:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>Announcement</b>\n\n{message}",
                parse_mode='HTML'
            )
            sent += 1
            await asyncio.sleep(0.05)  # small delay to avoid hitting rate limits
        except Exception as e:
            logger.error(f"Failed to send broadcast to {uid}: {e}")
            failed += 1

    await update.message.reply_text(f"Broadcast sent to {sent} users. Failed: {failed}")

# ==================== Game Class ====================
class IntegratedBingoGame:
    def __init__(self):
        self.round_number = 1
        self.called_numbers = []
        self.game_started = False
        self.total_pool = 0
        self.house_profit = 0
        self.users = {}
        self.active_games = {}
        self.game_connections = {}
        self.taken_cards = {}
        self.game_winner = {}
        self.number_tasks = {}
        self.countdown_timers = {}
        self.game_locks = {}
        self.bot_app = None
        self.user_connections = {}
        self.MAX_CONNECTIONS_PER_USER = 5
        self.auto_start_timer = None
        self.first_card_time = None
        self.reset_timer = None
        self.stop_number_generation = False
        self.game_id = 1
        self.heartbeat_task = None

    def get_lock(self, game_id: int) -> asyncio.Lock:
        if game_id not in self.game_locks:
            self.game_locks[game_id] = asyncio.Lock()
        return self.game_locks[game_id]

    async def start_heartbeat(self):
        while True:
            await asyncio.sleep(30)
            for game_id, connections in self.game_connections.items():
                for conn in connections:
                    try:
                        await conn.send_json({'type': 'heartbeat'})
                    except:
                        pass

    async def connect(self, game_id: int, websocket: WebSocket, user_id: int):
        if self.user_connections.get(user_id, 0) >= self.MAX_CONNECTIONS_PER_USER:
            logger.warning(f"User {user_id} exceeded max connections ({self.MAX_CONNECTIONS_PER_USER}), rejecting")
            await websocket.close(code=1008, reason="Too many connections")
            return False

        await websocket.accept()
        self.user_connections[user_id] = self.user_connections.get(user_id, 0) + 1
        user = db.get_or_create_user(user_id)

        async with self.get_lock(game_id):
            if game_id not in self.game_connections:
                self.game_connections[game_id] = []
                self.taken_cards[game_id] = set()
                self.game_winner[game_id] = None
                self.countdown_timers[game_id] = 15
                self.active_games[game_id] = {
                    'called_numbers': [],
                    'players': {},
                    'prize_pool': 0,
                    'total_cards_sold': 0,
                    'last_winner': None,
                }

            self.game_connections[game_id].append(websocket)
            if user_id not in self.active_games[game_id]['players']:
                self.active_games[game_id]['players'][user_id] = {
                    'name': user.get('first_name', f"Player{user_id}"),
                    'cards': [],
                    'card_ids': [],
                    'marked': {},
                    'ready': False,
                    'winner': False,
                    'balance': user['balance']
                }

            active_games_count = db.get_active_games_count(user_id)
            total_stake = db.get_total_stake(user_id)

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

            asyncio.create_task(self.update_countdown(game_id))
            await self.broadcast(game_id, {'type': 'player_joined', 'players': self.get_players(game_id)})

        return True

    async def disconnect(self, game_id: int, websocket: WebSocket, user_id: int):
        async with self.get_lock(game_id):
            if game_id in self.game_connections and websocket in self.game_connections[game_id]:
                self.game_connections[game_id].remove(websocket)
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
        return [
            {'id': uid, 'name': data['name'], 'card_count': len(data['card_ids']), 'ready': data['ready'], 'winner': data['winner']}
            for uid, data in self.active_games[game_id]['players'].items()
        ]

    async def update_countdown(self, game_id: int):
        try:
            while game_id in self.active_games:
                await asyncio.sleep(1)
                if game_id in self.countdown_timers:
                    if self.countdown_timers[game_id] > 0:
                        self.countdown_timers[game_id] -= 1
                        await self.broadcast(game_id, {'type': 'countdown', 'time': self.countdown_timers[game_id]})
                    if self.countdown_timers[game_id] <= 0 and self.game_started:
                        self.countdown_timers[game_id] = 15
        except:
            pass

    async def select_cards(self, game_id: int, user_id: int, card_ids: List[int]):
        async with self.get_lock(game_id):
            if game_id not in self.active_games:
                return False, "Game not found", 0, None
            if self.game_started:
                return False, "Game already started", 0, None
            if user_id not in self.active_games[game_id]['players']:
                return False, "Player not found", 0, None

            player = self.active_games[game_id]['players'][user_id]
            if len(player['card_ids']) + len(card_ids) > MAX_CARDS_PER_PLAYER:
                return False, f"Maximum {MAX_CARDS_PER_PLAYER} cards per player", 0, None

            for card_id in card_ids:
                if card_id in self.taken_cards[game_id]:
                    return False, f"Card {card_id} already taken", 0, None
                if not next((c for c in BINGO_CARDS if c['id'] == card_id), None):
                    return False, f"Card {card_id} not found", 0, None

            total_cost = len(card_ids) * CARD_PRICE
            user = db.get_user(user_id)
            if not user or user['balance'] < total_cost:
                return False, f"Insufficient balance. Need {total_cost/100} ETB", total_cost, None

            was_empty = all(len(p['card_ids']) == 0 for p in self.active_games[game_id]['players'].values())

            update_result = db.update_balance(user_id, -total_cost, 'game_fee', f'Selected cards for game #{game_id}')
            if not update_result:
                return False, "Failed to deduct balance", total_cost, None
            new_balance = update_result['new_balance']

            for card_id in card_ids:
                self.taken_cards[game_id].add(card_id)
                card_data = next(c for c in BINGO_CARDS if c['id'] == card_id)
                player['cards'].append(card_data['card'])
                player['card_ids'].append(card_id)
                player['marked'][card_id] = []
                player['balance'] = new_balance
                player['ready'] = True

            self.active_games[game_id]['total_cards_sold'] += len(card_ids)
            self.active_games[game_id]['prize_pool'] = self.active_games[game_id]['total_cards_sold'] * CARD_PRICE

            if was_empty and not self.game_started:
                asyncio.create_task(self.start_auto_start_timer(game_id))

            await self.broadcast(game_id, {'type': 'player_ready', 'players': self.get_players(game_id), 'user_id': user_id})

            return True, f"Selected {len(card_ids)} cards", total_cost, new_balance

    async def start_auto_start_timer(self, game_id: int):
        if self.auto_start_timer:
            self.auto_start_timer.cancel()
        self.first_card_time = time.time()

        async def auto_start():
            await asyncio.sleep(AUTO_START_DELAY)
            async with self.get_lock(game_id):
                if not self.game_started and game_id in self.active_games and self.active_games[game_id]['players']:
                    await self.start_round(game_id)
        self.auto_start_timer = asyncio.create_task(auto_start())

    async def start_round(self, game_id: int = 1):
        if self.game_started or game_id not in self.active_games or self.active_games[game_id]['total_cards_sold'] == 0:
            return
        self.game_started = True
        self.stop_number_generation = False
        if self.auto_start_timer:
            self.auto_start_timer.cancel()
            self.auto_start_timer = None
        await self.broadcast(game_id, {'type': 'game_started', 'round': self.round_number})
        asyncio.create_task(self.draw_numbers(game_id))

    async def check_winner_any_line(self, game_id: int, last_number: int) -> List[Tuple[int, int]]:
        """Check if anyone won by completing a row, column, or diagonal.
           Returns a list of (user_id, card_id) for all winners on this number."""
        if game_id not in self.active_games:
            return []

        called = set(self.active_games[game_id]['called_numbers'])
        winners = []

        for user_id, player in self.active_games[game_id]['players'].items():
            if player['winner']:
                continue

            for card_idx, card in enumerate(player['cards']):
                card_id = player['card_ids'][card_idx]
                marked = set(player['marked'].get(card_id, []))

                def is_marked(val):
                    return val == 'FREE' or val in called or val in marked

                # Check rows
                for row in range(5):
                    if all(is_marked(card[col][row]) for col in range(5)):
                        logger.info(f"ROW BINGO! User {user_id} with card {card_id} at number {last_number}")
                        winners.append((user_id, card_id))
                        break

                # Check columns (if not already a winner from a row)
                if not any(w[0] == user_id and w[1] == card_id for w in winners):
                    for col in range(5):
                        if all(is_marked(card[col][row]) for row in range(5)):
                            logger.info(f"COLUMN BINGO! User {user_id} with card {card_id} at number {last_number}")
                            winners.append((user_id, card_id))
                            break

                # Check main diagonal
                if not any(w[0] == user_id and w[1] == card_id for w in winners):
                    if all(is_marked(card[i][i]) for i in range(5)):
                        logger.info(f"DIAGONAL BINGO (main)! User {user_id} with card {card_id} at number {last_number}")
                        winners.append((user_id, card_id))

                # Check anti-diagonal
                if not any(w[0] == user_id and w[1] == card_id for w in winners):
                    if all(is_marked(card[i][4-i]) for i in range(5)):
                        logger.info(f"DIAGONAL BINGO (anti)! User {user_id} with card {card_id} at number {last_number}")
                        winners.append((user_id, card_id))

        return list(set(winners))

    def mark_number(self, game_id: int, user_id: int, card_id: int, number: int):
        if game_id not in self.active_games or not self.game_started or self.game_winner.get(game_id):
            return False
        player = self.active_games[game_id]['players'].get(user_id)
        if not player or card_id not in player['marked'] or number in player['marked'][card_id]:
            return False
        player['marked'][card_id].append(number)
        return True

    async def draw_numbers(self, game_id: int = 1):
        numbers = list(range(1, 76))
        random.shuffle(numbers)

        for n in numbers:
            if self.stop_number_generation or self.game_winner.get(game_id):
                break

            await asyncio.sleep(3)

            if self.stop_number_generation or self.game_winner.get(game_id):
                break

            async with self.get_lock(game_id):
                if self.stop_number_generation or self.game_winner.get(game_id) or not self.game_started:
                    break

                self.called_numbers.append(n)
                self.active_games[game_id]['called_numbers'].append(n)
                await self.broadcast(game_id, {
                    'type': 'number_called',
                    'number': n,
                    'called': self.active_games[game_id]['called_numbers']
                })

                winners = await self.check_winner_any_line(game_id, n)
                if winners:
                    logger.info(f"🏆 WINNERS FOUND on number {n}: {winners}")
                    self.stop_number_generation = True
                    self.game_winner[game_id] = [w[0] for w in winners]
                    await self.finish_round_multi(game_id, winners)
                    break

    async def finish_round_multi(self, game_id: int, winners: List[Tuple[int, int]]):
        if game_id not in self.active_games:
            return

        self.stop_number_generation = True
        winner_ids = [w[0] for w in winners]
        logger.info(f"Finishing round {game_id} - Winners: {winner_ids}")

        prize_pool = self.active_games[game_id]['prize_pool']
        house_cut = prize_pool * HOUSE_PERCENT
        total_prize = prize_pool - house_cut
        prize_per_winner = total_prize // len(winners)
        remainder = total_prize - (prize_per_winner * len(winners))
        house_cut += remainder
        self.house_profit += house_cut

        for user_id, winning_card_id in winners:
            if user_id in self.active_games[game_id]['players']:
                player = self.active_games[game_id]['players'][user_id]
                update_result = db.update_balance(
                    user_id=user_id,
                    amount=prize_per_winner,
                    transaction_type='game_win',
                    description=f'Won round {self.round_number} in game #{game_id} (shared win)'
                )
                if update_result:
                    player['balance'] = update_result['new_balance']
                    player['winner'] = True

        winning_card_data = None
        if winners:
            first_winner_id, first_card_id = winners[0]
            card = next((c for c in BINGO_CARDS if c['id'] == first_card_id), None)
            if card:
                winning_card_data = card['card']

        await self.broadcast(game_id, {
            'type': 'game_won',
            'winners': [
                {
                    'user_id': uid,
                    'name': self.active_games[game_id]['players'][uid]['name'],
                    'card_id': cid
                } for uid, cid in winners
            ],
            'prize_per_winner': prize_per_winner / 100,
            'total_prize': total_prize / 100,
            'house_fee': house_cut / 100,
            'winning_card': winning_card_data,
            'winning_card_id': winners[0][1] if winners else None
        })

        if self.bot_app:
            for user_id, _ in winners:
                try:
                    await self.bot_app.bot.send_message(
                        chat_id=user_id,
                        text=f"🎉 CONGRATULATIONS! 🎉\n\nYou won round {self.round_number}!\nYour share: {prize_per_winner/100} ETB"
                    )
                except:
                    pass

        if self.reset_timer:
            self.reset_timer.cancel()
        self.reset_timer = asyncio.create_task(self.delayed_reset(game_id))

    async def delayed_reset(self, game_id: int):
        await asyncio.sleep(ROUND_RESET_DELAY)
        await self.reset_round(game_id)

    async def reset_round(self, game_id: int = 1):
        self.round_number += 1
        self.called_numbers = []
        self.game_started = False
        self.stop_number_generation = False
        self.auto_start_timer = None
        self.first_card_time = None
        self.reset_timer = None
        self.game_winner[game_id] = None

        if game_id in self.active_games:
            self.active_games[game_id]['called_numbers'] = []
            self.active_games[game_id]['prize_pool'] = 0
            self.active_games[game_id]['total_cards_sold'] = 0
            for player in self.active_games[game_id]['players'].values():
                player['cards'] = []
                player['card_ids'] = []
                player['marked'] = {}
                player['ready'] = False
                player['winner'] = False

        self.taken_cards[game_id] = set()
        logger.info(f"✅ Round {self.round_number} ready - all cards unlocked")
        await self.broadcast(game_id, {
            'type': 'game_reset',
            'round': self.round_number,
            'players': self.get_players(game_id),
            'countdown': 15
        })

game_manager = IntegratedBingoGame()

# ==================== Conversation Handlers ====================
deposit_conv = ConversationHandler(
    entry_points=[
        CommandHandler('deposit', deposit_command),
        CallbackQueryHandler(deposit_start_callback, pattern='^deposit_start$')
    ],
    states={
        SELECT_METHOD: [
            CallbackQueryHandler(method_callback, pattern='^(method_|cancel_deposit)'),
        ],
        SELECT_AMOUNT: [
            CallbackQueryHandler(amount_callback, pattern='^(amount_|cancel_deposit)'),
        ],
        WAIT_TRANSACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, transaction_handler)],
    },
    fallbacks=[CommandHandler('cancel', deposit_cancel)],
    name="deposit_conversation",
    allow_reentry=True,
    per_message=False
)

withdraw_conv = ConversationHandler(
    entry_points=[
        CommandHandler('withdraw', withdraw_command),
        CallbackQueryHandler(withdraw_start_callback, pattern='^withdraw_start$')
    ],
    states={
        WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
        WITHDRAW_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_phone)],
    },
    fallbacks=[CommandHandler('cancel', withdraw_cancel)],
    name="withdraw_conversation",
    allow_reentry=True,
    per_message=False
)

# ==================== Menu and Navigation Handlers ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    balance = user_data['balance'] / 100
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("💳 Deposit", callback_data="deposit_start")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_start")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    if str(user.id) == str(ADMIN_USER_ID):
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])
    await update.message.reply_text(
        f"🎯 Welcome, {user.first_name}!\n💰 Balance: {balance:.2f} ETB\n\nChoose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Callback query expired: {e}")

    user = update.effective_user
    data = query.data

    if data == "play":
        user_data = db.get_user(user.id)
        if not user_data or user_data['balance'] < CARD_PRICE:
            await query.edit_message_text(
                f"❌ Insufficient balance. Need {CARD_PRICE/100} ETB minimum.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💳 Deposit", callback_data="deposit_start"),
                    InlineKeyboardButton("◀️ Back", callback_data="menu")
                ]])
            )
            return
        webapp_url = f"{BASE_URL}/game?user_id={user.id}&game_id=1"
        await query.edit_message_text(
            f"🎮 Click to open game\n\n⏱️ Auto-starts {AUTO_START_DELAY}s after first card!\n\n💰 Balance: {user_data['balance']/100:.2f} ETB",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Open Game", web_app={'url': webapp_url})
            ]])
        )
    elif data == "balance":
        user_data = db.get_user(user.id)
        balance = user_data['balance'] / 100 if user_data else 0
        active_games = db.get_active_games_count(user.id)
        total_stake = db.get_total_stake(user.id) / 100
        await query.edit_message_text(
            f"💰 Your Balance\n\nCurrent: {balance:.2f} ETB\nActive Games: {active_games}\nTotal Stake: {total_stake:.2f} ETB",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Deposit", callback_data="deposit_start"),
                InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_start"),
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    elif data == "help":
        help_text = (
            "❓ Bingo Bot Help\n\nHow to Play:\n1. Click 'Play Bingo'\n2. Choose cards (1-1000)\n3. Game auto-starts 30s after first card\n4. Numbers called every 3 seconds\n5. Complete ONE LINE (row, column, or diagonal) to win!\n6. If multiple players win on the same number, the prize is split equally!\n\n"
            f"Price: {CARD_PRICE/100} ETB per card\n\nDeposit:\n• Tap 'Deposit' button\n• Choose Telebirr or CBE Birr\n• Choose an amount (50–10000 ETB)\n• Send the money and provide the transaction ID\n\nWithdraw:\n• Tap 'Withdraw' button\n• Enter amount and phone number\n• Admin will approve and send money"
        )
        await query.edit_message_text(
            help_text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    elif data == "admin" and str(user.id) == str(ADMIN_USER_ID):
        stats = db.get_system_stats()
        pending_payments = len(db.get_pending_payment_requests(limit=100))
        pending_withdrawals = len(db.get_pending_withdrawal_requests(limit=100))
        await query.edit_message_text(
            f"👑 Admin Panel\n\nUsers: {stats['total_users']}\nTotal Balance: {stats['total_balance']/100:.2f} ETB\nPending Payments: {pending_payments}\nPending Withdrawals: {pending_withdrawals}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 View Pending Payments", callback_data="admin_pending_payments")],
                [InlineKeyboardButton("💸 View Pending Withdrawals", callback_data="admin_pending_withdrawals")],
                [InlineKeyboardButton("◀️ Back", callback_data="menu")]
            ])
        )
    elif data == "admin_pending_payments" and str(user.id) == str(ADMIN_USER_ID):
        pending = db.get_pending_payment_requests(limit=10)
        if not pending:
            await query.edit_message_text("📊 No pending payments.", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="admin")
            ]]))
            return
        text = "📊 Pending Payments:\n\n" + "\n\n".join(
            f"🆔 {p['request_id']}\n👤 {p['first_name']} (@{p.get('username','N/A')})\n💰 {p['amount']/100:.2f} ETB"
            for p in pending
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="admin")
        ]]))
    elif data == "admin_pending_withdrawals" and str(user.id) == str(ADMIN_USER_ID):
        pending = db.get_pending_withdrawal_requests(limit=10)
        if not pending:
            await query.edit_message_text("📊 No pending withdrawals.", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="admin")
            ]]))
            return
        text = "💸 Pending Withdrawals:\n\n" + "\n\n".join(
            f"🆔 {w['request_id']}\n👤 {w['first_name']} (@{w.get('username','N/A')})\n💰 {w['amount']/100:.2f} ETB\n📱 {w['phone_number']}"
            for w in pending
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="admin")
        ]]))
    elif data == "menu":
        user_data = db.get_user(user.id)
        balance = user_data['balance'] / 100 if user_data else 0
        keyboard = [
            [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
            [InlineKeyboardButton("💰 Balance", callback_data="balance")],
            [InlineKeyboardButton("💳 Deposit", callback_data="deposit_start")],
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_start")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        if str(user.id) == str(ADMIN_USER_ID):
            keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])
        await query.edit_message_text(
            f"🎯 Main Menu\n💰 Balance: {balance:.2f} ETB",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ==================== Approval Handlers ====================
async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_payment_request(request_id)
    if not request:
        await query.edit_message_text("❌ Payment request not found")
        return
    db.update_payment_request_status(request_id, 'completed', 'Approved by admin')
    result = db.update_balance(request['user_id'], request['amount'], 'deposit', f'Payment approved - {request_id}')
    if result:
        await context.bot.send_message(
            request['user_id'],
            f"✅ Payment Approved!\n\nYour payment of {request['amount']/100:.2f} ETB has been approved.\nNew balance: {result['new_balance']/100:.2f} ETB"
        )
        admin_msg = context.bot_data.get(f"admin_msg_{request_id}")
        if admin_msg:
            try:
                await context.bot.edit_message_reply_markup(admin_msg['chat_id'], admin_msg['message_id'], reply_markup=None)
            except Exception as e:
                logger.error(f"Failed to remove admin buttons: {e}")
        await query.edit_message_text(f"✅ Payment Approved\n\nRequest ID: {request_id}\nAmount: {request['amount']/100:.2f} ETB")
    else:
        await query.edit_message_text("❌ Failed to update balance")

async def reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_payment_request(request_id)
    if not request:
        await query.edit_message_text("❌ Payment request not found")
        return
    db.update_payment_request_status(request_id, 'rejected', 'Rejected by admin')
    await context.bot.send_message(request['user_id'], f"❌ Payment Rejected\n\nYour payment of {request['amount']/100:.2f} ETB has been rejected.\nPlease contact admin.")
    admin_msg = context.bot_data.get(f"admin_msg_{request_id}")
    if admin_msg:
        try:
            await context.bot.edit_message_reply_markup(admin_msg['chat_id'], admin_msg['message_id'], reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to remove admin buttons: {e}")
    await query.edit_message_text(f"❌ Payment Rejected\n\nRequest ID: {request_id}")

async def approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_withdrawal_request(request_id)
    if not request:
        await query.edit_message_text("❌ Withdrawal request not found")
        return
    user = db.get_user(request['user_id'])
    if not user or user['balance'] < request['amount']:
        await query.edit_message_text("❌ Insufficient balance for this withdrawal")
        return
    db.update_balance(request['user_id'], -request['amount'], 'withdrawal', f'Withdrawal approved - {request_id}')
    db.update_withdrawal_request_status(request_id, 'completed', 'Approved by admin')
    await context.bot.send_message(
        request['user_id'],
        f"✅ Withdrawal Approved!\n\nAmount: {request['amount']/100:.2f} ETB\nPhone: {request['phone_number']}\nYour money will be sent shortly."
    )
    admin_msg = context.bot_data.get(f"admin_withdraw_msg_{request_id}")
    if admin_msg:
        try:
            await context.bot.edit_message_reply_markup(admin_msg['chat_id'], admin_msg['message_id'], reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to remove admin buttons: {e}")
    await query.edit_message_text(f"✅ Withdrawal Approved\n\nRequest ID: {request_id}\nAmount: {request['amount']/100:.2f} ETB\nPhone: {request['phone_number']}")

async def reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_withdrawal_request(request_id)
    if not request:
        await query.edit_message_text("❌ Withdrawal request not found")
        return
    db.update_withdrawal_request_status(request_id, 'rejected', 'Rejected by admin')
    await context.bot.send_message(request['user_id'], f"❌ Withdrawal Rejected\n\nYour withdrawal request for {request['amount']/100:.2f} ETB has been rejected.\nPlease contact admin.")
    admin_msg = context.bot_data.get(f"admin_withdraw_msg_{request_id}")
    if admin_msg:
        try:
            await context.bot.edit_message_reply_markup(admin_msg['chat_id'], admin_msg['message_id'], reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to remove admin buttons: {e}")
    await query.edit_message_text(f"❌ Withdrawal Rejected\n\nRequest ID: {request_id}")

# ==================== Main Callback Router ====================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith('approve_payment_'):
        request_id = data.split('_')[2]
        await approve_payment(update, context, request_id)
    elif data.startswith('reject_payment_'):
        request_id = data.split('_')[2]
        await reject_payment(update, context, request_id)
    elif data.startswith('approve_withdraw_'):
        request_id = data.split('_')[2]
        await approve_withdrawal(update, context, request_id)
    elif data.startswith('reject_withdraw_'):
        request_id = data.split('_')[2]
        await reject_withdrawal(update, context, request_id)
    else:
        await menu_callback(update, context)

# ==================== Bot Setup ====================
async def setup_bot():
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    application.add_handler(deposit_conv)
    application.add_handler(withdraw_conv)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cancel", deposit_cancel))
    application.add_handler(CommandHandler("broadcast", broadcast_command))  # <-- added
    application.add_handler(CallbackQueryHandler(button_callback))
    await application.initialize()
    await application.start()
    webhook_url = f"{BASE_URL}/webhook"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"🤖 Webhook set to {webhook_url}")
    return application

# ==================== Lifespan ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting up...")
    game_manager.bot_app = await setup_bot()
    asyncio.create_task(game_manager.start_heartbeat())
    yield
    logger.info("🛑 Shutting down...")
    for connections in game_manager.game_connections.values():
        for conn in connections:
            try:
                await conn.close()
            except:
                pass
    if game_manager.bot_app:
        await game_manager.bot_app.stop()
        await game_manager.bot_app.shutdown()

app = FastAPI(title="Bingo Game", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== Endpoints ====================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, game_manager.bot_app.bot)
    await game_manager.bot_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "online", "cards": len(BINGO_CARDS), "price_per_card": CARD_PRICE / 100, "max_cards_per_player": MAX_CARDS_PER_PLAYER}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/user/{user_id}")
async def get_user_info(user_id: int):
    try:
        user = db.get_user(user_id) or db.get_or_create_user(user_id)
        return {
            "user_id": user_id,
            "balance": user['balance'],
            "balance_etb": user['balance'] / 100,
            "active_games": db.get_active_games_count(user_id),
            "total_stake": db.get_total_stake(user_id) / 100,
            "games_played": user['games_played'],
            "games_won": user['games_won']
        }
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

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

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    connected = await game_manager.connect(game_id, websocket, user_id)
    if not connected:
        return
    try:
        while True:
            data = await websocket.receive_json()
            logger.info(f"WebSocket message: {data['type']} from user {user_id}")
            if data['type'] == 'select_cards':
                success, msg, cost, new_bal = await game_manager.select_cards(game_id, user_id, data['card_ids'])
                await websocket.send_json({'type': 'cards_selected', 'success': success, 'message': msg, 'cost': cost, 'new_balance': new_bal, 'card_ids': data['card_ids'] if success else []})
                if success:
                    for card_id in data['card_ids']:
                        card = next(c for c in BINGO_CARDS if c['id'] == card_id)
                        await websocket.send_json({'type': 'your_card', 'card': card['card'], 'card_id': card_id})
            elif data['type'] == 'mark_number':
                if not game_manager.game_started:
                    await websocket.send_json({'type': 'error', 'message': 'Game not started'})
                    continue
                success = game_manager.mark_number(game_id, user_id, data['card_id'], data['number'])
                if success:
                    await websocket.send_json({'type': 'number_marked', 'card_id': data['card_id'], 'number': data['number']})
            elif data['type'] == 'claim_bingo':
                card_id = data.get('card_id')
                if card_id:
                    last = game_manager.active_games[game_id]['called_numbers'][-1] if game_manager.active_games[game_id]['called_numbers'] else 0
                    winners = await game_manager.check_winner_any_line(game_id, last)
                    if winners:
                        if any(uid == user_id for uid, _ in winners):
                            game_manager.stop_number_generation = True
                            await game_manager.finish_round_multi(game_id, winners)
                        else:
                            await websocket.send_json({'type': 'error', 'message': 'Not a valid bingo'})
                    else:
                        await websocket.send_json({'type': 'error', 'message': 'No bingo found'})
            elif data['type'] == 'heartbeat':
                await websocket.send_json({'type': 'heartbeat_ack'})
            elif data['type'] == 'ping':
                await websocket.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        await game_manager.disconnect(game_id, websocket, user_id)
        logger.info(f"User {user_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await game_manager.disconnect(game_id, websocket, user_id)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)