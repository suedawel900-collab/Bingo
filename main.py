import os
import json
import random
import asyncio
import logging
import time
import uuid
import re
import aiohttp
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

BOT_USERNAME = os.getenv('BOT_USERNAME', 'MK_BINGO_bot')
RAILWAY_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'bingo-production-a078.up.railway.app')

if not RAILWAY_URL.startswith('http'):
    BASE_URL = f"https://{RAILWAY_URL}"
else:
    BASE_URL = RAILWAY_URL

# Card prices (in cents)
CARD_PRICE_ROOM1 = 1000        # 10.00 ETB for room 1
CARD_PRICE_ROOM2 = 10000       # 100.00 ETB for room 2
CARD_PRICE_ROOM3 = 2000        # 20.00 ETB for room 3
MAX_CARDS_PER_PLAYER = 20
WELCOME_BONUS = 1000
AUTO_START_DELAY = 30
HOUSE_PERCENT = 0.20
ROUND_RESET_DELAY = 10

# ==================== BINGO PATTERNS ====================
PATTERNS = [
    "ONE_LINE", "TWO_LINES", "THREE_LINES", "FOUR_LINES",
    "FULL_HOUSE", "FOUR_CORNERS", "CENTER",
    "X_PATTERN", "PLUS_PATTERN", "TOP_ROW", "BOTTOM_ROW",
    "LEFT_COLUMN", "RIGHT_COLUMN", "DIAGONAL_MAIN", "DIAGONAL_SECOND",
    "T_SHAPE", "L_SHAPE", "CROSS", "BOX", "OUTER_FRAME"
]

# Payment methods with auto-approval settings
PAYMENT_METHODS = {
    "telebirr": {
        "name": "Telebirr",
        "account": "0982372677",
        "account_name": "Bingo Bot",
        "instructions": (
            "Dial *127# and send money to 0982372677\n\n"
            "📱 **የደረሰኝ ማረጋገጫ መስመር ላይ:**\n"
            "ክፍያዎን ከፍለው ከጨረሱ በኋላ የደረሰኝ ቁጥርዎን በመጠቀም ከዚህ ሊንክ ማረጋገጥ ይችላሉ፦\n"
            "`https://transactioninfo.ethiotelecom.et/receipt/{የደረሰኝ_ቁጥር}`\n\n"
            "ለምሳሌ: `https://transactioninfo.ethiotelecom.et/receipt/TRX123456`"
        ),
        "auto_approve": True,
        "receipt_pattern": r'^[A-Z0-9]{6,30}$'
    },
    "cbebirr": {
        "name": "CBE Birr",
        "account": "0982372677",
        "account_name": "Bingo Bot",
        "instructions": "Dial *847# and send money to 0982372677",
        "auto_approve": False,
        "receipt_pattern": r'^[A-Z0-9]{6,30}$'
    }
}

# Auto-approval settings
MIN_AMOUNT_FOR_AUTO_APPROVE = 10
YOUR_TELEBIRR_NUMBER = "0982372677"

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

# ==================== AUTO-APPROVAL FUNCTIONS ====================

async def verify_telebirr_transaction(transaction_id: str, amount: int) -> Tuple[bool, str]:
    """
    Real Telebirr verification using receipt scraping
    Falls back to format validation if the site is unreachable
    """
    
    transaction_id = transaction_id.strip().upper()
    
    # =========================
    # FORMAT VALIDATION (Always performed)
    # =========================
    if not re.match(r'^[A-Z0-9]{6,30}$', transaction_id):
        return False, "❌ የደረሰኝ ቁጥር ትክክል አይደለም (ከ6-30 ፊደል እና ቁጥር ብቻ)"

    if len(set(transaction_id)) == 1:
        return False, "❌ የደረሰኝ ቁጥር የተሳሳተ ነው (ተደጋጋሚ ፊደሎች)"
    
    common_fakes = ["TEST", "DEMO", "SAMPLE", "FAKE", "TRX"]
    if any(fake in transaction_id for fake in common_fakes):
        return False, "❌ የደረሰኝ ቁጥር የተሳሳተ ነው"
    
    # =========================
    # CHECK DUPLICATE RECEIPT (Always performed)
    # =========================
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM payment_proofs WHERE proof_data = ? AND proof_type = 'telebirr_receipt'",
            (transaction_id,)
        )
        if cursor.fetchone():
            return False, "❌ ይህ የደረሰኝ ቁጥር ተጠቅመዋል"
    finally:
        conn.close()
    
    # =========================
    # FETCH RECEIPT PAGE (with timeout and fallback)
    # =========================
    receipt_url = f"https://transactioninfo.ethiotelecom.et/receipt/{transaction_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(receipt_url, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    logger.warning(f"Receipt page returned status {response.status} for {transaction_id}")
                    return True, "✅ ክፍያ በመሠረታዊ ማረጋገጫ ተረጋግጧል (የድረ-ገጽ ችግር)"
                
                html = await response.text()
                
    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching receipt {transaction_id} - falling back to format validation")
        return True, "✅ ክፍያ በመሠረታዊ ማረጋገጫ ተረጋግጧል (የድረ-ገጽ ችግር)"
    except Exception as e:
        logger.error(f"Receipt fetch error for {transaction_id}: {e}")
        return True, "✅ ክፍያ በመሠረታዊ ማረጋገጫ ተረጋግጧል (የድረ-ገጽ ችግር)"

    html_lower = html.lower()
    logger.info(f"Receipt page fetched successfully for {transaction_id}, length: {len(html)}")
    
    success_indicators = [
        "success", "completed", "successful", "ስኬት", "ተሳክቷል",
        "status: completed", "transaction successful",
        "payment successful", "ክፍያ ተሳክቷል"
    ]
    
    found_success = False
    for indicator in success_indicators:
        if indicator in html_lower:
            found_success = True
            logger.info(f"Found success indicator: {indicator}")
            break
    
    if not found_success:
        failure_indicators = ["failed", "cancelled", "error", "አልተሳካም", "ተሰርዟል"]
        for indicator in failure_indicators:
            if indicator in html_lower:
                return False, f"❌ ክፍያው {indicator} ነው"
        
        if "receipt" not in html_lower and "ደረሰኝ" not in html_lower:
            logger.warning(f"No receipt found in page for {transaction_id}")
            return True, "✅ ክፍያ በመሠረታዊ ማረጋገጫ ተረጋግጧል"
    
    YOUR_TELEBIRR_NUMBER = "0982372677"
    
    number_variations = [
        YOUR_TELEBIRR_NUMBER,
        YOUR_TELEBIRR_NUMBER.replace("0", ""),
        YOUR_TELEBIRR_NUMBER[1:],
        f"0{int(YOUR_TELEBIRR_NUMBER)}",
        f"251{ YOUR_TELEBIRR_NUMBER[1:]}"
    ]
    
    receiver_found = False
    for variation in number_variations:
        if variation in html.replace(" ", "").replace("-", "").replace("+", ""):
            receiver_found = True
            logger.info(f"Found receiver number: {variation}")
            break
    
    if not receiver_found:
        logger.warning(f"Receiver number not found for {transaction_id}")
        return False, "❌ ክፍያው ወደ ትክክለኛ አካውንት አልተላከም"
    
    amount_str = str(amount)
    amount_patterns = [
        f"{amount}.00",
        f"{amount}",
        f"{amount:,}",
        f"{amount} ETB",
        f"{amount} ብር",
        f"ETB {amount}",
        f"{amount}birr",
        f"{amount} br"
    ]
    
    amount_found = False
    for pattern in amount_patterns:
        if pattern in html:
            amount_found = True
            logger.info(f"Found amount pattern: {pattern}")
            break
    
    if not amount_found:
        decimal_variations = [
            f"{amount}.0",
            f"{amount}.00",
            f"{amount:,.2f}",
            f"{amount:,}"
        ]
        for variation in decimal_variations:
            if variation in html:
                amount_found = True
                logger.info(f"Found decimal amount: {variation}")
                break
    
    if not amount_found:
        logger.warning(f"Amount {amount} not found in receipt for {transaction_id}")
        return False, "❌ የክፍያ መጠን አይዛመድም"
    
    return True, "✅ ክፍያ ተረጋገጠ"

async def auto_approve_payment(user_id: int, amount: int, transaction_id: str, method: str) -> Tuple[bool, str, Optional[Dict]]:
    """
    Automatically approve a payment with real verification
    """
    logger.info(f"Auto-approving payment for user {user_id}, amount {amount}, method {method}")
    
    if method == "telebirr":
        verified, message = await verify_telebirr_transaction(transaction_id, amount)
        if not verified:
            logger.warning(f"Auto-approval failed for user {user_id}: {message}")
            return False, message, None
        
        logger.info(f"Transaction {transaction_id} verified successfully")
    else:
        return False, "ራስ-ሰር ማረጋገጫ ለዚህ ዘዴ አይገኝም", None
    
    request_id = db.create_payment_request(
        user_id=user_id,
        method_id=1,
        amount=amount * 100,
        sender_phone=""
    )
    
    if not request_id:
        return False, "የክፍያ ጥያቄ መፍጠር አልተሳካም", None
    
    db.add_payment_proof(request_id, 'telebirr_receipt', transaction_id)
    db.update_payment_request_status(request_id, 'auto_approved', 'Auto-approved by system via receipt verification')
    
    result = db.update_balance(
        user_id=user_id,
        amount=amount * 100,
        transaction_type='deposit',
        description=f'Auto-approved payment - {request_id} (Receipt: {transaction_id})'
    )
    
    if not result:
        return False, "ቀሪ ሂሳብ ማዘመን አልተሳካም", None
    
    return True, f"የ{amount} ብር ክፍያዎ በራስ-ሰር ጸድቋል!", result

# ==================== WITHDRAWAL ELIGIBILITY CHECK ====================

async def check_withdrawal_eligibility(user_id: int) -> Tuple[bool, str]:
    """Check if user is eligible to withdraw (must have deposited at least 100 ETB first)"""
    user = db.get_user(user_id)
    if not user:
        return False, "❌ ተጠቃሚ አልተገኘም።"
    
    if not user.get('has_deposited', False):
        return False, "❌ ማውጣት ከመጀመርዎ በፊት ቢያንስ 100 ብር መሙላት አለብዎት።\n\n💡 መጀመሪያ ገንዘብ ይሙሉ እና ከዚያ ማውጣት ይችላሉ።"
    
    total_deposits = user.get('total_deposits', 0) / 100
    if total_deposits < 100:
        return False, f"❌ ማውጣት ከመጀመርዎ በፊት ቢያንስ 100 ብር መሙላት አለብዎት።\nእስካሁን ያስገቡት: {total_deposits:.2f} ብር"
    
    return True, "✅ ማውጣት ይችላሉ"

# ==================== PATTERN CHECKING FUNCTION ====================

def check_pattern(marked_positions, pattern_name):
    """
    Check if marked positions satisfy the required pattern
    marked_positions: list of (row, col) tuples that are marked
    pattern_name: string from PATTERNS list
    """
    size = 5
    p = pattern_name
    
    # Create 5x5 boolean grid
    marked = [[False]*size for _ in range(size)]
    for r, c in marked_positions:
        if 0 <= r < size and 0 <= c < size:
            marked[r][c] = True
    
    # ONE LINE
    if p == "ONE_LINE":
        return any(all(marked[r][c] for c in range(size)) for r in range(size)) or \
               any(all(marked[r][c] for r in range(size)) for c in range(size))
    
    # TWO LINES
    if p == "TWO_LINES":
        lines = 0
        for r in range(size):
            if all(marked[r][c] for c in range(size)):
                lines += 1
        for c in range(size):
            if all(marked[r][c] for r in range(size)):
                lines += 1
        return lines >= 2
    
    # THREE LINES
    if p == "THREE_LINES":
        lines = 0
        for r in range(size):
            if all(marked[r][c] for c in range(size)):
                lines += 1
        for c in range(size):
            if all(marked[r][c] for r in range(size)):
                lines += 1
        return lines >= 3
    
    # FOUR LINES
    if p == "FOUR_LINES":
        lines = 0
        for r in range(size):
            if all(marked[r][c] for c in range(size)):
                lines += 1
        for c in range(size):
            if all(marked[r][c] for r in range(size)):
                lines += 1
        return lines >= 4
    
    # FULL HOUSE
    if p == "FULL_HOUSE":
        return all(marked[r][c] for r in range(size) for c in range(size))
    
    # FOUR CORNERS
    if p == "FOUR_CORNERS":
        return marked[0][0] and marked[0][4] and marked[4][0] and marked[4][4]
    
    # CENTER
    if p == "CENTER":
        return marked[2][2]
    
    # X PATTERN
    if p == "X_PATTERN":
        return all(marked[i][i] for i in range(size)) and \
               all(marked[i][size-i-1] for i in range(size))
    
    # PLUS PATTERN
    if p == "PLUS_PATTERN":
        return all(marked[2][c] for c in range(size)) and \
               all(marked[r][2] for r in range(size))
    
    # TOP ROW
    if p == "TOP_ROW":
        return all(marked[0][c] for c in range(size))
    
    # BOTTOM ROW
    if p == "BOTTOM_ROW":
        return all(marked[4][c] for c in range(size))
    
    # LEFT COLUMN
    if p == "LEFT_COLUMN":
        return all(marked[r][0] for r in range(size))
    
    # RIGHT COLUMN
    if p == "RIGHT_COLUMN":
        return all(marked[r][4] for r in range(size))
    
    # DIAGONAL MAIN
    if p == "DIAGONAL_MAIN":
        return all(marked[i][i] for i in range(size))
    
    # DIAGONAL SECOND
    if p == "DIAGONAL_SECOND":
        return all(marked[i][size-i-1] for i in range(size))
    
    # BOX (center 3x3)
    if p == "BOX":
        return all(marked[r][c] for r in range(1,4) for c in range(1,4))
    
    # OUTER FRAME
    if p == "OUTER_FRAME":
        for i in range(size):
            if not marked[0][i] or not marked[4][i]:
                return False
            if not marked[i][0] or not marked[i][4]:
                return False
        return True
    
    # L SHAPE
    if p == "L_SHAPE":
        return all(marked[r][0] for r in range(size)) and \
               all(marked[4][c] for c in range(size))
    
    # T SHAPE
    if p == "T_SHAPE":
        return all(marked[0][c] for c in range(size)) and \
               all(marked[r][2] for r in range(size))
    
    # CROSS
    if p == "CROSS":
        return all(marked[2][c] for c in range(size)) and \
               all(marked[r][2] for r in range(size))
    
    return False

# ==================== ADMIN COMMANDS FOR PATTERNS ====================

async def set_pattern_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to set pattern for a room"""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ ይህን ትዕዛዝ ለመጠቀም አይፈቀድልዎትም።")
        return
    
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                f"Usage: /setpattern <room_id> <pattern_name>\n\n"
                f"Available patterns:\n{chr(10).join(PATTERNS)}"
            )
            return
        
        room_id = int(args[0])
        pattern = args[1].upper()
        
        if pattern not in PATTERNS:
            await update.message.reply_text(
                f"❌ የተሳሳተ ንድፍ። የሚገኙ ንድፎች:\n{chr(10).join(PATTERNS)}"
            )
            return
        
        if room_id not in [1, 2, 3]:
            await update.message.reply_text("❌ ክፍል ከ1-3 ብቻ ነው")
            return
        
        # Store pattern in game_manager
        game_manager.room_patterns[room_id] = pattern
        game_manager.room_pattern_locked[room_id] = True
        
        await update.message.reply_text(
            f"✅ ክፍል {room_id} ንድፍ ተቀይሯል: {pattern}"
        )
    except ValueError:
        await update.message.reply_text("❌ የተሳሳተ የክፍል ቁጥር")

async def set_room_price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to set price for a room"""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ ይህን ትዕዛዝ ለመጠቀም አይፈቀድልዎትም።")
        return
    
    try:
        args = context.args
        room_id = int(args[0])
        price = int(args[1]) * 100  # Convert to cents
        
        if room_id == 1:
            global CARD_PRICE_ROOM1
            CARD_PRICE_ROOM1 = price
        elif room_id == 2:
            global CARD_PRICE_ROOM2
            CARD_PRICE_ROOM2 = price
        elif room_id == 3:
            global CARD_PRICE_ROOM3
            CARD_PRICE_ROOM3 = price
        else:
            await update.message.reply_text("❌ ክፍል ከ1-3 ብቻ ነው")
            return
        
        await update.message.reply_text(f"✅ ክፍል {room_id} ዋጋ ተቀይሯል: {price/100} ብር")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /setprice <room_id> <price_in_etb>")

async def list_patterns_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available patterns"""
    patterns_list = "\n".join([f"• {p}" for p in PATTERNS])
    await update.message.reply_text(
        f"📋 **የሚገኙ ንድፎች**\n\n{patterns_list}",
        parse_mode='Markdown'
    )

# ==================== Deposit Handlers ====================

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
        "💰 <b>ገንዘብ መሙያ ሜኑ</b>\n\nየክፍያ ዘዴዎን ይምረጡ:",
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
            "💰 <b>ገንዘብ መሙያ ሜኑ</b>\n\nየክፍያ ዘዴዎን ይምረጡ:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to edit message: {e}, sending new")
        await context.bot.send_message(
            chat_id=user_id,
            text="💰 <b>ገንዘብ መሙያ ሜኑ</b>\n\nየክፍያ ዘዴዎን ይምረጡ:",
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
            text="❌ ገንዘብ መሙላት ተሰርዟል።",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")
            ]])
        )
        return ConversationHandler.END

    method = query.data.replace("method_", "")
    context.user_data['payment_method'] = method
    method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])

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
            f"💰 <b>{method_info['name']} ገንዘብ መሙላት</b>\n\n"
            f"<b>መጠን ይምረጡ:</b>",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Edit failed, sending new: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"💰 <b>{method_info['name']} ገንዘብ መሙላት</b>\n\n<b>መጠን ይምረጡ:</b>",
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
            text="❌ ገንዘብ መሙላት ተሰርዟል።",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")
            ]])
        )
        return ConversationHandler.END

    amount_str = query.data.replace("amount_", "")
    try:
        amount = int(amount_str)
    except ValueError:
        await query.edit_message_text("❌ የተሳሳተ መጠን። እባክዎ እንደገና ይሞክሩ።")
        return SELECT_AMOUNT

    if amount < 10 or amount > 10000:
        await query.edit_message_text("❌ መጠን ከ10 እስከ 10000 ብር መሆን አለበት። እባክዎ እንደገና ይምረጡ።")
        return SELECT_AMOUNT

    method = context.user_data.get('payment_method')
    if not method:
        logger.warning(f"User {user_id} had no payment_method in user_data – restarting")
        await query.edit_message_text(
            "❌ ክፍለ ጊዜ አልቋል። እባክዎ በ /deposit ይጀምሩ",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")
            ]])
        )
        return ConversationHandler.END

    context.user_data['deposit_amount'] = amount
    method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])

    try:
        await query.delete_message()
        logger.info(f"Deleted old amount selection message for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to delete old message: {e}")

    auto_approve_text = ""
    if method_info.get('auto_approve', False):
        auto_approve_text = (
            f"\n\n⚡ <b>ራስ-ሰር ማረጋገጫ</b>\n"
            f"የTelebirr ክፍያዎች በራስ-ሰር ይጸድቃሉ! የደረሰኝ ቁጥርዎን ከላኩ በኋላ በሰከንዶች ውስጥ ቀሪ ሂሳብዎ ይዘምናል።"
        )

    text = (f"💰 <b>{method_info['name']} ገንዘብ መሙላት</b>\n\n"
            f"💵 መጠን: <b>{amount} ብር</b>\n"
            f"🏦 አካውንት: <code>{method_info['account']}</code>\n"
            f"የአካውንት ስም: {method_info['account_name']}\n\n"
            f"<b>መመሪያ:</b>\n"
            f"{method_info['instructions']}\n"
            f"{auto_approve_text}\n\n"
            f"✅ ገንዘቡን ከላኩ በኋላ እባክዎ <b>የደረሰኝ ቁጥርዎን</b> ይላኩ።\n\n"
            f"<i>ምሳሌ: <code>TRX123456</code></i>")
    
    await context.bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode='HTML'
    )
    logger.info(f"Sent new instructions for amount {amount} to user {user_id}")

    return WAIT_TRANSACTION

async def transaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    trx_id = update.message.text.strip().upper()
    logger.info(f"Transaction ID from user {user_id}: {trx_id}")

    amount = context.user_data.get('deposit_amount')
    method = context.user_data.get('payment_method')

    if not amount or not method:
        await update.message.reply_text("❌ ክፍለ ጊዜ አልቋል። እባክዎ በ /deposit ይጀምሩ")
        return ConversationHandler.END

    method_info = PAYMENT_METHODS.get(method, PAYMENT_METHODS['telebirr'])
    
    if method_info.get('auto_approve', False):
        verifying_msg = await update.message.reply_text(
            "⏳ <b>በማረጋገጥ ላይ...</b>\n\nእባክዎ ይጠብቁ። ክፍያዎ እየተረጋገጠ ነው።",
            parse_mode='HTML'
        )
        
        try:
            success, message, balance_update = await auto_approve_payment(
                user_id=user_id,
                amount=amount,
                transaction_id=trx_id,
                method=method
            )
        except Exception as e:
            logger.error(f"Auto-approval exception: {e}")
            success = False
            message = "በማረጋገጥ ላይ ስህተት ተከስቷል"
            balance_update = None
        
        if success:
            await verifying_msg.delete()
            await update.message.reply_text(
                f"✅ <b>ክፍያ በራስ-ሰር ጸድቋል!</b>\n\n"
                f"💰 መጠን: <b>{amount} ብር</b>\n"
                f"💳 ዘዴ: {method_info['name']}\n"
                f"🔢 የደረሰኝ ቁጥር: <code>{trx_id}</code>\n\n"
                f"🆕 አዲስ ቀሪ ሂሳብ: <b>{balance_update['new_balance']/100:.2f} ብር</b>\n\n"
                f"💡 አሁን መጫወት ይችላሉ! 🎮",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎮 ወደ ጨዋታ ይሂዱ", callback_data="play")
                ]])
            )
            
            bonus_paid = db.check_and_pay_referral_bonus(user_id)
            if bonus_paid:
                conn = db.get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,))
                    result = cursor.fetchone()
                    if result and result['referred_by']:
                        referrer_id = result['referred_by']
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=f"🎁 **የማስተዋወቂያ ቦነስ!** 🎁\n\n"
                                 f"የጋበዙት ሰው (ID: {user_id}) የመጀመሪያ ገንዘባቸውን ሞልተዋል!\n"
                                 f"እርስዎ **5 ብር** ቦነስ አግኝተዋል!",
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    logger.error(f"Failed to notify referrer: {e}")
                finally:
                    conn.close()
            
            context.user_data.clear()
            return ConversationHandler.END
        else:
            await verifying_msg.delete()
            await update.message.reply_text(
                f"⚠️ <b>ራስ-ሰር ማረጋገጥ አልተሳካም</b>\n\n"
                f"ምክንያት: {message}\n\n"
                f"ክፍያዎ ለአስተዳዳሪ ማረጋገጫ ተልኳል። በቅርቡ ይጸድቃል።",
                parse_mode='HTML'
            )
    
    methods = db.get_payment_methods(type='mobile_money', active_only=True)
    if not methods:
        await update.message.reply_text("❌ ምንም የክፍያ ዘዴዎች አልተገኙም")
        return ConversationHandler.END
    method_id = methods[0]['id']

    try:
        request_id = db.create_payment_request(
            user_id=user_id,
            method_id=method_id,
            amount=amount * 100,
            sender_phone=""
        )
    except Exception as e:
        logger.error(f"Database error creating payment request: {e}")
        await update.message.reply_text("❌ የክፍያ ጥያቄ መፍጠር አልተሳካም። እባክዎ በኋላ ይሞክሩ።")
        return ConversationHandler.END

    if not request_id:
        await update.message.reply_text("❌ የክፍያ ጥያቄ መፍጠር አልተሳካም። እባክዎ እንደገና ይሞክሩ።")
        return ConversationHandler.END

    try:
        db.add_payment_proof(request_id, 'text', trx_id)
    except Exception as e:
        logger.error(f"Failed to add payment proof: {e}")

    await update.message.reply_text(
        f"✅ <b>የክፍያ ሪፖርት ተልኳል!</b>\n\n"
        f"💰 <b>መጠን:</b> {amount} ብር\n"
        f"💳 <b>ዘዴ:</b> {method_info['name']}\n"
        f"🆔 <b>የጥያቄ መለያ:</b> <code>{request_id}</code>\n"
        f"🔢 <b>የግብይት መለያ:</b> <code>{trx_id}</code>\n\n"
        f"⏳ አስተዳዳሪው ክፍያዎን በቅርቡ ያረጋግጣል።\n"
        f"ቀሪ ሂሳብዎ ሲዘምን ይነገርዎታል።",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")
        ]])
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ አረጋግጥ", callback_data=f"approve_payment_{request_id}"),
            InlineKeyboardButton("❌ ከልክል", callback_data=f"reject_payment_{request_id}")
        ]
    ]
    try:
        admin_message = await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"💰 <b>አዲስ የክፍያ ጥያቄ</b>\n\n"
                 f"👤 <b>ተጠቃሚ:</b> {update.effective_user.first_name}\n"
                 f"🆔 <b>የተጠቃሚ መለያ:</b> <code>{user_id}</code>\n"
                 f"💰 <b>መጠን:</b> {amount} ብር\n"
                 f"💳 <b>ዘዴ:</b> {method_info['name']}\n"
                 f"🆔 <b>የጥያቄ መለያ:</b> <code>{request_id}</code>\n"
                 f"🔢 <b>የግብይት መለያ:</b> <code>{trx_id}</code>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.bot_data[f"admin_msg_{request_id}"] = {
            'chat_id': ADMIN_USER_ID,
            'message_id': admin_message.message_id
        }
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

    context.user_data.clear()
    return ConversationHandler.END

async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"User {update.effective_user.id} cancelled deposit")
    await update.message.reply_text(
        "❌ ገንዘብ መሙላት ተሰርዟል።",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ==================== Withdrawal Handlers ====================

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = db.get_user(user_id) or db.get_or_create_user(user_id, user.username, user.first_name, user.last_name)
    
    eligible, message = await check_withdrawal_eligibility(user_id)
    if not eligible:
        keyboard = [
            [InlineKeyboardButton("💳 ገንዘብ ሙሉ", callback_data="deposit_start")],
            [InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")]
        ]
        await update.message.reply_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    balance = user_data['balance'] / 100
    await update.message.reply_text(
        f"💸 <b>ማውጣት</b>\n\n"
        f"የእርስዎ ቀሪ ሂሳብ: <b>{balance:.2f} ብር</b>\n"
        f"ዝቅተኛ ማውጫ: <b>10 ብር</b>\n\n"
        f"እባክዎ ማውጣት የሚፈልጉትን መጠን ያስገቡ (10-{balance:.2f} ብር):",
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
    
    eligible, message = await check_withdrawal_eligibility(user.id)
    if not eligible:
        keyboard = [
            [InlineKeyboardButton("💳 ገንዘብ ሙሉ", callback_data="deposit_start")],
            [InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")]
        ]
        await query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    user_data = db.get_user(user.id) or db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    balance = user_data['balance'] / 100
    
    try:
        await query.edit_message_text(
            f"💸 <b>ማውጣት</b>\n\n"
            f"የእርስዎ ቀሪ ሂሳብ: <b>{balance:.2f} ብር</b>\n"
            f"ዝቅተኛ ማውጫ: <b>10 ብር</b>\n\n"
            f"እባክዎ ማውጣት የሚፈልጉትን መጠን ያስገቡ (10-{balance:.2f} ብር):",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Edit failed, sending new: {e}")
        await context.bot.send_message(
            chat_id=user.id,
            text=f"💸 <b>ማውጣት</b>\n\n"
                 f"የእርስዎ ቀሪ ሂሳብ: <b>{balance:.2f} ብር</b>\n"
                 f"ዝቅተኛ ማውጫ: <b>10 ብር</b>\n\n"
                 f"እባክዎ ማውጣት የሚፈልጉትን መጠን ያስገቡ (10-{balance:.2f} ብር):",
            parse_mode='HTML'
        )
    return WITHDRAW_AMOUNT

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    eligible, message = await check_withdrawal_eligibility(user_id)
    if not eligible:
        keyboard = [
            [InlineKeyboardButton("💳 ገንዘብ ሙሉ", callback_data="deposit_start")],
            [InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")]
        ]
        await update.message.reply_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    try:
        amount = float(text)
        user_data = db.get_user(user_id)
        balance_cents = user_data['balance']
        balance_etb = balance_cents / 100

        if amount < 10:
            await update.message.reply_text("❌ ዝቅተኛ ማውጫ 10 ብር ነው። እባክዎ ትክክለኛ መጠን ያስገቡ:")
            return WITHDRAW_AMOUNT
        if amount > balance_etb:
            await update.message.reply_text(
                f"❌ በቂ ገንዘብ የለም። የእርስዎ ቀሪ ሂሳብ {balance_etb:.2f} ብር ነው።\nእባክዎ ዝቅተኛ መጠን ያስገቡ:"
            )
            return WITHDRAW_AMOUNT

        amount_cents = int(amount * 100)
        context.user_data['withdraw_amount'] = amount_cents
        context.user_data['withdraw_amount_etb'] = amount

        await update.message.reply_text(
            "📱 <b>ስልክ ቁጥርዎን ያስገቡ</b> (በሞባይል ገንዘብዎ የተመዘገበው):\nምሳሌ: <code>0982372677</code>",
            parse_mode='HTML'
        )
        return WITHDRAW_PHONE
    except ValueError:
        await update.message.reply_text("❌ የተሳሳተ መጠን። እባክዎ ቁጥር ያስገቡ:")
        return WITHDRAW_AMOUNT

async def withdraw_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    user = update.effective_user
    amount_cents = context.user_data.get('withdraw_amount')
    amount_etb = context.user_data.get('withdraw_amount_etb')

    if not amount_cents:
        await update.message.reply_text("❌ ክፍለ ጊዜ አልቋል። እባክዎ በ /withdraw ይጀምሩ")
        return ConversationHandler.END

    request_id = db.create_withdrawal_request(user.id, amount_cents, phone)
    if not request_id:
        await update.message.reply_text("❌ የማውጫ ጥያቄ መፍጠር አልተሳካም። እባክዎ እንደገና ይሞክሩ።")
        return ConversationHandler.END

    keyboard = [
        [
            InlineKeyboardButton("✅ አረጋግጥ", callback_data=f"approve_withdraw_{request_id}"),
            InlineKeyboardButton("❌ ከልክል", callback_data=f"reject_withdraw_{request_id}")
        ]
    ]
    try:
        admin_message = await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"💸 <b>አዲስ የማውጫ ጥያቄ</b>\n\n"
                 f"👤 <b>ተጠቃሚ:</b> {user.first_name}\n"
                 f"🆔 <b>የተጠቃሚ መለያ:</b> <code>{user.id}</code>\n"
                 f"💰 <b>መጠን:</b> {amount_etb:.2f} ብር\n"
                 f"📱 <b>ስልክ:</b> {phone}\n"
                 f"🆔 <b>የጥያቄ መለያ:</b> <code>{request_id}</code>",
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
        f"✅ <b>የማውጫ ጥያቄ ተልኳል!</b>\n\n"
        f"💰 መጠን: {amount_etb:.2f} ብር\n"
        f"📱 ስልክ: {phone}\n"
        f"🆔 የጥያቄ መለያ: <code>{request_id}</code>\n\n"
        f"⏳ አስተዳዳሪው ጥያቄዎን በቅርቡ ያስኬዳል።",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")
        ]])
    )

    context.user_data.clear()
    return ConversationHandler.END

async def withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ ማውጣት ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ==================== Broadcast Command ====================

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                text=f"📢 <b>ማስታወቂያ</b>\n\n{message}",
                parse_mode='HTML'
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Failed to send broadcast to {uid}: {e}")
            failed += 1

    await update.message.reply_text(f"Broadcast sent to {sent} users. Failed: {failed}")

# ==================== Admin Command to Start Room 2 ====================

async def start_room2_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    game_id = 2
    if game_id not in game_manager.active_games:
        await update.message.reply_text("❌ Room 2 has not been initialized yet (no players).")
        return

    if game_manager.game_started.get(game_id, False):
        await update.message.reply_text("❌ Room 2 is already started.")
        return

    if game_manager.active_games[game_id]['total_cards_sold'] == 0:
        await update.message.reply_text("❌ No cards sold in Room 2. Cannot start.")
        return

    await game_manager.start_round(game_id)
    await update.message.reply_text(f"✅ Room 2 started manually.")

# ==================== REFERRAL SYSTEM ====================

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user:
        await update.message.reply_text("❌ ተጠቃሚ አልተገኘም። እባክዎ በመጀመሪያ ቦቱን ይጀምሩ።")
        return
    
    referral_code = user.get('referral_code')
    if not referral_code:
        referral_code = db.generate_referral_code(user_id)
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET referral_code = ? WHERE user_id = ?", (referral_code, user_id))
            conn.commit()
        finally:
            conn.close()
    
    stats = db.get_referral_stats(user_id)
    bot_username = context.bot.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{referral_code}"
    
    message = (
        f"🎁 **የማስተዋወቂያ ስርዓት** 🎁\n\n"
        f"🔗 **የእርስዎ ማስተዋወቂያ ሊንክ:**\n"
        f"`{referral_link}`\n\n"
        f"📊 **ስታቲስቲክስ:**\n"
        f"• የተመዘገቡ ጓደኞች: {stats['total_referrals']}\n"
        f"• በመጠባበቅ ላይ ያሉ: {stats['pending_bonuses']}\n"
        f"• ጠቅላላ የተከፈለ: {stats['total_earnings']/100:.2f} ብር\n\n"
        f"💡 **እንዴት እንደሚሰራ:**\n"
        f"1. ከላይ ያለውን ሊንክ ለጓደኛዎ ይላኩ\n"
        f"2. ጓደኛዎ ሊንኩን ጠቅ አድርጎ ቦቱን ይጀምር\n"
        f"3. ጓደኛዎ ቢያንስ አንድ ጊዜ ገንዘብ ይሙላ\n"
        f"4. እርስዎ **5 ብር** ያገኛሉ!\n\n"
        f"🚀 ብዙ ጓደኞችዎን ይጋብዙ እና ገንዘብ ያግኙ!"
    )
    
    if stats['recent_referrals']:
        message += "\n\n**የቅርብ ጊዜ መጋበዣዎች:**\n"
        for ref in stats['recent_referrals'][:5]:
            name = ref['first_name'] or f"User {ref['user_id']}"
            date = datetime.fromisoformat(ref['created_at']).strftime("%Y-%m-%d")
            status = "✅ ተከፍሏል" if ref.get('has_deposited') else "⏳ በመጠባበቅ ላይ"
            message += f"• {name} - {date} - {status}\n"
    
    keyboard = [
        [InlineKeyboardButton("📋 ሊንክ ቅዳ", callback_data="copy_link")],
        [InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")]
    ]
    
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )

async def referral_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "copy_link":
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        referral_code = user.get('referral_code')
        
        if not referral_code:
            referral_code = db.generate_referral_code(user_id)
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET referral_code = ? WHERE user_id = ?", (referral_code, user_id))
                conn.commit()
            finally:
                conn.close()
        
        bot_username = context.bot.username
        link = f"https://t.me/{bot_username}?start=ref_{referral_code}"
        
        await query.edit_message_text(
            f"✅ ሊንክዎ ተዘጋጅቷል! ይህን ሊንክ ለጓደኛዎ ይላኩ:\n\n`{link}`\n\n"
            f"ሊንኩን ለመቅዳት በላዩ ላይ ይንኩና Copy ይምረጡ።",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ ወደ ማስተዋወቂያ ተመለስ", callback_data="back_to_referral")
            ]])
        )
    
    elif query.data == "back_to_referral":
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        stats = db.get_referral_stats(user_id)
        bot_username = context.bot.username
        referral_link = f"https://t.me/{bot_username}?start=ref_{user['referral_code']}"
        
        message = (
            f"🎁 **የማስተዋወቂያ ስርዓት** 🎁\n\n"
            f"🔗 **የእርስዎ ማስተዋወቂያ ሊንክ:**\n"
            f"`{referral_link}`\n\n"
            f"📊 **ስታቲስቲክስ:**\n"
            f"• የተመዘገቡ ጓደኞች: {stats['total_referrals']}\n"
            f"• በመጠባበቅ ላይ ያሉ: {stats['pending_bonuses']}\n"
            f"• ጠቅላላ የተከፈለ: {stats['total_earnings']/100:.2f} ብር"
        )
        
        keyboard = [
            [InlineKeyboardButton("📋 ሊንክ ቅዳ", callback_data="copy_link")],
            [InlineKeyboardButton("◀️ ወደ ሜኑ ተመለስ", callback_data="menu")]
        ]
        
        await query.edit_message_text(
            message,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def start_with_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    referred_by = None
    if args and args[0].startswith('ref_'):
        referral_code = args[0][4:]
        referrer = db.get_user_by_referral_code(referral_code)
        if referrer and referrer['user_id'] != user.id:
            referred_by = referrer['user_id']
            logger.info(f"User {user.id} was referred by {referred_by}")
    
    user_data = db.get_or_create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        referred_by=referred_by
    )
    
    balance = user_data['balance'] / 100
    
    if referred_by:
        referrer = db.get_user(referred_by)
        referrer_name = referrer.get('first_name') or f"User {referred_by}"
        welcome_text = (
            f"🎉 እንኳን ደስ አለዎት! 🎉\n"
            f"👤 በ {referrer_name} ተጋብዘዋል\n"
            f"💰 {balance:.2f} ብር ተጠቃሚ ሁነዋል!\n"
            f"📝 ማስታወሻ: አንዴ ገንዘብ ሲሞሉ የሚጋብዝዎ ሰው 5 ብር ያገኛል\n\n"
            f"እድልዎ ተሳክቷል 🎯\n"
            f"ቀጣዩ ዙር ይበልጥ ትልቅ ሊሆን ይችላል!\n"
            f"👉 ካርድዎን እንደገና ይያዙ\n"
            f"👉 ትልቅ ሽልማት ይሞክሩ\n"
            f"👑 MK BINGO – እድል የሚቀይር ጨዋታ!"
        )
    else:
        welcome_text = (
            f"🎉 እንኳን ደስ አለዎት! 🎉\n"
            f"💰 {balance:.2f} ብር ተጠቃሚ ሁነዋል!\n"
            f"እድልዎ ተሳክቷል 🎯\n"
            f"ቀጣዩ ዙር ይበልጥ ትልቅ ሊሆን ይችላል!\n"
            f"👉 ካርድዎን እንደገና ይያዙ\n"
            f"👉 ትልቅ ሽልማት ይሞክሩ\n\n"
            f"💡 ጓደኞችዎን ይጋብዙ እና እያንዳንዳቸው ገንዘብ ሲሞሉ 5 ብር ያግኙ!\n"
            f"ዝርዝር መረጃ ለማግኘት /refer ይጠቀሙ\n\n"
            f"👑 MK BINGO – እድል የሚቀይር ጨዋታ!"
        )
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("💳 Deposit", callback_data="deposit_start")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_start")],
        [InlineKeyboardButton("🎁 Referral", callback_data="referral")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    if str(user.id) == str(ADMIN_USER_ID):
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = db.get_user(user_id)
    
    if not user_data:
        user_data = db.get_or_create_user(user_id, update.effective_user.username, update.effective_user.first_name, update.effective_user.last_name)
    
    balance = user_data['balance'] / 100
    referral_earnings = user_data.get('referral_earnings', 0) / 100
    games_won = user_data.get('games_won', 0)
    has_deposited = user_data.get('has_deposited', False)
    total_deposits = user_data.get('total_deposits', 0) / 100
    
    stats = db.get_referral_stats(user_id)
    
    deposit_status = "✅ ገንዘብ ሞልተዋል" if has_deposited else "❌ ገና ገንዘብ አልሞሉም"
    withdrawal_status = "✅ ማውጣት ይችላሉ" if has_deposited and total_deposits >= 100 else "❌ ማውጣት ከመጀመር 100 ብር መሙላት አለብዎት"
    
    message = (
        f"💰 **የእርስዎ ቀሪ ሂሳብ**\n\n"
        f"ጠቅላላ ቀሪ: **{balance:.2f} ብር**\n"
        f"ከጨዋታ ያገኙት: **{(balance - referral_earnings):.2f} ብር**\n"
        f"ከማስተዋወቂያ ያገኙት: **{referral_earnings:.2f} ብር**\n"
        f"ያስገቡት ጠቅላላ: **{total_deposits:.2f} ብር**\n"
        f"ያሸነፉባቸው ጨዋታዎች: **{games_won}**\n\n"
        f"📊 **ሁኔታ:**\n"
        f"• ተቀማጭ: {deposit_status}\n"
        f"• ማውጫ: {withdrawal_status}\n\n"
        f"👥 የተመዘገቡ ጓደኞች: **{stats['total_referrals']}**\n"
        f"⏳ በመጠባበቅ ላይ ያሉ: **{stats['pending_bonuses']}**"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')

# ==================== Game Class with Manual Marking and Card Suspension ====================

class IntegratedBingoGame:
    def __init__(self):
        # Per‑room data dictionaries
        self.round_numbers = {}
        self.called_numbers = {}
        self.game_started = {}
        self.total_pool = {}
        self.house_profit = 0
        self.active_games = {}
        self.game_connections = {}
        self.taken_cards = {}
        self.game_winner = {}
        self.number_tasks = {}
        self.countdown_timers = {}
        self.game_locks = {}
        self.auto_start_timers = {}
        self.first_card_time = {}
        self.reset_timers = {}
        
        # Pattern system
        self.room_patterns = {}  # room_id -> pattern name
        self.room_pattern_locked = {}  # room_id -> bool
        self.room_price = {}  # room_id -> price in cents
        self.room_auto_timer_task = {}  # room_id -> asyncio task
        self.suspended_players = {}  # room_id -> set of suspended user_ids

        # Card-based suspension system
        self.suspended_cards = {}  # game_id -> {user_id: set(suspended_card_ids)}
        self.false_bingo_attempts = {}  # game_id -> {user_id: int}
        self.player_marked_numbers = {}  # game_id -> {user_id: {card_id: set(marked_numbers)}}
        self.card_owners = {}  # game_id -> {card_id: user_id}
        self.user_selected_cards = {}  # game_id -> {user_id: set(card_ids)}

        self.bot_app = None
        self.user_connections = {}
        self.MAX_CONNECTIONS_PER_USER = 5
        self.stop_number_generation = {}
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

    async def start_auto_settings_timer(self, game_id: int):
        """Auto-set pattern and price after 10 seconds if not set by admin"""
        await asyncio.sleep(10)
        
        async with self.get_lock(game_id):
            if not self.room_pattern_locked.get(game_id, False):
                # Auto-set random pattern and default price
                self.room_patterns[game_id] = random.choice(PATTERNS)
                self.room_pattern_locked[game_id] = True
                if game_id == 1:
                    self.room_price[game_id] = CARD_PRICE_ROOM1
                elif game_id == 2:
                    self.room_price[game_id] = CARD_PRICE_ROOM2
                else:
                    self.room_price[game_id] = CARD_PRICE_ROOM3
                
                logger.info(f"Room {game_id} auto-set pattern: {self.room_patterns[game_id]}")

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
                self.round_numbers[game_id] = 1
                self.called_numbers[game_id] = []
                self.game_started[game_id] = False
                self.stop_number_generation[game_id] = False
                self.suspended_players[game_id] = set()
                
                # Initialize card-based suspension structures
                self.suspended_cards[game_id] = {}
                self.false_bingo_attempts[game_id] = {}
                self.player_marked_numbers[game_id] = {}
                self.card_owners[game_id] = {}
                self.user_selected_cards[game_id] = {}
                
                self.active_games[game_id] = {
                    'called_numbers': [],
                    'players': {},
                    'prize_pool': 0,
                    'total_cards_sold': 0,
                    'last_winner': None,
                }
                
                # Start auto-settings timer for new room
                asyncio.create_task(self.start_auto_settings_timer(game_id))

            self.game_connections[game_id].append(websocket)
            if user_id not in self.active_games[game_id]['players']:
                self.active_games[game_id]['players'][user_id] = {
                    'name': user.get('first_name', f"Player{user_id}"),
                    'cards': [],
                    'card_ids': [],
                    'marked': {},
                    'ready': False,
                    'winner': False,
                    'balance': user['balance'],
                    'suspended': False
                }
                
                # Initialize card suspension tracking for this user
                if user_id not in self.suspended_cards[game_id]:
                    self.suspended_cards[game_id][user_id] = set()
                if user_id not in self.player_marked_numbers[game_id]:
                    self.player_marked_numbers[game_id][user_id] = {}
                if user_id not in self.user_selected_cards[game_id]:
                    self.user_selected_cards[game_id][user_id] = set()

            active_games_count = db.get_active_games_count(user_id)
            total_stake = db.get_total_stake(user_id)

            pattern_display = self.room_patterns.get(game_id, "አልተመረጠም")
            
            # Send initial connection data
            await websocket.send_json({
                'type': 'connected',
                'taken_cards': list(self.taken_cards[game_id]),
                'players': self.get_players(game_id),
                'round': self.round_numbers[game_id],
                'game_started': self.game_started[game_id],
                'winner': self.game_winner[game_id],
                'called_numbers': self.active_games[game_id]['called_numbers'],
                'countdown': self.countdown_timers[game_id],
                'balance': user['balance'] / 100,
                'active_games': active_games_count,
                'total_stake': total_stake / 100,
                'auto_start_delay': AUTO_START_DELAY,
                'auto_start_active': game_id in self.auto_start_timers,
                'pattern': pattern_display,
                'pattern_locked': self.room_pattern_locked.get(game_id, False),
                'manual_marking': True  # Signal that manual marking is required
            })

            # Send player's cards with their marked numbers
            player = self.active_games[game_id]['players'][user_id]
            for card_id in player['card_ids']:
                card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                if card_data:
                    # Get manually marked numbers for this card
                    marked_numbers = []
                    if (game_id in self.player_marked_numbers and 
                        user_id in self.player_marked_numbers[game_id] and 
                        card_id in self.player_marked_numbers[game_id][user_id]):
                        marked_numbers = list(self.player_marked_numbers[game_id][user_id][card_id])
                    
                    await websocket.send_json({
                        'type': 'your_card',
                        'card': card_data['card'],
                        'card_id': card_id,
                        'marked': marked_numbers,
                        'suspended': card_id in self.suspended_cards[game_id].get(user_id, set())
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
            {
                'id': uid, 
                'name': data['name'], 
                'card_count': len(data['card_ids']), 
                'ready': data['ready'], 
                'winner': data['winner'],
                'suspended': uid in self.suspended_players.get(game_id, set())
            }
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
                    if self.countdown_timers[game_id] <= 0 and self.game_started.get(game_id, False):
                        self.countdown_timers[game_id] = 15
        except:
            pass

    async def select_cards(self, game_id: int, user_id: int, card_ids: List[int]) -> Tuple[bool, str, int, Optional[int]]:
        """
        Select cards for a player with proper validation and duplicate prevention
        """
        async with self.get_lock(game_id):
            try:
                # Initialize tracking structures
                if game_id not in self.user_selected_cards:
                    self.user_selected_cards[game_id] = {}
                if user_id not in self.user_selected_cards[game_id]:
                    self.user_selected_cards[game_id][user_id] = set()
                
                # Validate game state
                if game_id not in self.active_games:
                    return False, "Game not found", 0, None
                
                if self.game_started.get(game_id, False):
                    return False, "Game already started", 0, None
                
                if user_id not in self.active_games[game_id]['players']:
                    return False, "Player not found", 0, None
                
                player = self.active_games[game_id]['players'][user_id]
                
                # Check if player is suspended
                if user_id in self.suspended_players.get(game_id, set()):
                    return False, "You are suspended from this round", 0, None
                
                # Check if cards are already selected by this user
                already_selected = [cid for cid in card_ids if cid in self.user_selected_cards[game_id][user_id]]
                if already_selected:
                    return False, f"Cards {already_selected} already selected", 0, None
                
                # Check maximum cards limit
                if len(player['card_ids']) + len(card_ids) > MAX_CARDS_PER_PLAYER:
                    return False, f"Maximum {MAX_CARDS_PER_PLAYER} cards per player", 0, None
                
                # Validate all cards exist and are available
                price_per_card = self.room_price.get(game_id, 1000)
                available_cards = []
                
                for card_id in card_ids:
                    # Check if card exists
                    card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                    if not card_data:
                        return False, f"Card {card_id} not found", 0, None
                    
                    # Check if card is already taken by someone else
                    if card_id in self.taken_cards.get(game_id, set()):
                        owner = self.card_owners.get(game_id, {}).get(card_id)
                        if owner and owner != user_id:
                            return False, f"Card {card_id} already taken by another player", 0, None
                    
                    available_cards.append(card_data)
                
                # Calculate total cost
                total_cost = len(card_ids) * price_per_card
                
                # Check user balance
                user = db.get_user(user_id)
                if not user or user['balance'] < total_cost:
                    return False, f"Insufficient balance. Need {total_cost/100} ETB", total_cost, None
                
                # Deduct balance
                update_result = db.update_balance(
                    user_id, 
                    -total_cost, 
                    'game_fee', 
                    f'Selected {len(card_ids)} cards for game #{game_id}'
                )
                
                if not update_result:
                    return False, "Failed to deduct balance", total_cost, None
                
                new_balance = update_result['new_balance']
                
                # Initialize game structures if needed
                if game_id not in self.taken_cards:
                    self.taken_cards[game_id] = set()
                if game_id not in self.card_owners:
                    self.card_owners[game_id] = {}
                if game_id not in self.suspended_cards:
                    self.suspended_cards[game_id] = {}
                if user_id not in self.suspended_cards.get(game_id, {}):
                    self.suspended_cards.setdefault(game_id, {})[user_id] = set()
                
                # Assign each card
                for i, card_data in enumerate(available_cards):
                    card_id = card_ids[i]
                    
                    # Mark card as taken
                    self.taken_cards[game_id].add(card_id)
                    self.card_owners[game_id][card_id] = user_id
                    self.user_selected_cards[game_id][user_id].add(card_id)
                    
                    # Add to player's cards
                    player['cards'].append(card_data['card'])
                    player['card_ids'].append(card_id)
                    player['marked'][card_id] = []
                    
                    # Initialize marked numbers for manual marking
                    if game_id not in self.player_marked_numbers:
                        self.player_marked_numbers[game_id] = {}
                    if user_id not in self.player_marked_numbers[game_id]:
                        self.player_marked_numbers[game_id][user_id] = {}
                    if card_id not in self.player_marked_numbers[game_id][user_id]:
                        self.player_marked_numbers[game_id][user_id][card_id] = set()
                
                player['balance'] = new_balance
                player['ready'] = True
                
                # Update game stats
                self.active_games[game_id]['total_cards_sold'] += len(card_ids)
                self.active_games[game_id]['prize_pool'] = self.active_games[game_id]['total_cards_sold'] * price_per_card
                
                # Start auto-start timer if conditions met
                if (game_id != 2 and not self.game_started.get(game_id, False) and 
                    self.active_games[game_id]['total_cards_sold'] >= 5):
                    if game_id not in self.auto_start_timers:
                        asyncio.create_task(self.start_auto_start_timer(game_id))
                        await self.broadcast(game_id, {
                            'type': 'auto_start_timer', 
                            'delay': AUTO_START_DELAY
                        })
                
                # Broadcast player ready
                await self.broadcast(game_id, {
                    'type': 'player_ready', 
                    'players': self.get_players(game_id), 
                    'user_id': user_id
                })
                
                logger.info(f"User {user_id} successfully selected {len(card_ids)} cards in room {game_id}")
                
                return True, f"Selected {len(card_ids)} cards", total_cost, new_balance
                
            except Exception as e:
                logger.error(f"Error in select_cards: {e}")
                return False, f"Error selecting cards: {str(e)}", 0, None

    async def start_auto_start_timer(self, game_id: int):
        if game_id in self.auto_start_timers:
            self.auto_start_timers[game_id].cancel()
        self.first_card_time[game_id] = time.time()

        async def auto_start():
            await asyncio.sleep(AUTO_START_DELAY)
            async with self.get_lock(game_id):
                if not self.game_started.get(game_id, False) and game_id in self.active_games and self.active_games[game_id]['players']:
                    await self.start_round(game_id)
        self.auto_start_timers[game_id] = asyncio.create_task(auto_start())

    async def start_round(self, game_id: int = 1):
        if self.game_started.get(game_id, False) or game_id not in self.active_games or self.active_games[game_id]['total_cards_sold'] == 0:
            return
        self.game_started[game_id] = True
        self.stop_number_generation[game_id] = False
        if game_id in self.auto_start_timers:
            self.auto_start_timers[game_id].cancel()
            del self.auto_start_timers[game_id]
        await self.broadcast(game_id, {'type': 'game_started', 'round': self.round_numbers[game_id]})
        asyncio.create_task(self.draw_numbers(game_id))

    async def draw_numbers(self, game_id: int = 1):
        """Draw numbers but don't auto-mark them - players mark manually"""
        numbers = list(range(1, 76))
        random.shuffle(numbers)

        for n in numbers:
            if self.stop_number_generation.get(game_id, False) or self.game_winner.get(game_id):
                break

            await asyncio.sleep(3)

            if self.stop_number_generation.get(game_id, False) or self.game_winner.get(game_id):
                break

            async with self.get_lock(game_id):
                if self.stop_number_generation.get(game_id, False) or self.game_winner.get(game_id) or not self.game_started.get(game_id, False):
                    break

                # Add number to called numbers
                self.called_numbers[game_id].append(n)
                self.active_games[game_id]['called_numbers'].append(n)
                
                # Broadcast the number but DON'T auto-mark
                await self.broadcast(game_id, {
                    'type': 'number_called',
                    'number': n,
                    'called': self.active_games[game_id]['called_numbers'],
                    'auto_mark': False  # Signal that players need to mark manually
                })

                logger.info(f"Room {game_id} - Called number: {n} (players must mark manually)")

    async def handle_mark_number(self, game_id: int, user_id: int, card_id: int, number: int) -> dict:
        """
        Handle manual marking of a called number by the player
        Returns dict with success status and any relevant data
        """
        async with self.get_lock(game_id):
            result = {
                'success': False,
                'message': '',
                'card_id': card_id,
                'number': number,
                'pattern_completed': False,
                'already_marked': False
            }
            
            try:
                # Validation checks
                if not self.game_started.get(game_id, False):
                    result['message'] = 'Game not started'
                    return result
                
                if game_id not in self.active_games:
                    result['message'] = 'Game not found'
                    return result
                
                if user_id not in self.active_games[game_id]['players']:
                    result['message'] = 'Player not found'
                    return result
                
                player = self.active_games[game_id]['players'][user_id]
                
                # Check if player is suspended
                if user_id in self.suspended_players.get(game_id, set()):
                    result['message'] = 'You are suspended from this round'
                    return result
                
                # Check if card is suspended
                if (game_id in self.suspended_cards and 
                    user_id in self.suspended_cards[game_id] and 
                    card_id in self.suspended_cards[game_id][user_id]):
                    result['message'] = f'Card {card_id} is suspended'
                    return result
                
                # Check if card belongs to player
                if card_id not in player['card_ids']:
                    result['message'] = 'Card does not belong to you'
                    return result
                
                # Check if number has been called
                if number not in self.active_games[game_id]['called_numbers']:
                    result['message'] = f'Number {number} has not been called yet'
                    return result
                
                # Get card data
                card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                if not card_data:
                    result['message'] = 'Card not found'
                    return result
                
                # Check if number is on this card
                number_found = False
                for col in range(5):
                    for row in range(5):
                        val = card_data['card'][col][row]
                        if val == number:
                            number_found = True
                            break
                    if number_found:
                        break
                
                if not number_found:
                    result['message'] = f'Number {number} is not on this card'
                    return result
                
                # Initialize marking structures if needed
                if game_id not in self.player_marked_numbers:
                    self.player_marked_numbers[game_id] = {}
                if user_id not in self.player_marked_numbers[game_id]:
                    self.player_marked_numbers[game_id][user_id] = {}
                if card_id not in self.player_marked_numbers[game_id][user_id]:
                    self.player_marked_numbers[game_id][user_id][card_id] = set()
                
                # Check if already marked
                if number in self.player_marked_numbers[game_id][user_id][card_id]:
                    result['message'] = f'Number {number} already marked on this card'
                    result['already_marked'] = True
                    return result
                
                # Mark the number
                self.player_marked_numbers[game_id][user_id][card_id].add(number)
                
                # Also update player['marked'] for backward compatibility
                if card_id not in player['marked']:
                    player['marked'][card_id] = []
                if number not in player['marked'][card_id]:
                    player['marked'][card_id].append(number)
                
                result['success'] = True
                result['message'] = f'✓ Marked {number} on card {card_id}'
                
                # Check if this mark completes the pattern
                is_winner = await self.check_winner_with_manual_marks(
                    game_id, user_id, card_id
                )
                
                if is_winner:
                    result['pattern_completed'] = True
                    result['message'] = f'🎉 BINGO! Card {card_id} completed the pattern! Click BINGO to claim!'
                
                logger.info(f"User {user_id} manually marked {number} on card {card_id}")
                return result
                
            except Exception as e:
                logger.error(f"Error in handle_mark_number: {e}")
                result['message'] = f"Error: {str(e)}"
                return result

    async def handle_false_bingo(self, game_id: int, user_id: int, card_id: int = None) -> Tuple[bool, str, dict]:
        """
        Handle false bingo claim with improved card-based suspension
        Returns: (success, message, updated_status)
        """
        async with self.get_lock(game_id):
            try:
                if game_id not in self.active_games:
                    return False, "Game not found", {}
                
                if user_id not in self.active_games[game_id]['players']:
                    return False, "Player not found", {}
                
                player = self.active_games[game_id]['players'][user_id]
                
                # Check if player has any cards
                if not player['card_ids']:
                    return False, "You have no cards", {}
                
                # Check if player is already fully suspended
                if user_id in self.suspended_players.get(game_id, set()):
                    return False, "You are already suspended from this round", {}
                
                # Initialize suspension tracking
                if game_id not in self.suspended_cards:
                    self.suspended_cards[game_id] = {}
                if user_id not in self.suspended_cards[game_id]:
                    self.suspended_cards[game_id][user_id] = set()
                
                if game_id not in self.false_bingo_attempts:
                    self.false_bingo_attempts[game_id] = {}
                if user_id not in self.false_bingo_attempts[game_id]:
                    self.false_bingo_attempts[game_id][user_id] = 0
                
                # Get player's active cards (not suspended)
                active_cards = []
                suspended_cards = self.suspended_cards[game_id][user_id]
                
                for cid in player['card_ids']:
                    if cid not in suspended_cards:
                        active_cards.append(cid)
                
                logger.info(f"User {user_id} false bingo - Active cards: {active_cards}, Suspended: {list(suspended_cards)}")
                
                if not active_cards:
                    # All cards suspended - suspend player entirely
                    if game_id not in self.suspended_players:
                        self.suspended_players[game_id] = set()
                    self.suspended_players[game_id].add(user_id)
                    
                    logger.info(f"User {user_id} fully suspended - no active cards left")
                    
                    # Broadcast full suspension
                    await self.broadcast(game_id, {
                        'type': 'player_suspended',
                        'user_id': user_id,
                        'reason': 'all_cards_suspended',
                        'suspended_players': list(self.suspended_players[game_id])
                    })
                    
                    status = await self.get_player_card_status(game_id, user_id)
                    return True, "All your cards are suspended. You are out of this round!", status
                
                # Decide which card to suspend
                card_to_suspend = None
                reason = ""
                
                # If a specific card was provided and it belongs to the player and is active
                if card_id and card_id in player['card_ids']:
                    if card_id in suspended_cards:
                        # Card already suspended, pick a random active card
                        card_to_suspend = random.choice(active_cards)
                        reason = "random (selected card already suspended)"
                    else:
                        # Suspend the specific card that caused false bingo
                        card_to_suspend = card_id
                        reason = "specific_card"
                else:
                    # No card specified, pick a random active card
                    if active_cards:
                        card_to_suspend = random.choice(active_cards)
                        reason = "random"
                    else:
                        return False, "No active cards to suspend", {}
                
                # Suspend the card
                self.suspended_cards[game_id][user_id].add(card_to_suspend)
                self.false_bingo_attempts[game_id][user_id] += 1
                
                # Clear marked numbers for suspended card
                if (game_id in self.player_marked_numbers and 
                    user_id in self.player_marked_numbers[game_id] and 
                    card_to_suspend in self.player_marked_numbers[game_id][user_id]):
                    del self.player_marked_numbers[game_id][user_id][card_to_suspend]
                
                # Also clear from player['marked'] for backward compatibility
                if card_to_suspend in player['marked']:
                    player['marked'][card_to_suspend] = []
                
                # Get remaining active cards count
                remaining_cards = len([c for c in player['card_ids'] 
                                     if c not in self.suspended_cards[game_id][user_id]])
                
                logger.info(f"User {user_id} - Suspended card {card_to_suspend}, remaining: {remaining_cards}")
                
                # Broadcast card suspension
                await self.broadcast(game_id, {
                    'type': 'card_suspended',
                    'user_id': user_id,
                    'suspended_card_id': card_to_suspend,
                    'remaining_cards': remaining_cards,
                    'total_cards': len(player['card_ids']),
                    'false_bingo_attempts': self.false_bingo_attempts[game_id][user_id],
                    'suspended_cards': list(self.suspended_cards[game_id][user_id]),
                    'reason': reason
                })
                
                # Get updated status
                status = await self.get_player_card_status(game_id, user_id)
                
                # Send personal message to the player
                player_message = self._get_suspension_message(reason, card_to_suspend, remaining_cards)
                
                return True, player_message, status
                
            except Exception as e:
                logger.error(f"Error in handle_false_bingo: {e}")
                return False, f"Error: {str(e)}", {}

    def _get_suspension_message(self, reason: str, card_id: int, remaining_cards: int) -> str:
        """Get appropriate message based on suspension reason"""
        base_messages = {
            "specific_card": f"❌ Wrong Bingo! Card {card_id} is suspended for this round!",
            "random": f"❌ Wrong Bingo! Random card {card_id} has been suspended!",
            "random (selected card already suspended)": f"❌ Wrong Bingo! That card was already suspended. Random card {card_id} suspended instead!"
        }
        
        message = base_messages.get(reason, f"❌ Wrong Bingo! Card {card_id} suspended!")
        
        if remaining_cards == 0:
            message += "\n\n⚠️ You have no active cards left! You are out of this round."
        elif remaining_cards == 1:
            message += "\n\n⚠️ Warning: This is your last active card!"
        elif remaining_cards <= 3:
            message += f"\n\n⚠️ You have {remaining_cards} active cards remaining."
        
        return message

    async def get_player_card_status(self, game_id: int, user_id: int) -> dict:
        """Get status of all player's cards (active/suspended)"""
        try:
            if game_id not in self.active_games:
                return {'error': 'Game not found'}
            
            if user_id not in self.active_games[game_id]['players']:
                return {'error': 'Player not found'}
            
            player = self.active_games[game_id]['players'][user_id]
            
            # Get suspended cards
            suspended = set()
            if game_id in self.suspended_cards and user_id in self.suspended_cards[game_id]:
                suspended = self.suspended_cards[game_id][user_id]
            
            # Get false bingo attempts
            attempts = 0
            if game_id in self.false_bingo_attempts and user_id in self.false_bingo_attempts[game_id]:
                attempts = self.false_bingo_attempts[game_id][user_id]
            
            result = {
                'total_cards': len(player['card_ids']),
                'active_cards': [],
                'suspended_cards': [],
                'false_bingo_attempts': attempts,
                'fully_suspended': user_id in self.suspended_players.get(game_id, set())
            }
            
            for card_id in player['card_ids']:
                card_info = {
                    'card_id': card_id,
                    'status': 'suspended' if card_id in suspended else 'active'
                }
                
                # Add marked numbers if any
                marked_count = 0
                marked_numbers = []
                if (game_id in self.player_marked_numbers and 
                    user_id in self.player_marked_numbers[game_id] and 
                    card_id in self.player_marked_numbers[game_id][user_id]):
                    marked_count = len(self.player_marked_numbers[game_id][user_id][card_id])
                    marked_numbers = list(self.player_marked_numbers[game_id][user_id][card_id])
                
                card_info['marked_count'] = marked_count
                card_info['marked_numbers'] = marked_numbers
                
                if card_id in suspended:
                    result['suspended_cards'].append(card_info)
                else:
                    result['active_cards'].append(card_info)
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting card status: {e}")
            return {'error': str(e)}

    async def check_winner_with_manual_marks(self, game_id: int, user_id: int, card_id: int) -> bool:
        """Check winner using only manually marked numbers"""
        try:
            if game_id not in self.active_games:
                return False
            
            # Check if player is suspended
            if user_id in self.suspended_players.get(game_id, set()):
                return False
            
            # Check if card is suspended
            if (game_id in self.suspended_cards and 
                user_id in self.suspended_cards[game_id] and 
                card_id in self.suspended_cards[game_id][user_id]):
                return False
            
            player = self.active_games[game_id]['players'].get(user_id)
            if not player:
                return False
            
            # Find the card
            if card_id not in player['card_ids']:
                return False
            
            card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
            if not card_data:
                return False
            
            # Get ONLY manually marked numbers (not auto-marked)
            marked_numbers = set()
            if (game_id in self.player_marked_numbers and 
                user_id in self.player_marked_numbers[game_id] and 
                card_id in self.player_marked_numbers[game_id][user_id]):
                marked_numbers = self.player_marked_numbers[game_id][user_id][card_id]
            
            # Convert to marked positions
            marked_positions = []
            for row in range(5):
                for col in range(5):
                    val = card_data['card'][col][row]
                    if val == 'FREE':
                        marked_positions.append((row, col))
                    elif val in marked_numbers:  # Only count manually marked numbers
                        marked_positions.append((row, col))
            
            # Get room pattern
            pattern = self.room_patterns.get(game_id, "ONE_LINE")
            
            # Check if pattern is satisfied
            return check_pattern(marked_positions, pattern)
            
        except Exception as e:
            logger.error(f"Error checking winner with manual marks: {e}")
            return False

    async def finish_round_multi(self, game_id: int, winners: List[Tuple[int, int]]):
        if game_id not in self.active_games:
            return

        self.stop_number_generation[game_id] = True
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
                    description=f'Won round {self.round_numbers[game_id]} in room #{game_id} (shared win)'
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

        pattern_display = self.room_patterns.get(game_id, "አልተመረጠም")
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
            'winning_card_id': winners[0][1] if winners else None,
            'pattern': pattern_display
        })

        if self.bot_app:
            for user_id, _ in winners:
                try:
                    await self.bot_app.bot.send_message(
                        chat_id=user_id,
                        text=f"🎉 እንኳን ደስ አለዎት! 🎉\n\nበክፍል {game_id} ዙር {self.round_numbers[game_id]} አሸንፈዋል!\nየእርስዎ ድርሻ: {prize_per_winner/100} ብር"
                    )
                except:
                    pass

        if game_id in self.reset_timers:
            self.reset_timers[game_id].cancel()
        self.reset_timers[game_id] = asyncio.create_task(self.delayed_reset(game_id))

    async def delayed_reset(self, game_id: int):
        await asyncio.sleep(ROUND_RESET_DELAY)
        await self.reset_round(game_id)

    async def reset_round(self, game_id: int = 1):
        """Reset round with card suspension cleanup"""
        logger.info(f"Resetting round for game {game_id}")
        
        # Clear card-specific suspension data for this game
        if game_id in self.suspended_cards:
            del self.suspended_cards[game_id]
        if game_id in self.false_bingo_attempts:
            del self.false_bingo_attempts[game_id]
        if game_id in self.player_marked_numbers:
            del self.player_marked_numbers[game_id]
        if game_id in self.card_owners:
            del self.card_owners[game_id]
        if game_id in self.user_selected_cards:
            del self.user_selected_cards[game_id]
        
        # Clear player suspensions
        if game_id in self.suspended_players:
            del self.suspended_players[game_id]
        
        # Reset round numbers
        self.round_numbers[game_id] = self.round_numbers.get(game_id, 1) + 1
        self.called_numbers[game_id] = []
        self.game_started[game_id] = False
        self.stop_number_generation[game_id] = False
        
        # Cancel timers
        if game_id in self.auto_start_timers:
            self.auto_start_timers[game_id].cancel()
            del self.auto_start_timers[game_id]
        if game_id in self.first_card_time:
            del self.first_card_time[game_id]
        if game_id in self.reset_timers:
            del self.reset_timers[game_id]
        
        self.game_winner[game_id] = None
        
        # Reset game data
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
        
        pattern_display = self.room_patterns.get(game_id, "አልተመረጠም")
        logger.info(f"✅ Room {game_id} round {self.round_numbers[game_id]} ready")
        
        await self.broadcast(game_id, {
            'type': 'game_reset',
            'round': self.round_numbers[game_id],
            'players': self.get_players(game_id),
            'countdown': 15,
            'pattern': pattern_display
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

# ==================== Menu and Navigation ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_with_referral(update, context)

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
        if not user_data or user_data['balance'] < CARD_PRICE_ROOM1:
            await query.edit_message_text(
                f"❌ በቂ ገንዘብ የለም። ቢያንስ {CARD_PRICE_ROOM1/100} ብር ያስፈልጋል።",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💳 ገንዘብ ሙሉ", callback_data="deposit_start"),
                    InlineKeyboardButton("◀️ ተመለስ", callback_data="menu")
                ]])
            )
            return
        webapp_url = f"{BASE_URL}/rooms?user_id={user.id}"
        await query.edit_message_text(
            f"🎮 ጨዋታውን ለመክፈት እና ክፍል ለመምረጥ ይጫኑ\n\n"
            f"💰 ቀሪ ሂሳብ: {user_data['balance']/100:.2f} ብር",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 ጨዋታ ክፍሎች", web_app={'url': webapp_url})
            ]])
        )
    elif data == "balance":
        await balance_command(update, context)
    elif data == "referral":
        await referral_callback(update, context)
    elif data == "help":
        help_text = (
            "❓ የቢንጎ ሮቦት እገዛ\n\nእንዴት እንደሚጫወት:\n"
            "1. 'Play Bingo' ን ይጫኑ\n"
            "2. በድረ-ገጹ ላይ ክፍል ይምረጡ\n"
            "3. ካርዶችን ይምረጡ (1-1000)\n"
            "4. ጨዋታው 5 ካርዶች ከተሸጡ በኋላ በ30 ሰከንድ ይጀምራል\n"
            "5. ቁጥሮች በየ3 ሰከንድ ይጠራሉ\n"
            "6. **በእራስዎ ምልክት ያድርጉ** - ቁጥሩ ሲጠራ ካርድዎ ላይ ጠቅ ያድርጉ\n"
            "7. ንድፉን ሲያጠናቅቁ 'ቢንጎ' ይጫኑ\n"
            "8. የውሸት ቢንጎ ብትጫኑ አንድ ካርድዎ ይታገዳል\n\n"
            f"ዋጋ (ክፍል 1): {CARD_PRICE_ROOM1/100} ብር በካርድ\n"
            f"ዋጋ (ክፍል 2): {CARD_PRICE_ROOM2/100} ብር በካርድ\n"
            f"ዋጋ (ክፍል 3): {CARD_PRICE_ROOM3/100} ብር በካርድ\n\n"
            "ተቀማጭ:\n• 'Deposit' ቁልፍን ይጫኑ\n• Telebirr ወይም CBE Birr ይምረጡ\n• መጠን ይምረጡ (50–10000 ብር)\n• ገንዘቡን ይላኩ እና የደረሰኝ ቁጥር ይላኩ\n\n"
            "ማውጣት:\n• 'Withdraw' ቁልፍን ይጫኑ\n• መጠን እና ስልክ ቁጥር ያስገቡ\n• አስተዳዳሪው ያረጋግጣል እና ገንዘቡን ይልካል\n\n"
            "🎁 ማስተዋወቂያ:\n• /refer በመጠቀም ጓደኞችዎን ይጋብዙ\n• እያንዳንዱ ጓደኛዎ ገንዘብ ሲሞላ 5 ብር ያግኙ\n\n"
            "💸 ማውጣት ሁኔታ:\n• ማውጣት ከመጀመር በፊት ቢያንስ 100 ብር መሙላት አለብዎት\n\n"
            "⚡ ራስ-ሰር ማረጋገጫ:\n• የTelebirr ክፍያዎች በራስ-ሰር ይጸድቃሉ!"
        )
        await query.edit_message_text(
            help_text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ ተመለስ", callback_data="menu")
            ]])
        )
    elif data == "admin" and str(user.id) == str(ADMIN_USER_ID):
        stats = db.get_system_stats()
        pending_payments = len(db.get_pending_payment_requests(limit=100))
        pending_withdrawals = len(db.get_pending_withdrawal_requests(limit=100))
        await query.edit_message_text(
            f"👑 የአስተዳዳሪ ፓነል\n\n"
            f"ተጠቃሚዎች: {stats['total_users']}\n"
            f"ጠቅላላ ቀሪ: {stats['total_balance']/100:.2f} ብር\n"
            f"በመጠባበቅ ላይ ያሉ ክፍያዎች: {pending_payments}\n"
            f"በመጠባበቅ ላይ ያሉ ማውጫዎች: {pending_withdrawals}\n"
            f"በመጠባበቅ ላይ ያሉ ማስተዋወቂያዎች: {stats.get('pending_referrals', 0)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 የሚጠባበቁ ክፍያዎች", callback_data="admin_pending_payments")],
                [InlineKeyboardButton("💸 የሚጠባበቁ ማውጫዎች", callback_data="admin_pending_withdrawals")],
                [InlineKeyboardButton("◀️ ተመለስ", callback_data="menu")]
            ])
        )
    elif data == "admin_pending_payments" and str(user.id) == str(ADMIN_USER_ID):
        pending = db.get_pending_payment_requests(limit=10)
        if not pending:
            await query.edit_message_text("📊 ምንም የሚጠባበቁ ክፍያዎች የሉም።", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ ተመለስ", callback_data="admin")
            ]]))
            return
        text = "📊 የሚጠባበቁ ክፍያዎች:\n\n"
        for p in pending:
            text += f"🆔 {p['request_id']}\n👤 {p['first_name']} (@{p.get('username','N/A')})\n💰 {p['amount']/100:.2f} ብር\n\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ተመለስ", callback_data="admin")
        ]]))
    elif data == "admin_pending_withdrawals" and str(user.id) == str(ADMIN_USER_ID):
        pending = db.get_pending_withdrawal_requests(limit=10)
        if not pending:
            await query.edit_message_text("📊 ምንም የሚጠባበቁ ማውጫዎች የሉም።", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ ተመለስ", callback_data="admin")
            ]]))
            return
        text = "💸 የሚጠባበቁ ማውጫዎች:\n\n"
        for w in pending:
            text += f"🆔 {w['request_id']}\n👤 {w['first_name']} (@{w.get('username','N/A')})\n💰 {w['amount']/100:.2f} ብር\n📱 {w['phone_number']}\n\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ተመለስ", callback_data="admin")
        ]]))
    elif data == "menu":
        user_data = db.get_user(user.id)
        balance = user_data['balance'] / 100 if user_data else 0
        keyboard = [
            [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
            [InlineKeyboardButton("💰 Balance", callback_data="balance")],
            [InlineKeyboardButton("💳 Deposit", callback_data="deposit_start")],
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_start")],
            [InlineKeyboardButton("🎁 Referral", callback_data="referral")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        if str(user.id) == str(ADMIN_USER_ID):
            keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])
        await query.edit_message_text(
            f"🎯 ዋና ሜኑ\n💰 ቀሪ ሂሳብ: {balance:.2f} ብር",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ==================== Approval Handlers ====================

async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_payment_request(request_id)
    if not request:
        await query.edit_message_text("❌ የክፍያ ጥያቄ አልተገኘም")
        return
    
    db.update_payment_request_status(request_id, 'completed', 'Approved by admin')
    result = db.update_balance(request['user_id'], request['amount'], 'deposit', f'Payment approved - {request_id}')
    
    if result:
        await context.bot.send_message(
            request['user_id'],
            f"✅ ገንዘብ መሙላት ጸድቋል!\n\n"
            f"የእርስዎ {request['amount']/100:.2f} ብር ጸድቋል።\n"
            f"አዲስ ቀሪ ሂሳብ: {result['new_balance']/100:.2f} ብር\n\n"
            f"💡 አሁን ማውጣት ይችላሉ! /withdraw ይጠቀሙ"
        )
        
        bonus_paid = db.check_and_pay_referral_bonus(request['user_id'])
        if bonus_paid:
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (request['user_id'],))
                referrer_id = cursor.fetchone()['referred_by']
                
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎁 **የማስተዋወቂያ ቦነስ!** 🎁\n\n"
                         f"የጋበዙት ሰው (ID: {request['user_id']}) የመጀመሪያ ገንዘባቸውን ሞልተዋል!\n"
                         f"እርስዎ **5 ብር** ቦነስ አግኝተዋል!",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Failed to notify referrer: {e}")
            finally:
                conn.close()
        
        admin_msg = context.bot_data.get(f"admin_msg_{request_id}")
        if admin_msg:
            try:
                await context.bot.edit_message_reply_markup(
                    admin_msg['chat_id'], 
                    admin_msg['message_id'], 
                    reply_markup=None
                )
            except Exception as e:
                logger.error(f"Failed to remove admin buttons: {e}")
        
        await query.edit_message_text(
            f"✅ ገንዘብ መሙላት ጸድቋል\n\n"
            f"የጥያቄ መለያ: {request_id}\n"
            f"መጠን: {request['amount']/100:.2f} ብር"
        )
    else:
        await query.edit_message_text("❌ ቀሪ ሂሳብ ማዘመን አልተሳካም")

async def reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_payment_request(request_id)
    if not request:
        await query.edit_message_text("❌ የክፍያ ጥያቄ አልተገኘም")
        return
    db.update_payment_request_status(request_id, 'rejected', 'Rejected by admin')
    await context.bot.send_message(
        request['user_id'], 
        f"❌ ክፍያ ውድቅ ሆኗል\n\nየእርስዎ {request['amount']/100:.2f} ብር ክፍያ ውድቅ ሆኗል።\nእባክዎ አስተዳዳሪውን ያግኙ።"
    )
    admin_msg = context.bot_data.get(f"admin_msg_{request_id}")
    if admin_msg:
        try:
            await context.bot.edit_message_reply_markup(admin_msg['chat_id'], admin_msg['message_id'], reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to remove admin buttons: {e}")
    await query.edit_message_text(f"❌ ክፍያ ውድቅ ሆኗል\n\nየጥያቄ መለያ: {request_id}")

async def approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_withdrawal_request(request_id)
    if not request:
        await query.edit_message_text("❌ የማውጫ ጥያቄ አልተገኘም")
        return
    user = db.get_user(request['user_id'])
    if not user or user['balance'] < request['amount']:
        await query.edit_message_text("❌ ለዚህ ማውጫ በቂ ገንዘብ የለም")
        return
    db.update_balance(request['user_id'], -request['amount'], 'withdrawal', f'Withdrawal approved - {request_id}')
    db.update_withdrawal_request_status(request_id, 'completed', 'Approved by admin')
    await context.bot.send_message(
        request['user_id'],
        f"✅ ማውጣት ጸድቋል!\n\nመጠን: {request['amount']/100:.2f} ብር\nስልክ: {request['phone_number']}\nገንዘብዎ በቅርቡ ይላካል።"
    )
    admin_msg = context.bot_data.get(f"admin_withdraw_msg_{request_id}")
    if admin_msg:
        try:
            await context.bot.edit_message_reply_markup(admin_msg['chat_id'], admin_msg['message_id'], reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to remove admin buttons: {e}")
    await query.edit_message_text(
        f"✅ ማውጫ ጸድቋል\n\n"
        f"የጥያቄ መለያ: {request_id}\n"
        f"መጠን: {request['amount']/100:.2f} ብር\n"
        f"ስልክ: {request['phone_number']}"
    )

async def reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: str):
    query = update.callback_query
    request = db.get_withdrawal_request(request_id)
    if not request:
        await query.edit_message_text("❌ የማውጫ ጥያቄ አልተገኘም")
        return
    db.update_withdrawal_request_status(request_id, 'rejected', 'Rejected by admin')
    await context.bot.send_message(
        request['user_id'],
        f"❌ ማውጣት ውድቅ ሆኗል\n\nየእርስዎ {request['amount']/100:.2f} ብር ማውጫ ጥያቄ ውድቅ ሆኗል።\nእባክዎ አስተዳዳሪውን ያግኙ።"
    )
    admin_msg = context.bot_data.get(f"admin_withdraw_msg_{request_id}")
    if admin_msg:
        try:
            await context.bot.edit_message_reply_markup(admin_msg['chat_id'], admin_msg['message_id'], reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to remove admin buttons: {e}")
    await query.edit_message_text(f"❌ ማውጫ ውድቅ ሆኗል\n\nየጥያቄ መለያ: {request_id}")

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
    application.add_handler(CommandHandler("start", start_with_referral))
    application.add_handler(CommandHandler("refer", referral_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("cancel", deposit_cancel))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("startroom2", start_room2_command))
    application.add_handler(CommandHandler("setpattern", set_pattern_command))
    application.add_handler(CommandHandler("setprice", set_room_price_command))
    application.add_handler(CommandHandler("patterns", list_patterns_command))
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

# ==================== Web App Routes ====================

@app.get("/rooms", response_class=HTMLResponse)
async def rooms_page(request: Request, user_id: int):
    user = db.get_or_create_user(user_id)
    return templates.TemplateResponse("rooms.html", {
        "request": request,
        "user_id": user_id,
        "balance": user['balance'] / 100,
        "price_room1": CARD_PRICE_ROOM1 / 100,
        "price_room2": CARD_PRICE_ROOM2 / 100,
        "price_room3": CARD_PRICE_ROOM3 / 100,
        "bot_username": BOT_USERNAME
    })

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    user = db.get_or_create_user(user_id)
    pattern = game_manager.room_patterns.get(game_id, "አልተመረጠም")
    price = game_manager.room_price.get(game_id, 1000) / 100
    return templates.TemplateResponse("bingo.html", {
        "request": request,
        "user_id": user_id,
        "game_id": game_id,
        "pattern": pattern,
        "admin_id": ADMIN_USER_ID,
        "price_per_card": price,
        "max_cards": MAX_CARDS_PER_PLAYER,
        "initial_balance": user['balance'] / 100,
        "initial_active_games": db.get_active_games_count(user_id),
        "initial_stake": db.get_total_stake(user_id) / 100,
        "auto_start_delay": AUTO_START_DELAY,
        "manual_marking": True  # Tell frontend that manual marking is required
    })

# ==================== API Endpoints ====================

@app.get("/api/room-stats")
async def get_room_stats():
    stats = {}
    for room_id in [1, 2, 3]:
        if room_id in game_manager.active_games:
            total_cards = game_manager.active_games[room_id]['total_cards_sold']
            player_count = len(game_manager.active_games[room_id]['players'])
            game_started = game_manager.game_started.get(room_id, False)
            pattern = game_manager.room_patterns.get(room_id, "አልተመረጠም")
        else:
            total_cards = 0
            player_count = 0
            game_started = False
            pattern = "አልተመረጠም"
        
        stats[room_id] = {
            "total_cards": total_cards,
            "players": player_count,
            "game_started": game_started,
            "pattern": pattern
        }
    return stats

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, game_manager.bot_app.bot)
    await game_manager.bot_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "online", "cards": len(BINGO_CARDS), "price_per_card": CARD_PRICE_ROOM1 / 100, "max_cards_per_player": MAX_CARDS_PER_PLAYER}

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

# ==================== WebSocket Endpoint with Manual Marking Support ====================

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
                await websocket.send_json({
                    'type': 'cards_selected', 
                    'success': success, 
                    'message': msg, 
                    'cost': cost, 
                    'new_balance': new_bal, 
                    'card_ids': data['card_ids'] if success else []
                })
                if success:
                    for card_id in data['card_ids']:
                        card = next(c for c in BINGO_CARDS if c['id'] == card_id)
                        await websocket.send_json({
                            'type': 'your_card', 
                            'card': card['card'], 
                            'card_id': card_id,
                            'marked': [],
                            'suspended': False
                        })
            
            elif data['type'] == 'mark_number':
                # Player manually marks a called number
                result = await game_manager.handle_mark_number(
                    game_id, user_id, data['card_id'], data['number']
                )
                
                await websocket.send_json({
                    'type': 'mark_result',
                    **result
                })
                
                # If pattern completed, highlight the bingo button
                if result.get('pattern_completed'):
                    await websocket.send_json({
                        'type': 'pattern_completed',
                        'card_id': data['card_id'],
                        'message': result['message']
                    })
            
            elif data['type'] == 'claim_bingo':
                card_id = data.get('card_id')
                if not card_id:
                    await websocket.send_json({
                        'type': 'error',
                        'message': 'No card specified'
                    })
                    continue
                
                # Check if this is a valid win using only manually marked numbers
                is_winner = await game_manager.check_winner_with_manual_marks(
                    game_id, user_id, card_id
                )
                
                if is_winner:
                    # Valid bingo
                    logger.info(f"Valid bingo by user {user_id} on card {card_id}")
                    winners = [(user_id, card_id)]
                    game_manager.stop_number_generation[game_id] = True
                    await game_manager.finish_round_multi(game_id, winners)
                else:
                    # False bingo - suspend a card
                    logger.info(f"False bingo by user {user_id} on card {card_id}")
                    success, message, status = await game_manager.handle_false_bingo(
                        game_id, user_id, card_id
                    )
                    
                    await websocket.send_json({
                        'type': 'false_bingo_result',
                        'success': success,
                        'message': message,
                        'claimed_card_id': card_id,
                        'new_status': status
                    })
            
            elif data['type'] == 'false_bingo':
                # Handle false bingo from client (backup)
                card_id = data.get('card_id')
                logger.info(f"False bingo report from user {user_id} on card {card_id}")
                success, message, status = await game_manager.handle_false_bingo(
                    game_id, user_id, card_id
                )
                await websocket.send_json({
                    'type': 'false_bingo_result',
                    'success': success,
                    'message': message,
                    'claimed_card_id': card_id,
                    'new_status': status
                })
            
            elif data['type'] == 'get_card_status':
                status = await game_manager.get_player_card_status(game_id, user_id)
                await websocket.send_json({
                    'type': 'card_status',
                    'status': status
                })
            
            elif data['type'] == 'heartbeat':
                await websocket.send_json({'type': 'heartbeat_ack'})
            
            elif data['type'] == 'ping':
                await websocket.send_json({'type': 'pong'})
                
    except WebSocketDisconnect:
        await game_manager.disconnect(game_id, websocket, user_id)
        logger.info(f"User {user_id} disconnected from room {game_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await game_manager.disconnect(game_id, websocket, user_id)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)