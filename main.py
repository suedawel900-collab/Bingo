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
CARD_PRICE_ROOM1 = 5000        # 50.00 ETB for room 1
CARD_PRICE_ROOM2 = 10000       # 100.00 ETB for room 2
CARD_PRICE_ROOM3 = 20000        # 200.00 ETB for room 3
MAX_CARDS_PER_PLAYER = 8
WELCOME_BONUS = 1000
AUTO_START_DELAY = 30
AUTO_CALL_INTERVAL = 3  # seconds
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

# Pattern mapping for frontend
PATTERN_MAPPING = {
    "any_row": "ONE_LINE",
    "any_col": "ONE_LINE",
    "diag_tl": "DIAGONAL_MAIN",
    "diag_tr": "DIAGONAL_SECOND",
    "any_line": "ONE_LINE",
    "full_house": "FULL_HOUSE",
    "four_corners": "FOUR_CORNERS",
    "x_pattern": "X_PATTERN",
    "plus_pattern": "PLUS_PATTERN",
    "t_pattern": "T_SHAPE",
    "l_pattern": "L_SHAPE",
    "u_pattern": "CROSS",
    "frame": "OUTER_FRAME",
    "blackout": "FULL_HOUSE",
    "two_rows": "TWO_LINES",
    "two_cols": "TWO_LINES",
    "top_bottom": "TWO_LINES",
    "center_col": "CENTER",
    "b_o_cols": "TWO_LINES",
    "six_pack": "BOX"
}

def get_pattern_name(pattern_id: str) -> str:
    """Convert pattern ID to display name"""
    pattern_names = {
        "any_line": "Any Line",
        "any_row": "Any Row",
        "any_col": "Any Column",
        "diag_tl": "Diagonal TL→BR",
        "diag_tr": "Diagonal TR→BL",
        "full_house": "Full House",
        "four_corners": "Four Corners",
        "x_pattern": "X Pattern",
        "plus_pattern": "Plus Pattern",
        "t_pattern": "T Pattern",
        "l_pattern": "L Pattern",
        "u_pattern": "U Pattern",
        "frame": "Frame",
        "blackout": "Blackout",
        "two_rows": "Two Rows",
        "two_cols": "Two Columns",
        "top_bottom": "Top & Bottom",
        "center_col": "Center Column",
        "b_o_cols": "B & O Columns",
        "six_pack": "Six Pack"
    }
    return pattern_names.get(pattern_id, "Any Line")

# Payment methods with auto-approval settings
PAYMENT_METHODS = {
    "telebirr": {
        "name": "Telebirr",
        "account": "0982372677",
        "account_name": "MK Bingo",
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
        "account_name": "MK Bingo",
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
    user = update.effective_user
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
        f"ዝቅተኛ ማውጫ: <b>100 ብር</b>\n\n"
        f"እባክዎ ማውጣት የሚፈልጉትን መጠን ያስገቡ (100-{balance:.2f} ብር):",
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
            f"ዝቅተኛ ማውጫ: <b>100 ብር</b>\n\n"
            f"እባክዎ ማውጣት የሚፈልጉትን መጠን ያስገቡ (100-{balance:.2f} ብር):",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Edit failed, sending new: {e}")
        await context.bot.send_message(
            chat_id=user.id,
            text=f"💸 <b>ማውጣት</b>\n\n"
                 f"የእርስዎ ቀሪ ሂሳብ: <b>{balance:.2f} ብር</b>\n"
                 f"ዝቅተኛ ማውጫ: <b>100 ብር</b>\n\n"
                 f"እባክዎ ማውጣት የሚፈልጉትን መጠን ያስገቡ (100-{balance:.2f} ብር):",
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

        if amount < 100:
            await update.message.reply_text("❌ ዝቅተኛ ማውጫ 100 ብር ነው። እባክዎ ትክክለኛ መጠን ያስገቡ:")
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