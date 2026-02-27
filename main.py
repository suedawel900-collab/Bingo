import sqlite3
import json
import random
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import asyncio
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ==========================
# DATABASE
# ==========================

conn = sqlite3.connect("bingo.db", check_same_thread=False)
cursor = conn.cursor()

PRICE_PER_CARD = 10
HOUSE_COMMISSION_PERCENT = 20
ADMIN_ID = int(os.getenv('ADMIN_ID', '123456789'))  # 🔥 Set your Telegram admin ID
BOT_TOKEN = "8578474198:AAGcqcyTihBMxV-gtqukkbU_SBk1EszG-7w"
PUBLIC_URL = os.getenv('PUBLIC_URL', 'https://your-domain.com')  # Set your public URL

bot_app = None

# ==========================
# CREATE TABLES (with pattern support)
# ==========================

cursor.execute("""
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    positions TEXT NOT NULL  -- JSON array of [row, col] pairs
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round INTEGER DEFAULT 1,
    started INTEGER DEFAULT 0,
    called_numbers TEXT DEFAULT '[]',
    pattern_id INTEGER DEFAULT 1,
    FOREIGN KEY (pattern_id) REFERENCES patterns(id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER,
    user_id INTEGER,
    card_id INTEGER,
    card_data TEXT,
    marked_numbers TEXT DEFAULT '[]'
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0,
    wins INTEGER DEFAULT 0
)
""")

conn.commit()

# ==========================
# LOAD / GENERATE 100 PATTERNS
# ==========================

def generate_patterns():
    patterns = []
    
    # Basic patterns (1-20)
    basic_patterns = [
        {"name": "Full House", "description": "Mark all numbers on your card", "positions": "full_house"},
        {"name": "Four Corners", "description": "Mark the four corner squares", "positions": [[0,0], [0,4], [4,0], [4,4]]},
        {"name": "X Pattern", "description": "Mark both diagonals", "positions": [[0,0], [1,1], [2,2], [3,3], [4,4], [0,4], [1,3], [3,1], [4,0]]},
        {"name": "Plus Sign", "description": "Mark middle row and middle column", "positions": [[2,0], [2,1], [2,2], [2,3], [2,4], [0,2], [1,2], [3,2], [4,2]]},
        {"name": "Top Row", "description": "Mark the entire top row", "positions": [[0,0], [0,1], [0,2], [0,3], [0,4]]},
        {"name": "Middle Row", "description": "Mark the entire middle row", "positions": [[2,0], [2,1], [2,2], [2,3], [2,4]]},
        {"name": "Bottom Row", "description": "Mark the entire bottom row", "positions": [[4,0], [4,1], [4,2], [4,3], [4,4]]},
        {"name": "First Column", "description": "Mark the entire first column", "positions": [[0,0], [1,0], [2,0], [3,0], [4,0]]},
        {"name": "Middle Column", "description": "Mark the entire middle column", "positions": [[0,2], [1,2], [2,2], [3,2], [4,2]]},
        {"name": "Last Column", "description": "Mark the entire last column", "positions": [[0,4], [1,4], [2,4], [3,4], [4,4]]},
        {"name": "Small Diamond", "description": "Mark a diamond shape in the center", "positions": [[1,2], [2,1], [2,2], [2,3], [3,2]]},
        {"name": "Big Diamond", "description": "Mark a large diamond shape", "positions": [[0,2], [1,1], [1,3], [2,0], [2,4], [3,1], [3,3], [4,2]]},
        {"name": "Letter L", "description": "Mark L shape (top row and last column)", "positions": [[0,0], [0,1], [0,2], [0,3], [0,4], [1,4], [2,4], [3,4], [4,4]]},
        {"name": "Letter T", "description": "Mark T shape", "positions": [[0,0], [0,1], [0,2], [0,3], [0,4], [1,2], [2,2], [3,2], [4,2]]},
        {"name": "Letter U", "description": "Mark U shape", "positions": [[0,0], [0,4], [1,0], [1,4], [2,0], [2,4], [3,0], [3,4], [4,0], [4,1], [4,2], [4,3], [4,4]]},
        {"name": "Frame", "description": "Mark the outer border", "positions": [[0,0], [0,1], [0,2], [0,3], [0,4], [1,0], [1,4], [2,0], [2,4], [3,0], [3,4], [4,0], [4,1], [4,2], [4,3], [4,4]]},
        {"name": "Checkerboard", "description": "Mark alternating squares", "positions": [[0,0], [0,2], [0,4], [1,1], [1,3], [2,0], [2,2], [2,4], [3,1], [3,3], [4,0], [4,2], [4,4]]},
        {"name": "Zigzag", "description": "Mark a zigzag pattern", "positions": [[0,0], [0,1], [1,1], [1,2], [2,2], [2,3], [3,3], [3,4], [4,4]]},
        {"name": "Spiral", "description": "Mark a spiral pattern", "positions": [[0,0], [0,1], [0,2], [0,3], [0,4], [1,4], [2,4], [3,4], [4,4], [4,3], [4,2], [4,1], [4,0], [3,0], [2,0], [1,0], [1,1], [1,2], [1,3], [2,3], [3,3], [3,2], [3,1], [2,1], [2,2]]},
        {"name": "Smiley Face", "description": "Mark a smiley face pattern", "positions": [[1,1], [1,3], [3,0], [3,1], [3,2], [3,3], [3,4], [4,2]]}
    ]
    
    # Generate variations (21-100)
    patterns.extend(basic_patterns)
    
    # Add variations of rows, columns, and diagonals
    for i in range(20, 100):
        pattern_type = random.choice(["row", "column", "diagonal", "cross", "letter", "shape"])
        if pattern_type == "row":
            row = random.randint(0, 4)
            name = f"Row {row + 1} Variant {i}"
            description = f"Mark row {row + 1} with a twist"
            positions = [[row, col] for col in range(5)]
        elif pattern_type == "column":
            col = random.randint(0, 4)
            name = f"Column {col + 1} Variant {i}"
            description = f"Mark column {col + 1} with a twist"
            positions = [[row, col] for row in range(5)]
        elif pattern_type == "diagonal":
            name = f"Diagonal Variant {i}"
            description = "Mark a diagonal pattern"
            positions = [[j, j] for j in range(5)] + [[j, 4-j] for j in range(5)]
        elif pattern_type == "cross":
            name = f"Cross Variant {i}"
            description = "Mark a cross pattern"
            positions = [[2, j] for j in range(5)] + [[j, 2] for j in range(5)]
        else:
            # Random shape
            name = f"Random Shape {i}"
            description = "Mark a random pattern"
            positions = []
            for _ in range(random.randint(5, 15)):
                positions.append([random.randint(0, 4), random.randint(0, 4)])
            # Remove duplicates
            positions = [list(x) for x in set(tuple(pos) for pos in positions)]
        
        patterns.append({
            "name": name,
            "description": description,
            "positions": positions
        })
    
    return patterns[:100]  # Ensure exactly 100 patterns

def init_patterns():
    cursor.execute("SELECT COUNT(*) FROM patterns")
    count = cursor.fetchone()[0]
    if count == 0:
        patterns = generate_patterns()
        for p in patterns:
            positions = p["positions"]
            if isinstance(positions, str):
                positions_json = json.dumps({"type": positions})
            else:
                positions_json = json.dumps(positions)
            cursor.execute("INSERT INTO patterns (name, description, positions) VALUES (?, ?, ?)",
                           (p["name"], p["description"], positions_json))
        conn.commit()
        logger.info(f"✅ Inserted {len(patterns)} patterns.")

init_patterns()

# ==========================
# TELEGRAM BOT HANDLERS
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when /start is issued."""
    user = update.effective_user
    welcome_message = (
        f"👋 Welcome to Bingo Game Bot, {user.first_name}!\n\n"
        "🎮 This bot allows you to play Bingo with friends.\n\n"
        "Commands:\n"
        "/play - Start playing Bingo\n"
        "/balance - Check your balance\n"
        "/help - Show this help message"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when /help is issued."""
    help_text = (
        "📖 *Bingo Game Help*\n\n"
        "*How to Play:*\n"
        "1. Use /play to start a game\n"
        "2. Buy cards using the buttons\n"
        "3. Wait for numbers to be called\n"
        "4. Mark numbers on your card\n"
        "5. Shout BINGO when you win!\n\n"
        "*Commands:*\n"
        "/start - Start the bot\n"
        "/play - Play Bingo\n"
        "/balance - Check your balance\n"
        "/help - Show this help"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start playing Bingo"""
    user_id = update.effective_user.id
    
    # Create inline keyboard for game options
    keyboard = [
        [InlineKeyboardButton("🎫 Buy 1 Card (10 ETB)", callback_data="buy_1")],
        [InlineKeyboardButton("🎫 Buy 3 Cards (30 ETB)", callback_data="buy_3")],
        [InlineKeyboardButton("🎫 Buy 5 Cards (50 ETB)", callback_data="buy_5")],
        [InlineKeyboardButton("🎮 Open Web Game", url=f"{PUBLIC_URL}/game?user_id={user_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎮 *Bingo Game*\n\n"
        "Choose an option:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user balance"""
    user_id = update.effective_user.id
    
    # Get balance from database
    cursor.execute("SELECT balance, wins FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    
    if user:
        balance_amount, wins = user
        await update.message.reply_text(
            f"💰 *Your Balance*\n\n"
            f"Balance: {balance_amount} ETB\n"
            f"Wins: {wins}\n"
            f"Card Price: 10 ETB",
            parse_mode='Markdown'
        )
    else:
        # Create new user
        cursor.execute("INSERT INTO users (id, balance) VALUES (?, ?)", (user_id, 10.0))
        conn.commit()
        await update.message.reply_text(
            "💰 *Your Balance*\n\n"
            f"Balance: 10.0 ETB\n"
            f"Wins: 0\n"
            f"Card Price: 10 ETB",
            parse_mode='Markdown'
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "play":
        # Create inline keyboard for game options
        keyboard = [
            [InlineKeyboardButton("🎫 Buy 1 Card (10 ETB)", callback_data="buy_1")],
            [InlineKeyboardButton("🎫 Buy 3 Cards (30 ETB)", callback_data="buy_3")],
            [InlineKeyboardButton("🎫 Buy 5 Cards (50 ETB)", callback_data="buy_5")],
            [InlineKeyboardButton("🎮 Open Web Game", url=f"{PUBLIC_URL}/game?user_id={user_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🎮 *Bingo Game*\n\n"
            "Choose an option:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    elif data == "balance":
        # Get balance from database
        cursor.execute("SELECT balance, wins FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if user:
            balance_amount, wins = user
            await query.edit_message_text(
                f"💰 *Your Balance*\n\n"
                f"Balance: {balance_amount} ETB\n"
                f"Wins: {wins}\n"
                f"Card Price: 10 ETB",
                parse_mode='Markdown'
            )
        else:
            # Create new user
            cursor.execute("INSERT INTO users (id, balance) VALUES (?, ?)", (user_id, 10.0))
            conn.commit()
            await query.edit_message_text(
                "💰 *Your Balance*\n\n"
                f"Balance: 10.0 ETB\n"
                f"Wins: 0\n"
                f"Card Price: 10 ETB",
                parse_mode='Markdown'
            )
    elif data == "help":
        help_text = (
            "📖 *Bingo Game Help*\n\n"
            "*How to Play:*\n"
            "1. Use /play to start a game\n"
            "2. Buy cards using the buttons\n"
            "3. Wait for numbers to be called\n"
            "4. Mark numbers on your card\n"
            "5. Shout BINGO when you win!\n\n"
            "*Commands:*\n"
            "/start - Start the bot\n"
            "/play - Play Bingo\n"
            "/balance - Check your balance\n"
            "/help - Show this help"
        )
        await query.edit_message_text(help_text, parse_mode='Markdown')
    elif data.startswith("buy_"):
        count = int(data.split("_")[1])
        
        # Check if user has enough balance
        cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("INSERT INTO users (id, balance) VALUES (?, ?)", (user_id, 10.0))
            conn.commit()
            current_balance = 10.0
        else:
            current_balance = user[0]
        
        total_cost = count * 10
        
        if current_balance < total_cost:
            await query.edit_message_text(
                f"❌ Insufficient balance!\n\n"
                f"Your balance: {current_balance} ETB\n"
                f"Need: {total_cost} ETB\n\n"
                f"Use /balance to check your balance."
            )
            return
        
        # Deduct balance
        new_balance = current_balance - total_cost
        cursor.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user_id))
        conn.commit()
        
        # Generate game link
        game_url = f"{PUBLIC_URL}/game?user_id={user_id}"
        
        await query.edit_message_text(
            f"✅ *{count} Card(s) Purchased!*\n\n"
            f"Total cost: {total_cost} ETB\n"
            f"Remaining balance: {new_balance} ETB\n\n"
            f"Click below to start playing:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Open Game", url=game_url)
            ]]),
            parse_mode='Markdown'
        )

async def setup_bot():
    """Initialize and start the Telegram bot"""
    global bot_app
    
    # Create application
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("help", help_command))
    bot_app.add_handler(CommandHandler("play", play))
    bot_app.add_handler(CommandHandler("balance", balance))
    bot_app.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    
    logger.info("🤖 Telegram bot started successfully")
    
    return bot_app

async def shutdown_bot():
    """Shutdown the Telegram bot gracefully"""
    global bot_app
    if bot_app:
        logger.info("🛑 Stopping bot...")
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()

# ==========================
# HELPER FUNCTIONS
# ==========================

def get_game_state(game_id: int):
    cursor.execute("SELECT round, started, called_numbers, pattern_id FROM games WHERE id = ?", (game_id,))
    row = cursor.fetchone()
    if not row:
        return None
    round_number, started, called_numbers, pattern_id = row
    cursor.execute("SELECT card_id FROM user_cards WHERE game_id = ?", (game_id,))
    taken_cards = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT name, description FROM patterns WHERE id = ?", (pattern_id,))
    pattern = cursor.fetchone()
    pattern_name = pattern[0] if pattern else "Standard"
    return {
        "round": round_number,
        "started": bool(started),
        "called_numbers": json.loads(called_numbers),
        "taken_cards": taken_cards,
        "pattern_id": pattern_id,
        "pattern_name": pattern_name
    }

def calculate_prize_pool(game_id: int):
    cursor.execute("SELECT COUNT(*) FROM user_cards WHERE game_id = ?", (game_id,))
    total_cards = cursor.fetchone()[0]
    return total_cards * PRICE_PER_CARD

def pay_winner(game_id: int, winner_id: int):
    prize_pool = calculate_prize_pool(game_id)
    if prize_pool <= 0:
        logger.warning("⚠ Prize pool is 0")
        return 0

    house_cut = round(prize_pool * HOUSE_COMMISSION_PERCENT / 100, 2)
    winner_prize = round(prize_pool - house_cut, 2)

    cursor.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE id = ?", (winner_prize, winner_id))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (house_cut, ADMIN_ID))
    conn.commit()

    logger.info(f"🏆 Winner {winner_id} received {winner_prize} ETB")
    return winner_prize

def reset_round(game_id: int):
    cursor.execute("UPDATE games SET round = round + 1, started = 0, called_numbers = '[]' WHERE id = ?", (game_id,))
    cursor.execute("DELETE FROM user_cards WHERE game_id = ?", (game_id,))
    conn.commit()
    logger.info(f"🔄 Round reset for game {game_id}")

def check_bingo(card_data, called_numbers_set, pattern_data):
    """
    Check if the card has achieved bingo based on the pattern
    """
    try:
        # Parse card data
        if isinstance(card_data, str):
            card = json.loads(card_data)
        else:
            card = card_data
            
        # Parse pattern positions
        if isinstance(pattern_data, str):
            pattern = json.loads(pattern_data)
        else:
            pattern = pattern_data
            
        # Handle special pattern types
        if isinstance(pattern, dict) and "type" in pattern:
            pattern_type = pattern["type"]
            
            if pattern_type == "full_house":
                # All numbers on card must be called
                all_numbers = set()
                for row in card:
                    for num in row:
                        if num != "FREE":  # Skip FREE space
                            all_numbers.add(str(num))
                return all_numbers.issubset(called_numbers_set)
                
            else:
                # Default to using positions from pattern
                positions = pattern.get("positions", [])
        else:
            # Direct positions array
            positions = pattern
            
        # Check each required position
        for pos in positions:
            row, col = pos
            num = card[row][col]
            if num != "FREE" and str(num) not in called_numbers_set:
                return False
                
        return True
        
    except Exception as e:
        logger.error(f"Error checking bingo: {e}")
        return False

# ==========================
# API ENDPOINTS
# ==========================

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {"message": "Bingo Game Server", "status": "running"}

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    # Ensure user exists
    cursor.execute("INSERT OR IGNORE INTO users (id, balance) VALUES (?, ?)", (user_id, 10.0))
    conn.commit()
    cursor.execute("SELECT balance, wins FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    balance = user[0] if user else 10.0
    wins = user[1] if user else 0

    # Ensure game exists
    cursor.execute("INSERT OR IGNORE INTO games (id, pattern_id) VALUES (?, 1)", (game_id,))
    conn.commit()

    return templates.TemplateResponse("bingo.html", {
        "request": request,
        "user_id": user_id,
        "game_id": game_id,
        "admin_id": ADMIN_ID,
        "price_per_card": PRICE_PER_CARD,
        "max_cards": 20,
        "initial_balance": balance,
        "initial_active_games": 0,
        "initial_stake": 0
    })

@app.get("/api/game/{game_id}/state")
async def game_state_api(game_id: int):
    return get_game_state(game_id)

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    cursor.execute("SELECT balance, wins FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        return {"balance": row[0], "wins": row[1]}
    return {"balance": 10.0, "wins": 0}

@app.get("/api/patterns")
async def list_patterns():
    cursor.execute("SELECT id, name, description FROM patterns ORDER BY id")
    rows = cursor.fetchall()
    return [{"id": r[0], "name": r[1], "description": r[2]} for r in rows]

# ==========================
# WEBSOCKET
# ==========================

connections = {}

def generate_bingo_card():
    """Generate a random 5x5 bingo card"""
    card = []
    for i in range(5):
        row = []
        for j in range(5):
            # BINGO columns: B(1-15), I(16-30), N(31-45), G(46-60), O(61-75)
            if j == 0:  # B column
                num = random.randint(1, 15)
            elif j == 1:  # I column
                num = random.randint(16, 30)
            elif j == 2:  # N column
                num = random.randint(31, 45)
            elif j == 3:  # G column
                num = random.randint(46, 60)
            else:  # O column
                num = random.randint(61, 75)
            row.append(num)
        card.append(row)
    # Set middle as FREE
    card[2][2] = "FREE"
    return card

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    await websocket.accept()
    logger.info(f"WebSocket connected: game={game_id}, user={user_id}")

    if game_id not in connections:
        connections[game_id] = []
    connections[game_id].append(websocket)

    # Ensure game exists (again, just in case)
    cursor.execute("INSERT OR IGNORE INTO games (id, pattern_id) VALUES (?, 1)", (game_id,))
    conn.commit()

    # Send initial state
    state = get_game_state(game_id)
    if state:
        await websocket.send_json({"type": "connected", **state})
    else:
        logger.error(f"Game {game_id} not found after creation?")
        await websocket.send_json({"type": "error", "message": "Game not found"})
        connections[game_id].remove(websocket)
        return

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            logger.info(f"Received {msg_type} from user {user_id} in game {game_id}")

            # ---------- SELECT CARDS ----------
            if msg_type == "select_cards":
                count = data.get("count", 1)
                logger.info(f"User {user_id} selecting {count} cards")
                
                # Check if user has enough balance
                cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
                user_balance = cursor.fetchone()
                if not user_balance or user_balance[0] < count * PRICE_PER_CARD:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Insufficient balance. Need {count * PRICE_PER_CARD} ETB"
                    })
                    continue
                
                # Check if game has started
                state = get_game_state(game_id)
                if state and state["started"]:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Game already started. Cannot buy cards now."
                    })
                    continue
                
                # Generate cards
                cards = []
                for _ in range(count):
                    card_id = random.randint(100000, 999999)
                    card_data = generate_bingo_card()
                    cards.append({
                        "id": card_id,
                        "data": card_data
                    })
                    
                    # Save to database
                    cursor.execute("""
                        INSERT INTO user_cards (game_id, user_id, card_id, card_data, marked_numbers)
                        VALUES (?, ?, ?, ?, ?)
                    """, (game_id, user_id, card_id, json.dumps(card_data), "[]"))
                
                # Deduct balance
                new_balance = user_balance[0] - (count * PRICE_PER_CARD)
                cursor.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user_id))
                conn.commit()
                
                # Send response
                await websocket.send_json({
                    "type": "cards_selected",
                    "cards": cards,
                    "new_balance": new_balance
                })
                
                # Broadcast to admin that new cards were purchased
                for ws in connections.get(game_id, []):
                    try:
                        await ws.send_json({
                            "type": "cards_purchased",
                            "user_id": user_id,
                            "count": count
                        })
                    except:
                        pass

            # ---------- CALL NUMBER ----------
            elif msg_type == "call_number":
                # Only admin can call numbers
                if user_id != ADMIN_ID:
                    logger.warning(f"Non-admin {user_id} attempted to call number in game {game_id}")
                    await websocket.send_json({
                        "type": "error",
                        "message": "Only admin can call numbers"
                    })
                    continue
                
                number = data.get("number")
                if not number:
                    continue
                
                # Get current game state
                state = get_game_state(game_id)
                if not state or not state["started"]:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Game not started"
                    })
                    continue
                
                # Check if number already called
                called_numbers = state["called_numbers"]
                if number in called_numbers:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Number {number} already called"
                    })
                    continue
                
                # Add number to called numbers
                called_numbers.append(number)
                cursor.execute("UPDATE games SET called_numbers = ? WHERE id = ?",
                              (json.dumps(called_numbers), game_id))
                conn.commit()
                
                # Broadcast to all clients
                for ws in connections.get(game_id, []):
                    try:
                        await ws.send_json({
                            "type": "number_called",
                            "number": number,
                            "called_numbers": called_numbers
                        })
                    except:
                        pass
                
                # Check for winners
                await check_for_winner(game_id)

            # ---------- SET PATTERN ----------
            elif msg_type == "set_pattern":
                # Only admin can set pattern
                if user_id != ADMIN_ID:
                    logger.warning(f"Non-admin {user_id} attempted to set pattern in game {game_id}")
                    await websocket.send_json({
                        "type": "error",
                        "message": "Only admin can set pattern"
                    })
                    continue
                
                pattern_id = data.get("pattern_id")
                if not pattern_id:
                    continue
                
                # Update game pattern
                cursor.execute("UPDATE games SET pattern_id = ? WHERE id = ?", (pattern_id, game_id))
                conn.commit()
                
                # Get pattern info
                cursor.execute("SELECT name, description FROM patterns WHERE id = ?", (pattern_id,))
                pattern = cursor.fetchone()
                
                # Broadcast to all clients
                for ws in connections.get(game_id, []):
                    try:
                        await ws.send_json({
                            "type": "pattern_updated",
                            "pattern_id": pattern_id,
                            "pattern_name": pattern[0] if pattern else "Unknown"
                        })
                    except:
                        pass

            # ---------- START GAME ----------
            elif msg_type == "start_game":
                # Check admin
                if user_id != ADMIN_ID:
                    logger.warning(f"Non-admin {user_id} attempted to start game {game_id}")
                    await websocket.send_json({"type": "error", "message": "You are not authorized to start the game"})
                    continue

                try:
                    # Update database
                    cursor.execute("UPDATE games SET started = 1 WHERE id = ?", (game_id,))
                    if cursor.rowcount == 0:
                        # Game didn't exist – insert it
                        cursor.execute("INSERT INTO games (id, started) VALUES (?, 1)", (game_id,))
                    conn.commit()
                    logger.info(f"Game {game_id} started by admin {user_id}")

                    # Broadcast to all connected clients
                    for ws in connections.get(game_id, []):
                        try:
                            await ws.send_json({"type": "game_started"})
                        except Exception as e:
                            logger.error(f"Error broadcasting to client: {e}")

                    # Confirm to admin
                    await websocket.send_json({"type": "start_game", "success": True})

                except Exception as e:
                    logger.exception(f"Error starting game {game_id}: {e}")
                    await websocket.send_json({"type": "error", "message": f"Database error: {str(e)}"})

            # ---------- WINNER (manual) ----------
            elif msg_type == "winner":
                # Only admin can declare winner
                if user_id != ADMIN_ID:
                    logger.warning(f"Non-admin {user_id} attempted to declare winner in game {game_id}")
                    await websocket.send_json({
                        "type": "error",
                        "message": "Only admin can declare winner"
                    })
                    continue
                
                winner_id = data.get("winner_id")
                card_id = data.get("card_id")
                
                if not winner_id or not card_id:
                    continue
                
                # Process win
                await handle_win(game_id, winner_id, card_id)

            # ---------- MARK NUMBER (for players) ----------
            elif msg_type == "mark_number":
                card_id = data.get("card_id")
                number = data.get("number")
                
                if not card_id or not number:
                    continue
                
                # Get user's card
                cursor.execute("""
                    SELECT marked_numbers FROM user_cards 
                    WHERE game_id = ? AND user_id = ? AND card_id = ?
                """, (game_id, user_id, card_id))
                result = cursor.fetchone()
                
                if result:
                    marked = json.loads(result[0])
                    if number not in marked:
                        marked.append(number)
                        cursor.execute("""
                            UPDATE user_cards SET marked_numbers = ? 
                            WHERE game_id = ? AND user_id = ? AND card_id = ?
                        """, (json.dumps(marked), game_id, user_id, card_id))
                        conn.commit()
                        
                        await websocket.send_json({
                            "type": "number_marked",
                            "card_id": card_id,
                            "number": number,
                            "marked_numbers": marked
                        })

            # ---------- RESET GAME ----------
            elif msg_type == "reset_game":
                # Only admin can reset
                if user_id != ADMIN_ID:
                    logger.warning(f"Non-admin {user_id} attempted to reset game {game_id}")
                    await websocket.send_json({
                        "type": "error",
                        "message": "Only admin can reset game"
                    })
                    continue
                
                reset_round(game_id)
                
                # Broadcast to all clients
                for ws in connections.get(game_id, []):
                    try:
                        await ws.send_json({
                            "type": "game_reset",
                            "message": "Game has been reset for next round"
                        })
                    except:
                        pass

            # ---------- PING ----------
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                logger.warning(f"Unknown message type: {msg_type}")

    except Exception as e:
        logger.exception(f"WebSocket error for user {user_id} in game {game_id}: {e}")
    finally:
        if websocket in connections.get(game_id, []):
            connections[game_id].remove(websocket)
        logger.info(f"WebSocket disconnected: game={game_id}, user={user_id}")

async def check_for_winner(game_id: int):
    """Check if any player has achieved bingo"""
    try:
        # Get game info
        cursor.execute("SELECT called_numbers, pattern_id FROM games WHERE id = ?", (game_id,))
        game = cursor.fetchone()
        if not game:
            return
        
        called_numbers = json.loads(game[0])
        called_numbers_set = set(str(num) for num in called_numbers)
        pattern_id = game[1]
        
        # Get pattern
        cursor.execute("SELECT positions FROM patterns WHERE id = ?", (pattern_id,))
        pattern = cursor.fetchone()
        if not pattern:
            return
        
        pattern_data = json.loads(pattern[0])
        
        # Check all user cards
        cursor.execute("SELECT user_id, card_id, card_data FROM user_cards WHERE game_id = ?", (game_id,))
        cards = cursor.fetchall()
        
        for user_id, card_id, card_data in cards:
            if check_bingo(card_data, called_numbers_set, pattern_data):
                # Found a winner!
                logger.info(f"BINGO! User {user_id} with card {card_id} in game {game_id}")
                await handle_win(game_id, user_id, card_id)
                break  # Only first winner matters
                
    except Exception as e:
        logger.error(f"Error checking for winner: {e}")

async def handle_win(game_id: int, winner_id: int, card_id: int):
    """Handle a winner"""
    try:
        # Pay winner
        prize = pay_winner(game_id, winner_id)
        
        # Get winner info
        cursor.execute("SELECT wins FROM users WHERE id = ?", (winner_id,))
        wins = cursor.fetchone()
        total_wins = wins[0] if wins else 1
        
        # Broadcast win to all clients
        for ws in connections.get(game_id, []):
            try:
                await ws.send_json({
                    "type": "bingo",
                    "winner_id": winner_id,
                    "card_id": card_id,
                    "prize": prize,
                    "message": f"Player {winner_id} wins {prize} ETB!"
                })
            except:
                pass
        
        # Reset after a delay (can be called manually by admin)
        
    except Exception as e:
        logger.error(f"Error handling win: {e}")

@app.on_event("startup")
async def startup_event():
    """Start the Telegram bot when FastAPI starts"""
    global bot_app
    try:
        bot_app = await setup_bot()
    except Exception as e:
        logger.error(f"Failed to start Telegram bot: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Stop the Telegram bot when FastAPI shuts down"""
    await shutdown_bot()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    
    # Run the server
    uvicorn.run(app, host="0.0.0.0", port=port)