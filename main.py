import os
import json
import random
import asyncio
import logging
import time
import uuid
import re
import sys
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Dict, Set, List, Any, Optional, Tuple

import aiohttp
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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log')
    ]
)
logger = logging.getLogger(__name__)

# Configuration with error handling
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable not set")
    raise ValueError("BOT_TOKEN environment variable not set")

ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')
if not ADMIN_USER_ID:
    logger.error("ADMIN_USER_ID environment variable not set")
    raise ValueError("ADMIN_USER_ID environment variable not set")
try:
    ADMIN_USER_ID = int(ADMIN_USER_ID)
except ValueError:
    raise ValueError("ADMIN_USER_ID must be an integer")

BOT_USERNAME = os.getenv('BOT_USERNAME', 'MK_BINGO_bot')
RAILWAY_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN')

if not RAILWAY_URL:
    # Fallback for local development
    RAILWAY_URL = "localhost:8080"
    BASE_URL = f"http://{RAILWAY_URL}"
    logger.warning(f"RAILWAY_PUBLIC_DOMAIN not set, using localhost: {BASE_URL}")
else:
    if not RAILWAY_URL.startswith('http'):
        BASE_URL = f"https://{RAILWAY_URL}"
    else:
        BASE_URL = RAILWAY_URL

# Card prices (in cents)
CARD_PRICE_ROOM1 = 5000        # 50.00 ETB
CARD_PRICE_ROOM2 = 10000       # 100.00 ETB
CARD_PRICE_ROOM3 = 20000        # 200.00 ETB
MAX_CARDS_PER_PLAYER = 8
WELCOME_BONUS = 1000
AUTO_START_DELAY = 30
AUTO_CALL_INTERVAL = 3  # seconds
HOUSE_PERCENT = 0.20
ROUND_RESET_DELAY = 10

logger.info(f"✅ Configuration loaded - BASE_URL: {BASE_URL}")

# ==================== DATABASE INITIALIZATION ====================
try:
    db = Database()
    logger.info("✅ Database initialized successfully")
except Exception as e:
    logger.error(f"❌ Database initialization failed: {e}")
    raise

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

# ==================== CARDS ====================
CARDS_FILE = "static/bingo_cards.json"

def generate_default_cards():
    """Generate 1000 bingo cards"""
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

# Load or generate cards
try:
    os.makedirs("static", exist_ok=True)
    if os.path.exists(CARDS_FILE):
        with open(CARDS_FILE, 'r') as f:
            BINGO_CARDS = json.load(f)
        logger.info(f"✅ Loaded {len(BINGO_CARDS)} cards from file")
        if len(BINGO_CARDS) < 1000:
            BINGO_CARDS = generate_default_cards()
            with open(CARDS_FILE, 'w') as f:
                json.dump(BINGO_CARDS, f)
    else:
        BINGO_CARDS = generate_default_cards()
        with open(CARDS_FILE, 'w') as f:
            json.dump(BINGO_CARDS, f)
except Exception as e:
    logger.error(f"Error loading cards: {e}")
    BINGO_CARDS = generate_default_cards()

# Setup templates and static files
templates = Jinja2Templates(directory="templates")
os.makedirs("static", exist_ok=True)

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

# ==================== Game Class ====================

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
        self.countdown_timers = {}
        self.game_locks = {}
        self.auto_start_timers = {}
        self.first_card_time = {}
        self.reset_timers = {}
        self.number_pool = {}  # For manual number calling
        
        # Pattern system
        self.room_patterns = {}  # room_id -> pattern name
        self.room_pattern_locked = {}  # room_id -> bool
        self.room_price = {}  # room_id -> price in cents
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
        """Send heartbeat to all connected clients"""
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
        """Handle new WebSocket connection"""
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
                self.number_pool[game_id] = list(range(1, 76))
                random.shuffle(self.number_pool[game_id])
                
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

            pattern_display = self.room_patterns.get(game_id, "any_line")
            pattern_name = get_pattern_name(pattern_display)
            
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
                'pattern_name': pattern_name,
                'pattern_locked': self.room_pattern_locked.get(game_id, False),
                'manual_marking': True
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
        """Handle WebSocket disconnection"""
        async with self.get_lock(game_id):
            if game_id in self.game_connections and websocket in self.game_connections[game_id]:
                self.game_connections[game_id].remove(websocket)
            if user_id in self.user_connections:
                self.user_connections[user_id] -= 1
                if self.user_connections[user_id] <= 0:
                    del self.user_connections[user_id]
        logger.info(f"User {user_id} disconnected from game {game_id}")

    async def broadcast(self, game_id: int, message: dict):
        """Broadcast message to all clients in a game room"""
        if game_id in self.game_connections:
            for conn in self.game_connections[game_id][:]:
                try:
                    await conn.send_json(message)
                except:
                    if conn in self.game_connections[game_id]:
                        self.game_connections[game_id].remove(conn)

    def get_players(self, game_id: int):
        """Get list of players in a game room"""
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
        """Update countdown timer for game room"""
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
        """Select cards for a player"""
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
                price_per_card = self.room_price.get(game_id, CARD_PRICE_ROOM1)
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
                    
                    # Save to database
                    db.save_card_status(user_id, game_id, card_id, [], False)
                
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
        """Start auto-start timer for game"""
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
        """Start a new round"""
        if self.game_started.get(game_id, False) or game_id not in self.active_games or self.active_games[game_id]['total_cards_sold'] == 0:
            return
        self.game_started[game_id] = True
        self.stop_number_generation[game_id] = False
        if game_id in self.auto_start_timers:
            self.auto_start_timers[game_id].cancel()
            del self.auto_start_timers[game_id]
        await self.broadcast(game_id, {'type': 'game_started', 'round': self.round_numbers[game_id]})
        logger.info(f"Room {game_id} started")

    async def call_next_number(self, game_id: int):
        """Call the next number in the sequence (manual calling)"""
        async with self.get_lock(game_id):
            if not self.game_started.get(game_id, False):
                return
            
            if self.stop_number_generation.get(game_id, False):
                return
            
            if game_id not in self.number_pool or not self.number_pool[game_id]:
                return
            
            number = self.number_pool[game_id].pop()
            
            # Add to called numbers
            if game_id not in self.called_numbers:
                self.called_numbers[game_id] = []
            self.called_numbers[game_id].append(number)
            
            if game_id in self.active_games:
                self.active_games[game_id]['called_numbers'].append(number)
            
            # Broadcast to all players
            await self.broadcast(game_id, {
                'type': 'number_called',
                'number': number,
                'called': self.called_numbers[game_id],
                'auto_mark': False
            })
            
            logger.info(f"Room {game_id} - Called number: {number}")

    async def handle_mark_number(self, game_id: int, user_id: int, card_id: int, number: int) -> dict:
        """Handle manual marking of a called number"""
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
                
                # Save to database
                marked_list = list(self.player_marked_numbers[game_id][user_id][card_id])
                db.save_card_status(user_id, game_id, card_id, marked_list, 
                                   card_id in self.suspended_cards.get(game_id, {}).get(user_id, set()))
                
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

    async def add_card(self, game_id: int, user_id: int) -> Tuple[bool, str, Optional[int]]:
        """Add a single card during the game"""
        async with self.get_lock(game_id):
            try:
                if game_id not in self.active_games:
                    return False, "Game not found", None
                
                if user_id not in self.active_games[game_id]['players']:
                    return False, "Player not found", None
                
                player = self.active_games[game_id]['players'][user_id]
                
                # Check max cards
                if len(player['card_ids']) >= MAX_CARDS_PER_PLAYER:
                    return False, f"Maximum {MAX_CARDS_PER_PLAYER} cards per player", None
                
                # Check if player is suspended
                if user_id in self.suspended_players.get(game_id, set()):
                    return False, "You are suspended from this round", None
                
                # Get price
                price_per_card = self.room_price.get(game_id, CARD_PRICE_ROOM1)
                
                # Check balance
                user = db.get_user(user_id)
                if not user or user['balance'] < price_per_card:
                    return False, f"Insufficient balance. Need {price_per_card/100} ETB", None
                
                # Generate new card
                available_cards = [c for c in BINGO_CARDS if c['id'] not in self.taken_cards.get(game_id, set())]
                if not available_cards:
                    return False, "No available cards", None
                
                card_data = random.choice(available_cards)
                card_id = card_data['id']
                
                # Deduct balance
                update_result = db.update_balance(
                    user_id, 
                    -price_per_card, 
                    'game_fee', 
                    f'Added card during game #{game_id}'
                )
                
                if not update_result:
                    return False, "Failed to deduct balance", None
                
                new_balance = update_result['new_balance']
                
                # Add card
                if game_id not in self.taken_cards:
                    self.taken_cards[game_id] = set()
                self.taken_cards[game_id].add(card_id)
                
                if game_id not in self.card_owners:
                    self.card_owners[game_id] = {}
                self.card_owners[game_id][card_id] = user_id
                
                player['cards'].append(card_data['card'])
                player['card_ids'].append(card_id)
                player['marked'][card_id] = []
                player['balance'] = new_balance
                
                # Initialize marked numbers
                if game_id not in self.player_marked_numbers:
                    self.player_marked_numbers[game_id] = {}
                if user_id not in self.player_marked_numbers[game_id]:
                    self.player_marked_numbers[game_id][user_id] = {}
                if card_id not in self.player_marked_numbers[game_id][user_id]:
                    self.player_marked_numbers[game_id][user_id][card_id] = set()
                
                # Save to database
                db.save_card_status(user_id, game_id, card_id, [], False)
                
                # Update game stats
                self.active_games[game_id]['total_cards_sold'] += 1
                self.active_games[game_id]['prize_pool'] += price_per_card
                
                logger.info(f"User {user_id} added card {card_id} during game {game_id}")
                
                return True, f"Card {card_id} added", new_balance
                
            except Exception as e:
                logger.error(f"Error in add_card: {e}")
                return False, f"Error: {str(e)}", None

    async def handle_false_bingo(self, game_id: int, user_id: int, card_id: int = None) -> Tuple[bool, str, dict]:
        """Handle false bingo claim with card-based suspension"""
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
                
                # Update database
                db.update_suspension_status(user_id, game_id, [card_to_suspend])
                
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
        """Finish a round with multiple winners"""
        if game_id not in self.active_games:
            return

        self.stop_number_generation[game_id] = True
        winner_ids = [w[0] for w in winners]
        logger.info(f"Finishing round {game_id} - Winners: {winner_ids}")

        prize_pool = self.active_games[game_id]['prize_pool']
        house_cut = int(prize_pool * HOUSE_PERCENT)
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

        pattern_display = self.room_patterns.get(game_id, "ONE_LINE")
        pattern_name = get_pattern_name(pattern_display)
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
            'pattern': pattern_display,
            'pattern_name': pattern_name
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
        """Delayed reset after round ends"""
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
        self.number_pool[game_id] = list(range(1, 76))
        random.shuffle(self.number_pool[game_id])
        
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
        
        pattern_display = self.room_patterns.get(game_id, "ONE_LINE")
        pattern_name = get_pattern_name(pattern_display)
        logger.info(f"✅ Room {game_id} round {self.round_numbers[game_id]} ready")
        
        await self.broadcast(game_id, {
            'type': 'game_reset',
            'round': self.round_numbers[game_id],
            'players': self.get_players(game_id),
            'countdown': 15,
            'pattern': pattern_display,
            'pattern_name': pattern_name
        })

# Initialize game manager
game_manager = IntegratedBingoGame()

# ==================== FastAPI App Setup ====================
app = FastAPI(title="MK BINGO Game")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== Web Routes ====================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "status": "online",
        "service": "MK BINGO",
        "cards": len(BINGO_CARDS),
        "price_per_card": CARD_PRICE_ROOM1 / 100,
        "max_cards_per_player": MAX_CARDS_PER_PLAYER
    }

@app.get("/health")
async def health():
    """Health check endpoint for Railway"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/rooms", response_class=HTMLResponse)
async def rooms_page(request: Request, user_id: int):
    """Room selection page"""
    try:
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
    except Exception as e:
        logger.error(f"Error loading rooms page: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    """Main game page"""
    try:
        user = db.get_or_create_user(user_id)
        
        # Get pattern
        pattern = game_manager.room_patterns.get(game_id, "any_line")
        pattern_name = get_pattern_name(pattern)
        
        # Get price
        if game_id == 1:
            price = CARD_PRICE_ROOM1 / 100
        elif game_id == 2:
            price = CARD_PRICE_ROOM2 / 100
        else:
            price = CARD_PRICE_ROOM3 / 100
        
        return templates.TemplateResponse("bingo.html", {
            "request": request,
            "user_id": user_id,
            "game_id": game_id,
            "pattern": pattern,
            "pattern_name": pattern_name,
            "price_per_card": price,
            "max_cards": MAX_CARDS_PER_PLAYER,
            "initial_balance": user['balance'] / 100,
            "initial_active_games": db.get_active_games_count(user_id),
            "initial_stake": db.get_total_stake(user_id) / 100,
            "auto_start_delay": AUTO_START_DELAY,
            "is_admin": str(user_id) == str(ADMIN_USER_ID)
        })
    except Exception as e:
        logger.error(f"Error loading game page: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/room-stats")
async def get_room_stats():
    """Get statistics for all rooms"""
    try:
        stats = {}
        for room_id in [1, 2, 3]:
            if room_id in game_manager.active_games:
                total_cards = game_manager.active_games[room_id]['total_cards_sold']
                player_count = len(game_manager.active_games[room_id]['players'])
                game_started = game_manager.game_started.get(room_id, False)
                pattern = game_manager.room_patterns.get(room_id, "any_line")
                pattern_name = get_pattern_name(pattern)
            else:
                total_cards = 0
                player_count = 0
                game_started = False
                pattern = "any_line"
                pattern_name = "Any Line"
            
            stats[room_id] = {
                "total_cards": total_cards,
                "players": player_count,
                "game_started": game_started,
                "pattern": pattern,
                "pattern_name": pattern_name
            }
        return stats
    except Exception as e:
        logger.error(f"Error getting room stats: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/user/{user_id}")
async def get_user_info(user_id: int):
    """Get user information"""
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

# ==================== WebSocket Endpoint ====================

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    """WebSocket endpoint for real-time gameplay"""
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
                        card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                        if card:
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
            
            elif data['type'] == 'add_card':
                success, message, new_balance = await game_manager.add_card(game_id, user_id)
                await websocket.send_json({
                    'type': 'add_card_result',
                    'success': success,
                    'message': message,
                    'new_balance': new_balance
                })
                if success:
                    # Send the new card data
                    # This would need to be implemented to return the actual card
                    pass
            
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

# ==================== Telegram Bot Setup ====================

async def setup_bot():
    """Setup and start the Telegram bot"""
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    # Add handlers here (simplified for now)
    # You can add your conversation handlers from the original code
    
    await application.initialize()
    await application.start()
    
    # Set webhook
    webhook_url = f"{BASE_URL}/webhook"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"🤖 Webhook set to {webhook_url}")
    
    return application

@app.post("/webhook")
async def webhook(request: Request):
    """Telegram webhook endpoint"""
    try:
        data = await request.json()
        update = Update.de_json(data, game_manager.bot_app.bot)
        await game_manager.bot_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)

# ==================== Lifespan ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    logger.info("🚀 Starting up MK BINGO application...")
    
    # Start bot
    try:
        game_manager.bot_app = await setup_bot()
        logger.info("✅ Telegram bot started")
    except Exception as e:
        logger.error(f"❌ Failed to start Telegram bot: {e}")
    
    # Start heartbeat
    asyncio.create_task(game_manager.start_heartbeat())
    logger.info("✅ Heartbeat task started")
    
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
    
    # Stop bot
    if game_manager.bot_app:
        await game_manager.bot_app.stop()
        await game_manager.bot_app.shutdown()
    
    logger.info("✅ Shutdown complete")

# Set lifespan
app.router.lifespan_context = lifespan

# ==================== Main Entry Point ====================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8080))
    host = os.getenv('HOST', '0.0.0.0')
    
    logger.info(f"🚀 Starting server on {host}:{port}")
    logger.info(f"📁 Static files: {os.path.abspath('static')}")
    logger.info(f"📁 Templates: {os.path.abspath('templates')}")
    logger.info(f"💰 Card price room 1: {CARD_PRICE_ROOM1/100} ETB")
    logger.info(f"💰 Card price room 2: {CARD_PRICE_ROOM2/100} ETB")
    logger.info(f"💰 Card price room 3: {CARD_PRICE_ROOM3/100} ETB")
    
    uvicorn.run(
        app, 
        host=host, 
        port=port,
        log_level="info"
    )