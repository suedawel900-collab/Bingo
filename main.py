import os
import sys
import json
import asyncio
import logging
import random
import sqlite3
import time
import fcntl
import signal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, filters, ContextTypes
)

# ==================== SINGLE INSTANCE LOCK ====================
def acquire_lock():
    """Ensure only one instance of the bot runs at a time"""
    try:
        lock_file = open('/tmp/bingo_bot.lock', 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        return lock_file
    except (IOError, OSError):
        print("❌ Another instance is already running! Exiting...")
        sys.exit(1)

# Acquire lock immediately
LOCK_FILE = acquire_lock()

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv('BOT_TOKEN', '8578474198:AAGcqcyTihBMxV-gtqukkbU_SBk1EszG-7w')
BASE_URL = os.getenv('BASE_URL', 'https://bingo-production.up.railway.app')
PORT = int(os.getenv('PORT', 8080))
ADMIN_USER_ID = 8741250511  # Admin user ID from your screenshots

# Game settings
PRICE_PER_CARD = 10.0  # ETB
MAX_CARDS_PER_PLAYER = 20
HOUSE_COMMISSION = 0.20  # 20%
TOTAL_CARDS = 1000
TOTAL_NUMBERS = 75

# BINGO column ranges (standard BINGO)
BINGO_COLUMNS = [
    {'name': 'B', 'min': 1, 'max': 15},
    {'name': 'I', 'min': 16, 'max': 30},
    {'name': 'N', 'min': 31, 'max': 45},
    {'name': 'G', 'min': 46, 'max': 60},
    {'name': 'O', 'min': 61, 'max': 75}
]

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATABASE SETUP ====================
DB_PATH = 'bingo.db'

def init_db():
    """Initialize database tables"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance_etb REAL DEFAULT 0,
            total_won_etb REAL DEFAULT 0,
            total_spent_etb REAL DEFAULT 0,
            games_played INTEGER DEFAULT 0,
            games_won INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Games table
    c.execute('''
        CREATE TABLE IF NOT EXISTS games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            pattern_id INTEGER,
            winner_id INTEGER,
            prize_pool_etb REAL,
            house_fee_etb REAL,
            status TEXT DEFAULT 'waiting'
        )
    ''')
    
    # Game players table
    c.execute('''
        CREATE TABLE IF NOT EXISTS game_players (
            game_id INTEGER,
            user_id INTEGER,
            card_ids TEXT,
            ready BOOLEAN DEFAULT 0,
            won BOOLEAN DEFAULT 0,
            FOREIGN KEY (game_id) REFERENCES games(game_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Cards table
    c.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            card_id INTEGER PRIMARY KEY,
            card_data TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # User cards table (for tracking which cards belong to which user)
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_cards (
            user_id INTEGER,
            game_id INTEGER,
            card_id INTEGER,
            marked_numbers TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (game_id) REFERENCES games(game_id),
            FOREIGN KEY (card_id) REFERENCES cards(card_id)
        )
    ''')
    
    # Called numbers table
    c.execute('''
        CREATE TABLE IF NOT EXISTS called_numbers (
            game_id INTEGER,
            number INTEGER,
            called_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
    ''')
    
    # Patterns table
    c.execute('''
        CREATE TABLE IF NOT EXISTS patterns (
            pattern_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            pattern_data TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # Transactions table
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount_etb REAL,
            type TEXT,
            reference TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Insert default patterns if none exist
    c.execute("SELECT COUNT(*) FROM patterns")
    if c.fetchone()[0] == 0:
        default_patterns = [
            (1, 'Full House', '{"type":"full_house"}'),
            (2, 'Four Corners', '{"type":"four_corners"}'),
            (3, 'X Pattern', '{"type":"x_pattern"}'),
            (4, 'Blackout', '{"type":"blackout"}'),
            (5, 'Letter L', '{"type":"letter_l"}'),
            (6, 'Letter T', '{"type":"letter_t"}'),
            (7, 'Diamond', '{"type":"diamond"}'),
            (8, 'Small Square', '{"type":"small_square"}'),
            (9, 'Big Square', '{"type":"big_square"}'),
            (10, 'Center Cross', '{"type":"center_cross"}'),
            (11, 'Top Row', '{"type":"top_row"}'),
            (12, 'Middle Row', '{"type":"middle_row"}'),
            (13, 'Bottom Row', '{"type":"bottom_row"}'),
            (14, 'Left Column', '{"type":"left_column"}'),
            (15, 'Right Column', '{"type":"right_column"}'),
            (16, 'Diagonal Top-Left', '{"type":"diagonal_tl"}'),
            (17, 'Diagonal Top-Right', '{"type":"diagonal_tr"}'),
            (18, 'Postage Stamp', '{"type":"postage_stamp"}'),
            (19, 'Arrow', '{"type":"arrow"}'),
            (20, 'Plus Sign', '{"type":"plus_sign"}')
        ]
        c.executemany(
            "INSERT INTO patterns (pattern_id, name, pattern_data) VALUES (?, ?, ?)",
            default_patterns
        )
        logger.info("✅ Inserted 20 bingo patterns")
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized")

# ==================== GAME STATE MANAGER ====================
class GameState:
    """Manages the state of a single game"""
    
    def __init__(self, game_id: int):
        self.game_id = game_id
        self.players: Dict[int, Dict] = {}  # user_id -> player data
        self.taken_cards: Set[int] = set()
        self.user_cards: Dict[int, List[int]] = {}  # user_id -> list of card_ids
        self.cards: Dict[int, List] = {}  # card_id -> card data (5x5 grid)
        self.marked: Dict[int, List[int]] = {}  # card_id -> marked numbers
        self.called_numbers: List[int] = []
        self.current_pattern_id = 1  # Default to Full House
        self.game_started = False
        self.game_ended = False
        self.round_start_time = None
        self.winner = None
        
    def add_player(self, user_id: int, username: str = "", first_name: str = ""):
        """Add a player to the game"""
        if user_id not in self.players:
            self.players[user_id] = {
                'id': user_id,
                'name': first_name or username or str(user_id),
                'username': username,
                'card_count': 0,
                'ready': False,
                'winner': False
            }
    
    def remove_player(self, user_id: int):
        """Remove a player from the game"""
        if user_id in self.players:
            # Release their cards
            if user_id in self.user_cards:
                for card_id in self.user_cards[user_id]:
                    self.taken_cards.discard(card_id)
                    self.cards.pop(card_id, None)
                    self.marked.pop(card_id, None)
                del self.user_cards[user_id]
            del self.players[user_id]
    
    def add_cards(self, user_id: int, card_ids: List[int], card_data: List):
        """Add cards for a player"""
        if user_id not in self.user_cards:
            self.user_cards[user_id] = []
        
        for i, card_id in enumerate(card_ids):
            self.user_cards[user_id].append(card_id)
            self.taken_cards.add(card_id)
            self.cards[card_id] = card_data[i] if i < len(card_data) else self.generate_card()
            self.marked[card_id] = []
        
        if user_id in self.players:
            self.players[user_id]['card_count'] = len(self.user_cards[user_id])
    
    def set_ready(self, user_id: int, ready: bool = True):
        """Set player ready status"""
        if user_id in self.players:
            self.players[user_id]['ready'] = ready
    
    def generate_card(self) -> List[List]:
        """Generate a proper BINGO card with standard column ranges"""
        card = []
        
        for row in range(5):
            row_data = []
            for col in range(5):
                if row == 2 and col == 2:  # FREE space
                    row_data.append('FREE')
                else:
                    col_range = BINGO_COLUMNS[col]
                    number = random.randint(col_range['min'], col_range['max'])
                    row_data.append(number)
            card.append(row_data)
        
        return card
    
    def call_number(self, number: int) -> bool:
        """Call a new number"""
        if number in self.called_numbers or number < 1 or number > 75:
            return False
        
        self.called_numbers.append(number)
        return True
    
    def mark_number(self, card_id: int, number: int) -> bool:
        """Mark a number on a card"""
        if card_id not in self.marked:
            return False
        
        if number not in self.called_numbers:
            return False
        
        if number not in self.marked[card_id]:
            self.marked[card_id].append(number)
            return True
        
        return False
    
    def check_bingo(self, card_id: int) -> bool:
        """Check if a card has bingo based on current pattern"""
        if card_id not in self.cards or card_id not in self.marked:
            return False
        
        card = self.cards[card_id]
        marked = set(self.marked[card_id])
        
        # Add FREE space to marked
        marked.add('FREE')
        
        pattern = self.get_pattern()
        
        if pattern == 'full_house':
            # Check all 24 non-FREE spaces
            for row in range(5):
                for col in range(5):
                    if row == 2 and col == 2:
                        continue
                    if card[row][col] not in marked:
                        return False
            return True
        
        elif pattern == 'four_corners':
            corners = [card[0][0], card[0][4], card[4][0], card[4][4]]
            return all(c in marked for c in corners)
        
        elif pattern == 'x_pattern':
            # Check both diagonals
            diag1 = [card[i][i] for i in range(5) if not (i == 2 and i == 2)]
            diag2 = [card[i][4-i] for i in range(5) if not (i == 2 and 4-i == 2)]
            return all(c in marked for c in diag1) and all(c in marked for c in diag2)
        
        elif pattern == 'top_row':
            return all(card[0][col] in marked for col in range(5) if card[0][col] != 'FREE')
        
        elif pattern == 'middle_row':
            return all(card[2][col] in marked for col in range(5) if card[2][col] != 'FREE')
        
        elif pattern == 'bottom_row':
            return all(card[4][col] in marked for col in range(5) if card[4][col] != 'FREE')
        
        elif pattern == 'left_column':
            return all(card[row][0] in marked for row in range(5) if card[row][0] != 'FREE')
        
        elif pattern == 'right_column':
            return all(card[row][4] in marked for row in range(5) if card[row][4] != 'FREE')
        
        # Add more patterns as needed
        
        return False
    
    def get_pattern(self) -> str:
        """Get current pattern type"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT pattern_data FROM patterns WHERE pattern_id = ?", (self.current_pattern_id,))
        result = c.fetchone()
        conn.close()
        
        if result:
            try:
                data = json.loads(result[0])
                return data.get('type', 'full_house')
            except:
                return 'full_house'
        return 'full_house'
    
    def reset_round(self):
        """Reset for next round"""
        self.called_numbers = []
        self.game_started = False
        self.game_ended = False
        self.winner = None
        
        # Reset player ready status
        for user_id in self.players:
            self.players[user_id]['ready'] = False
            self.players[user_id]['winner'] = False
        
        # Clear cards (they'll be re-selected)
        self.user_cards = {}
        self.taken_cards = set()
        self.cards = {}
        self.marked = {}
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'game_id': self.game_id,
            'players': list(self.players.values()),
            'taken_cards': list(self.taken_cards),
            'called_numbers': self.called_numbers,
            'game_started': self.game_started,
            'game_ended': self.game_ended,
            'pattern_id': self.current_pattern_id,
            'pattern_name': self.get_pattern_name()
        }
    
    def get_pattern_name(self) -> str:
        """Get current pattern name"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM patterns WHERE pattern_id = ?", (self.current_pattern_id,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else 'Full House'


# ==================== GLOBAL STATE ====================
games: Dict[int, GameState] = {}
active_connections: Dict[int, List[WebSocket]] = {}  # game_id -> list of websockets
user_connections: Dict[int, WebSocket] = {}  # user_id -> websocket

# ==================== FASTAPI APP ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown"""
    logger.info("🚀 Starting up...")
    init_db()
    yield
    logger.info("🛑 Shutting down...")
    # Cleanup
    for game_id, connections in active_connections.items():
        for ws in connections:
            await ws.close()
    active_connections.clear()
    user_connections.clear()

app = FastAPI(lifespan=lifespan)

# Templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== API ENDPOINTS ====================
@app.get("/")
async def root():
    return {"message": "Bingo Game API", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    """Serve the game HTML page"""
    # Get or create user
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, balance_etb) VALUES (?, ?)",
        (user_id, 100.0)  # Start with 100 ETB for demo
    )
    conn.commit()
    
    # Get user data
    c.execute(
        "SELECT balance_etb FROM users WHERE user_id = ?",
        (user_id,)
    )
    user = c.fetchone()
    balance = user[0] if user else 100.0
    
    # Get active games count
    c.execute(
        "SELECT COUNT(*) FROM game_players WHERE user_id = ? AND game_id IN (SELECT game_id FROM games WHERE status = 'playing')",
        (user_id,)
    )
    active_games = c.fetchone()[0]
    
    # Get total stake
    c.execute(
        "SELECT COALESCE(SUM(amount_etb), 0) FROM transactions WHERE user_id = ? AND type = 'card_purchase'",
        (user_id,)
    )
    total_stake = c.fetchone()[0]
    
    conn.close()
    
    return templates.TemplateResponse(
        "game.html",
        {
            "request": request,
            "user_id": user_id,
            "game_id": game_id,
            "admin_id": ADMIN_USER_ID,
            "initial_balance": balance,
            "initial_active_games": active_games,
            "initial_stake": total_stake,
            "price_per_card": PRICE_PER_CARD,
            "max_cards": MAX_CARDS_PER_PLAYER
        }
    )

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    """Get user data"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT user_id, username, first_name, balance_etb, total_won_etb, total_spent_etb, games_played, games_won FROM users WHERE user_id = ?",
        (user_id,)
    )
    user = c.fetchone()
    conn.close()
    
    if user:
        return {
            "user_id": user[0],
            "username": user[1],
            "first_name": user[2],
            "balance_etb": user[3],
            "total_won_etb": user[4],
            "total_spent_etb": user[5],
            "games_played": user[6],
            "games_won": user[7]
        }
    return None

@app.get("/api/patterns")
async def get_patterns():
    """Get all patterns"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT pattern_id, name, pattern_data FROM patterns WHERE is_active = 1")
    patterns = c.fetchall()
    conn.close()
    
    return [
        {"id": p[0], "name": p[1], "data": json.loads(p[2]) if p[2] else {}}
        for p in patterns
    ]

# ==================== WEBSOCKET ENDPOINT ====================
@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    """WebSocket connection for real-time game updates"""
    await websocket.accept()
    logger.info(f"User {user_id} connected to game {game_id}")
    
    # Get or create game state
    if game_id not in games:
        games[game_id] = GameState(game_id)
    
    game = games[game_id]
    
    # Add connection
    if game_id not in active_connections:
        active_connections[game_id] = []
    active_connections[game_id].append(websocket)
    user_connections[user_id] = websocket
    
    # Get user info from database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT username, first_name FROM users WHERE user_id = ?",
        (user_id,)
    )
    user_data = c.fetchone()
    conn.close()
    
    username = user_data[0] if user_data else ""
    first_name = user_data[1] if user_data else ""
    
    # Add player to game
    game.add_player(user_id, username, first_name)
    
    # Send initial state
    await websocket.send_json({
        "type": "connected",
        "game_id": game_id,
        "user_id": user_id,
        "taken_cards": list(game.taken_cards),
        "players": list(game.players.values()),
        "game_started": game.game_started,
        "called_numbers": game.called_numbers,
        "round": game.game_id,
        "pattern_name": game.get_pattern_name()
    })
    
    # Notify others
    await broadcast_to_game(game_id, {
        "type": "player_joined",
        "player": game.players[user_id]
    })
    
    try:
        while True:
            data = await websocket.receive_json()
            logger.info(f"Received: {data.get('type')} from user {user_id}")
            
            if data['type'] == 'select_cards':
                await handle_select_cards(websocket, game, user_id, data)
            
            elif data['type'] == 'start_game':
                await handle_start_game(websocket, game, user_id, data)
            
            elif data['type'] == 'call_number':
                await handle_call_number(websocket, game, user_id, data)
            
            elif data['type'] == 'mark_number':
                await handle_mark_number(websocket, game, user_id, data)
            
            elif data['type'] == 'claim_bingo':
                await handle_claim_bingo(websocket, game, user_id, data)
            
            elif data['type'] == 'set_pattern':
                await handle_set_pattern(websocket, game, user_id, data)
            
            elif data['type'] == 'reset_game':
                await handle_reset_game(websocket, game, user_id, data)
            
            elif data['type'] == 'ping':
                await websocket.send_json({"type": "pong"})
    
    except WebSocketDisconnect:
        logger.info(f"User {user_id} disconnected from game {game_id}")
        # Remove connection
        if game_id in active_connections:
            active_connections[game_id].remove(websocket)
        if user_id in user_connections:
            del user_connections[user_id]
        
        # Remove player from game after delay (allow reconnection)
        async def delayed_remove():
            await asyncio.sleep(30)  # Wait 30 seconds before removing
            if user_id not in user_connections:  # If still disconnected
                game.remove_player(user_id)
                await broadcast_to_game(game_id, {
                    "type": "player_left",
                    "user_id": user_id
                })
        
        asyncio.create_task(delayed_remove())
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if game_id in active_connections:
            active_connections[game_id].remove(websocket)

# ==================== WEBSOCKET HANDLERS ====================
async def handle_select_cards(websocket: WebSocket, game: GameState, user_id: int, data: Dict):
    """Handle card selection"""
    card_ids = data.get('card_ids', [])
    
    if game.game_started:
        await websocket.send_json({
            "type": "error",
            "message": "Game already started"
        })
        return
    
    if len(card_ids) > MAX_CARDS_PER_PLAYER:
        await websocket.send_json({
            "type": "error",
            "message": f"Maximum {MAX_CARDS_PER_PLAYER} cards allowed"
        })
        return
    
    # Check if cards are available
    for card_id in card_ids:
        if card_id in game.taken_cards:
            await websocket.send_json({
                "type": "error",
                "message": f"Card #{card_id} is already taken"
            })
            return
    
    # Check user balance
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance_etb FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    
    if not user:
        await websocket.send_json({
            "type": "error",
            "message": "User not found"
        })
        conn.close()
        return
    
    balance = user[0]
    total_cost = len(card_ids) * PRICE_PER_CARD
    
    if balance < total_cost:
        await websocket.send_json({
            "type": "error",
            "message": f"Insufficient balance. Need {total_cost} ETB, you have {balance} ETB"
        })
        conn.close()
        return
    
    # Deduct balance
    new_balance = balance - total_cost
    c.execute(
        "UPDATE users SET balance_etb = ?, total_spent_etb = total_spent_etb + ? WHERE user_id = ?",
        (new_balance, total_cost, user_id)
    )
    
    # Record transaction
    c.execute(
        "INSERT INTO transactions (user_id, amount_etb, type, reference) VALUES (?, ?, ?, ?)",
        (user_id, -total_cost, 'card_purchase', f'game_{game.game_id}')
    )
    
    conn.commit()
    conn.close()
    
    # Generate cards
    card_data = []
    for _ in card_ids:
        card_data.append(game.generate_card())
    
    # Add cards to game
    game.add_cards(user_id, card_ids, card_data)
    
    await websocket.send_json({
        "type": "cards_selected",
        "success": True,
        "message": f"Selected {len(card_ids)} cards",
        "card_ids": card_ids,
        "new_balance": new_balance
    })
    
    # Update player list
    await broadcast_to_game(game.game_id, {
        "type": "player_ready",
        "players": list(game.players.values())
    })
    
    logger.info(f"User {user_id} selected {len(card_ids)} cards in game {game.game_id}")

async def handle_start_game(websocket: WebSocket, game: GameState, user_id: int, data: Dict):
    """Handle game start (admin only)"""
    if user_id != ADMIN_USER_ID:
        await websocket.send_json({
            "type": "error",
            "message": "Only admin can start the game"
        })
        return
    
    ready_players = [p for p in game.players.values() if p['ready']]
    if len(ready_players) == 0:
        await websocket.send_json({
            "type": "error",
            "message": "No players ready"
        })
        return
    
    game.game_started = True
    game.round_start_time = datetime.now()
    
    await broadcast_to_game(game.game_id, {
        "type": "game_started",
        "time": game.round_start_time.isoformat()
    })
    
    # Send cards to players
    for player_id in game.user_cards:
        for card_id in game.user_cards[player_id]:
            if player_id in user_connections:
                try:
                    await user_connections[player_id].send_json({
                        "type": "your_card",
                        "card_id": card_id,
                        "card": game.cards[card_id],
                        "marked": game.marked[card_id]
                    })
                except:
                    pass

async def handle_call_number(websocket: WebSocket, game: GameState, user_id: int, data: Dict):
    """Handle number call (admin only)"""
    if user_id != ADMIN_USER_ID:
        await websocket.send_json({
            "type": "error",
            "message": "Only admin can call numbers"
        })
        return
    
    if not game.game_started:
        await websocket.send_json({
            "type": "error",
            "message": "Game not started"
        })
        return
    
    number = data.get('number')
    if not number or number < 1 or number > 75:
        await websocket.send_json({
            "type": "error",
            "message": "Invalid number"
        })
        return
    
    if game.call_number(number):
        await broadcast_to_game(game.game_id, {
            "type": "number_called",
            "number": number,
            "called": game.called_numbers
        })
        
        logger.info(f"Number {number} called in game {game.game_id}")
    else:
        await websocket.send_json({
            "type": "error",
            "message": f"Number {number} already called or invalid"
        })

async def handle_mark_number(websocket: WebSocket, game: GameState, user_id: int, data: Dict):
    """Handle marking a number on a card"""
    card_id = data.get('card_id')
    number = data.get('number')
    
    if not game.game_started:
        await websocket.send_json({
            "type": "error",
            "message": "Game not started"
        })
        return
    
    if card_id not in game.user_cards.get(user_id, []):
        await websocket.send_json({
            "type": "error",
            "message": "Card not owned by you"
        })
        return
    
    if game.mark_number(card_id, number):
        await websocket.send_json({
            "type": "number_marked",
            "card_id": card_id,
            "number": number
        })
    else:
        await websocket.send_json({
            "type": "error",
            "message": f"Could not mark number {number}"
        })

async def handle_claim_bingo(websocket: WebSocket, game: GameState, user_id: int, data: Dict):
    """Handle bingo claim"""
    card_id = data.get('card_id')
    
    if not game.game_started or game.game_ended:
        await websocket.send_json({
            "type": "error",
            "message": "Game not active"
        })
        return
    
    if card_id not in game.user_cards.get(user_id, []):
        await websocket.send_json({
            "type": "error",
            "message": "Card not owned by you"
        })
        return
    
    if game.check_bingo(card_id):
        # Calculate prize
        total_cards = sum(len(cards) for cards in game.user_cards.values())
        total_prize = total_cards * PRICE_PER_CARD
        house_fee = total_prize * HOUSE_COMMISSION
        winner_prize = total_prize - house_fee
        
        # Update winner
        game.game_ended = True
        game.winner = {
            'user_id': user_id,
            'name': game.players[user_id]['name'],
            'card_id': card_id,
            'prize': winner_prize
        }
        
        # Update database
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Add prize to winner
        c.execute(
            "UPDATE users SET balance_etb = balance_etb + ?, total_won_etb = total_won_etb + ?, games_won = games_won + 1 WHERE user_id = ?",
            (winner_prize, winner_prize, user_id)
        )
        
        # Record transaction
        c.execute(
            "INSERT INTO transactions (user_id, amount_etb, type, reference) VALUES (?, ?, ?, ?)",
            (user_id, winner_prize, 'win', f'game_{game.game_id}')
        )
        
        conn.commit()
        conn.close()
        
        await broadcast_to_game(game.game_id, {
            "type": "game_won",
            "winner": {
                "id": user_id,
                "name": game.players[user_id]['name'],
                "card_id": card_id
            },
            "prize": winner_prize,
            "total_prize": total_prize,
            "house_fee": house_fee
        })
        
        logger.info(f"Player {user_id} won game {game.game_id} with prize {winner_prize} ETB")
    else:
        await websocket.send_json({
            "type": "error",
            "message": "Not a bingo!"
        })

async def handle_set_pattern(websocket: WebSocket, game: GameState, user_id: int, data: Dict):
    """Handle pattern change (admin only)"""
    if user_id != ADMIN_USER_ID:
        await websocket.send_json({
            "type": "error",
            "message": "Only admin can change pattern"
        })
        return
    
    pattern_id = data.get('pattern_id')
    if not pattern_id:
        return
    
    game.current_pattern_id = pattern_id
    
    await broadcast_to_game(game.game_id, {
        "type": "pattern_updated",
        "pattern_id": pattern_id,
        "pattern_name": game.get_pattern_name()
    })

async def handle_reset_game(websocket: WebSocket, game: GameState, user_id: int, data: Dict):
    """Handle game reset for next round (admin only)"""
    if user_id != ADMIN_USER_ID:
        await websocket.send_json({
            "type": "error",
            "message": "Only admin can reset game"
        })
        return
    
    game.reset_round()
    
    await broadcast_to_game(game.game_id, {
        "type": "game_reset",
        "round": game.game_id + 1
    })
    
    logger.info(f"Game {game.game_id} reset for next round")

# ==================== BROADCAST FUNCTION ====================
async def broadcast_to_game(game_id: int, message: Dict):
    """Broadcast a message to all connected clients in a game"""
    if game_id in active_connections:
        for connection in active_connections[game_id]:
            try:
                await connection.send_json(message)
            except:
                pass

# ==================== TELEGRAM BOT SETUP ====================
telegram_app = None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_id = user.id
    
    # Register user in database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, balance_etb) VALUES (?, ?, ?, ?)",
        (user_id, user.username, user.first_name, 100.0)  # Start with 100 ETB
    )
    conn.commit()
    conn.close()
    
    # Create inline keyboard
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", url=f"{BASE_URL}/game?user_id={user_id}&game_id=1")],
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎯 Welcome to Bingo, {user.first_name}!\n\n"
        f"Click below to start playing:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "balance":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT balance_etb FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        conn.close()
        
        balance = user[0] if user else 0
        await query.edit_message_text(f"💰 Your balance: **{balance} ETB**", parse_mode='Markdown')
    
    elif query.data == "stats":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT balance_etb, total_won_etb, total_spent_etb, games_played, games_won FROM users WHERE user_id = ?",
            (user_id,)
        )
        user = c.fetchone()
        conn.close()
        
        if user:
            stats = (
                f"📊 **Your Stats**\n\n"
                f"💰 Balance: {user[0]} ETB\n"
                f"🏆 Total Won: {user[1]} ETB\n"
                f"💸 Total Spent: {user[2]} ETB\n"
                f"🎮 Games Played: {user[3]}\n"
                f"🏅 Games Won: {user[4]}"
            )
        else:
            stats = "No stats available"
        
        await query.edit_message_text(stats, parse_mode='Markdown')

async def setup_webhook(application):
    """Setup webhook for Telegram bot"""
    webhook_url = f"{BASE_URL}/webhook"
    try:
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook set to {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to set webhook: {e}")
        return False

async def shutdown_telegram():
    """Shutdown Telegram bot gracefully"""
    global telegram_app
    if telegram_app:
        try:
            await telegram_app.bot.delete_webhook()
            await telegram_app.stop()
            await telegram_app.shutdown()
            logger.info("✅ Telegram bot stopped")
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Telegram webhook endpoint"""
    global telegram_app
    if telegram_app:
        try:
            data = await request.json()
            update = Update.de_json(data, telegram_app.bot)
            await telegram_app.process_update(update)
            return {"ok": True}
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "Bot not initialized"}

def handle_signal():
    """Handle shutdown signals"""
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

async def shutdown():
    """Graceful shutdown"""
    logger.info("🛑 Shutting down gracefully...")
    await shutdown_telegram()
    # Close all WebSocket connections
    for game_id, connections in active_connections.items():
        for ws in connections:
            await ws.close()
    active_connections.clear()
    user_connections.clear()
    # Release lock
    if LOCK_FILE:
        LOCK_FILE.close()
    logger.info("✅ Shutdown complete")
    sys.exit(0)

# ==================== MAIN ====================
async def main():
    """Main entry point"""
    global telegram_app
    
    # Setup signal handlers
    handle_signal()
    
    # Initialize Telegram bot
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CallbackQueryHandler(button_callback))
    
    # Setup webhook
    await setup_webhook(telegram_app)
    
    # Start FastAPI with uvicorn
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=PORT,
        log_level="info"
    )
    server = uvicorn.Server(config)
    
    logger.info(f"🚀 Server starting on port {PORT}")
    await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    finally:
        if LOCK_FILE:
            LOCK_FILE.close()