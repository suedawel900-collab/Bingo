import os
import json
import random
import asyncio
import logging
import time
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

# Conversation states
PHONE_NUMBER, AMOUNT, REFERENCE = range(3)

logger.info(f"✅ Using BASE_URL: {BASE_URL}")

# Initialize database
db = Database()

# Load bingo cards
CARDS_FILE = "static/bingo_cards.json"

def generate_default_cards():
    cards = []
    for i in range(1, 101):
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
    return cards

# Load cards
try:
    if os.path.exists(CARDS_FILE):
        with open(CARDS_FILE, 'r') as f:
            BINGO_CARDS = json.load(f)
            logger.info(f"✅ Loaded {len(BINGO_CARDS)} cards")
    else:
        BINGO_CARDS = generate_default_cards()
        os.makedirs("static", exist_ok=True)
        with open(CARDS_FILE, 'w') as f:
            json.dump(BINGO_CARDS, f)
        logger.info(f"✅ Generated {len(BINGO_CARDS)} default cards")
except Exception as e:
    logger.error(f"Error loading cards: {e}")
    BINGO_CARDS = generate_default_cards()

# Templates
templates = Jinja2Templates(directory="templates")
os.makedirs("static", exist_ok=True)

# Game Manager - Fixed Version with Debug Logging
class GameManager:
    def __init__(self):
        self.active_games = {}
        self.game_connections = {}
        self.taken_cards = {}
        self.game_started = {}
        self.game_winner = {}
        self.round_number = {}
        self.number_tasks = {}
        self.countdown_timers = {}
        self.bot_app = None
        # Simple connection tracking
        self.user_connections = {}
        self.MAX_CONNECTIONS_PER_USER = 2
        
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
            self.game_started[game_id] = False
            self.game_winner[game_id] = None
            self.round_number[game_id] = 1
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
                'ready': False,
                'winner': False,
                'total_spent': 0,
                'cards_won': 0,
                'balance': user['balance']
            }
            logger.info(f"Created new player {user_id} in game {game_id} with balance {user['balance']}")
        
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
        # Send initial state
        await websocket.send_json({
            'type': 'connected',
            'taken_cards': list(self.taken_cards[game_id]),
            'players': self.get_players(game_id),
            'round': self.round_number[game_id],
            'game_started': self.game_started[game_id],
            'winner': self.game_winner[game_id],
            'called_numbers': self.active_games[game_id]['called_numbers'],
            'countdown': self.countdown_timers[game_id],
            'balance': user['balance'] / 100,
            'active_games': active_games,
            'total_stake': total_stake / 100
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
                    if self.countdown_timers[game_id] <= 0 and self.game_started[game_id]:
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
    
    # FIXED: Balance is deducted immediately when cards are selected with better logging
    async def select_cards(self, game_id: int, user_id: int, card_ids: List[int]):
        logger.info(f"select_cards called - game:{game_id}, user:{user_id}, cards:{card_ids}")
        
        if game_id not in self.active_games:
            logger.error(f"Game {game_id} not found")
            return False, "Game not found", 0, None
        
        if self.game_started[game_id]:
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
            
            card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
            if not card_data:
                logger.error(f"Card {card_id} not found")
                return False, f"Card {card_id} not found", 0, None
        
        total_cost = len(card_ids) * CARD_PRICE
        logger.info(f"Total cost: {total_cost}, player balance: {player['balance']}")
        
        # Check balance
        if player['balance'] < total_cost:
            logger.warning(f"Insufficient balance: {player['balance']} < {total_cost}")
            return False, f"Insufficient balance. Need {total_cost/100} ETB", total_cost, None
        
        # Add cards and deduct balance immediately
        for card_id in card_ids:
            self.taken_cards[game_id].add(card_id)
            card_data = next(c for c in BINGO_CARDS if c['id'] == card_id)
            player['cards'].append(card_data['card'])
            player['card_ids'].append(card_id)
            player['marked'][card_id] = []
            logger.info(f"Added card {card_id} to player {user_id}")
        
        player['total_spent'] += total_cost
        player['balance'] -= total_cost  # Deduct balance immediately
        self.active_games[game_id]['total_cards_sold'] += len(card_ids)
        self.active_games[game_id]['prize_pool'] = self.active_games[game_id]['total_cards_sold'] * CARD_PRICE
        
        logger.info(f"User {user_id} selected {len(card_ids)} cards, cost: {total_cost}, new balance: {player['balance']}")
        
        return True, f"Selected {len(card_ids)} cards", total_cost, player['balance']
    
    # FIXED: Finalize selection updates database
    async def finalize_selection(self, game_id: int, user_id: int):
        logger.info(f"finalize_selection called - game:{game_id}, user:{user_id}")
        
        if game_id not in self.active_games:
            logger.error(f"Game {game_id} not found")
            return False, "Game not found", None
        
        if user_id not in self.active_games[game_id]['players']:
            logger.error(f"User {user_id} not found in game {game_id}")
            return False, "Player not found", None
        
        player = self.active_games[game_id]['players'][user_id]
        logger.info(f"Player cards: {player['card_ids']}, spent: {player['total_spent']}, ready: {player['ready']}")
        
        if len(player['card_ids']) == 0:
            logger.warning(f"User {user_id} has no cards selected")
            return False, "No cards selected", player['balance']
        
        if player['ready']:
            logger.warning(f"User {user_id} is already ready")
            return False, "Already ready", player['balance']
        
        total_cost = player['total_spent']
        logger.info(f"Finalizing with total cost: {total_cost}")
        
        # Update database with the already deducted balance
        result = db.update_balance(
            user_id=user_id,
            amount=-total_cost,
            transaction_type='game_fee',
            description=f'Joined game #{game_id} with {len(player["card_ids"])} cards'
        )
        
        if not result:
            # If database update fails, refund the player
            logger.error(f"Failed to deduct balance for user {user_id}, refunding {total_cost}")
            player['balance'] += total_cost
            player['total_spent'] = 0
            for card_id in player['card_ids']:
                self.taken_cards[game_id].discard(card_id)
            player['cards'] = []
            player['card_ids'] = []
            player['marked'] = {}
            return False, "Failed to deduct balance", player['balance']
        
        # Save to active games
        db.add_active_game(user_id, game_id, player['card_ids'], total_cost)
        player['ready'] = True
        
        logger.info(f"User {user_id} finalized selection for game {game_id}, new balance: {player['balance']}")
        
        await self.broadcast(game_id, {
            'type': 'player_ready',
            'players': self.get_players(game_id),
            'user_id': user_id
        })
        
        return True, "Ready to play", player['balance']
    
    # FIXED: Only admin can start the game
    async def start_game(self, game_id: int, user_id: int):
        # Allow only admin to start
        if str(user_id) != ADMIN_USER_ID:
            return False, "Only admin can start the game"
        
        if game_id not in self.active_games:
            return False, "Game not found"
        
        if self.game_started[game_id]:
            return False, "Game already started"
        
        # Check if there are any ready players
        ready_players = [p for p in self.active_games[game_id]['players'].values() if p['ready']]
        if len(ready_players) == 0:
            return False, "No players ready"
        
        # Start the game
        self.game_started[game_id] = True
        self.game_winner[game_id] = None
        self.active_games[game_id]['called_numbers'] = []
        self.countdown_timers[game_id] = 15
        
        # Cancel any existing number generation task
        if self.number_tasks.get(game_id):
            self.number_tasks[game_id].cancel()
        
        # Start new number generation
        self.number_tasks[game_id] = asyncio.create_task(
            self.generate_numbers(game_id)
        )
        
        logger.info(f"Game {game_id} started by admin {user_id} with {len(ready_players)} players")
        
        await self.broadcast(game_id, {
            'type': 'game_started',
            'round': self.round_number[game_id]
        })
        
        return True, "Game started"
    
    async def reset_game(self, game_id: int):
        if game_id not in self.active_games:
            return
        
        if self.number_tasks[game_id]:
            self.number_tasks[game_id].cancel()
            self.number_tasks[game_id] = None
        
        self.game_started[game_id] = False
        self.game_winner[game_id] = None
        self.active_games[game_id]['called_numbers'] = []
        self.countdown_timers[game_id] = 15
        
        # Reset all players for new round
        for player in self.active_games[game_id]['players'].values():
            player['cards'] = []
            player['card_ids'] = []
            player['marked'] = {}
            player['ready'] = False
            player['winner'] = False
        
        # Clear taken cards
        self.taken_cards[game_id] = set()
        
        # Reset game stats
        self.active_games[game_id]['total_cards_sold'] = 0
        self.active_games[game_id]['prize_pool'] = 0
        
        self.round_number[game_id] += 1
        
        await self.broadcast(game_id, {
            'type': 'game_reset',
            'round': self.round_number[game_id],
            'players': self.get_players(game_id),
            'countdown': 15
        })
    
    async def generate_numbers(self, game_id: int):
        try:
            while game_id in self.active_games and self.game_started[game_id]:
                await asyncio.sleep(2)
                
                if not self.game_started[game_id] or self.game_winner[game_id]:
                    break
                
                available = [n for n in range(1, 76) 
                            if n not in self.active_games[game_id]['called_numbers']]
                
                if available:
                    number = random.choice(available)
                    self.active_games[game_id]['called_numbers'].append(number)
                    
                    await self.check_winners(game_id, number)
                    
                    await self.broadcast(game_id, {
                        'type': 'number_called',
                        'number': number,
                        'called': self.active_games[game_id]['called_numbers'],
                        'left': len(available) - 1
                    })
                else:
                    await self.broadcast(game_id, {
                        'type': 'game_over',
                        'message': 'All numbers called'
                    })
                    await self.reset_game(game_id)
                    break
        except asyncio.CancelledError:
            pass
    
    async def check_winners(self, game_id: int, last_number: int):
        if game_id not in self.active_games or self.game_winner[game_id]:
            return
        
        called = set(self.active_games[game_id]['called_numbers'])
        
        for user_id, player in self.active_games[game_id]['players'].items():
            if player['winner'] or not player['ready']:
                continue
            
            for card_idx, card in enumerate(player['cards']):
                card_id = player['card_ids'][card_idx]
                marked = set(player['marked'].get(card_id, []))
                
                if card[2][2] == 'FREE':
                    marked.add('FREE')
                
                if self.check_card_bingo(card, marked):
                    await self.declare_winner(game_id, user_id, card_id, last_number)
                    return
    
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
    
    async def declare_winner(self, game_id: int, user_id: int, card_id: int, winning_number: int):
        if game_id not in self.active_games or self.game_winner[game_id]:
            return
        
        player = self.active_games[game_id]['players'][user_id]
        player['winner'] = True
        player['cards_won'] += 1
        
        self.game_winner[game_id] = {
            'user_id': user_id,
            'name': player['name'],
            'card_id': card_id,
            'winning_number': winning_number,
            'round': self.round_number[game_id]
        }
        
        self.game_started[game_id] = False
        
        prize_pool = self.active_games[game_id]['prize_pool']
        winner_prize = int(prize_pool * 0.9)
        house_fee = prize_pool - winner_prize
        
        db.update_balance(
            user_id=user_id,
            amount=winner_prize,
            transaction_type='game_win',
            description=f'Won round {self.round_number[game_id]} in game #{game_id}'
        )
        
        player['balance'] += winner_prize
        
        await self.broadcast(game_id, {
            'type': 'game_won',
            'winner': self.game_winner[game_id],
            'prize': winner_prize / 100,
            'house_fee': house_fee / 100
        })
        
        if self.number_tasks[game_id]:
            self.number_tasks[game_id].cancel()
            self.number_tasks[game_id] = None
        
        # Send Telegram notification
        if self.bot_app:
            try:
                await self.bot_app.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 **CONGRATULATIONS!** 🎉\n\n"
                         f"You won round {self.round_number[game_id]}!\n"
                         f"Prize: **{winner_prize/100} ETB**",
                    parse_mode='Markdown'
                )
            except:
                pass
    
    def mark_number(self, game_id: int, user_id: int, card_id: int, number: int):
        if game_id not in self.active_games:
            return False
        
        if not self.game_started[game_id]:
            return False
        
        if self.game_winner[game_id]:
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
    
    async def get_user_stats(self, user_id: int):
        user = db.get_user(user_id)
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
        return {
            'balance': user['balance'] / 100 if user else 0,
            'active_games': active_games,
            'total_stake': total_stake / 100,
            'games_played': user['games_played'] if user else 0,
            'games_won': user['games_won'] if user else 0
        }

game_manager = GameManager()

# ==================== TELEGRAM BOT HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_data = db.get_or_create_user(user.id, user.username, user.first_name, user.last_name)
    
    balance = user_data['balance'] / 100
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
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
    
    if query.data == "play":
        user_data = db.get_user(user.id)
        if not user_data or user_data['balance'] < CARD_PRICE:
            await query.edit_message_text(
                f"❌ Insufficient balance. Need {CARD_PRICE/100} ETB minimum.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="menu")
                ]])
            )
            return
        
        webapp_url = f"{BASE_URL}/game?user_id={user.id}&game_id=1"
        
        await query.edit_message_text(
            "🎮 **Click to open game**",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Open Game", web_app={'url': webapp_url})
            ]])
        )
    
    elif query.data == "balance":
        user_data = db.get_user(user.id)
        balance = user_data['balance'] / 100 if user_data else 0
        active_games = db.get_active_games_count(user.id)
        total_stake = db.get_total_stake(user.id) / 100
        
        await query.edit_message_text(
            f"💰 **Your Balance**\n\n"
            f"Current: **{balance:.2f} ETB**\n"
            f"Active Games: {active_games}\n"
            f"Total Stake: {total_stake:.2f} ETB",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    
    elif query.data == "deposit":
        await query.edit_message_text(
            "💰 **Deposit Instructions**\n\n"
            "Send payment to:\n"
            "📱 **0953933030**\n\n"
            "After payment, send the reference number here.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    
    elif query.data == "help":
        help_text = (
            "❓ **Bingo Bot Help**\n\n"
            "**How to Play:**\n"
            "1. Click 'Play Bingo'\n"
            "2. Choose your cards\n"
            "3. Click Confirm to lock in your cards\n"
            "4. Wait for admin to start the game\n"
            "5. Mark numbers as they are called\n"
            "6. Click BINGO when you win!\n\n"
            f"**Price:** {CARD_PRICE/100} ETB per card\n\n"
            "**Note:** Only admin can start the game"
        )
        await query.edit_message_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    
    elif query.data == "admin" and str(user.id) == ADMIN_USER_ID:
        stats = db.get_system_stats()
        pending = len(db.get_pending_payment_requests())
        
        await query.edit_message_text(
            f"👑 **Admin Panel**\n\n"
            f"Users: {stats['total_users']}\n"
            f"Total Balance: {stats['total_balance']/100:.2f} ETB\n"
            f"Pending Payments: {pending}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Pending", callback_data="admin_pending"),
                InlineKeyboardButton("◀️ Back", callback_data="menu")
            ]])
        )
    
    elif query.data == "menu":
        user_data = db.get_user(user.id)
        balance = user_data['balance'] / 100 if user_data else 0
        
        keyboard = [
            [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
            [InlineKeyboardButton("💰 Balance", callback_data="balance")],
            [InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
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
    reference = update.message.text.strip()
    user = update.effective_user
    
    # Simple confirmation
    await update.message.reply_text(
        f"✅ Reference received: {reference}\nAdmin will verify your payment.",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Notify admin
    if ADMIN_USER_ID:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"💰 Payment reference from {user.first_name} (ID: {user.id}):\n`{reference}`",
            parse_mode='Markdown'
        )

# ==================== BOT SETUP ====================

async def setup_bot():
    """Initialize bot with webhook mode"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_callback))
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
        "max_cards_per_player": MAX_CARDS_PER_PLAYER
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
        
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
        return {
            "user_id": user_id,
            "balance": user['balance'],
            "balance_etb": user['balance'] / 100,
            "active_games": active_games,
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
        "initial_stake": db.get_total_stake(user_id) / 100
    })

# ==================== WEBSOCKET ENDPOINT - FIXED ====================

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
                logger.info(f"select_cards result: success={success}, message={message}, cost={cost}, new_balance={new_balance}")
                
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
            
            # FIXED: Handle finalize message with proper response
            elif data['type'] == 'finalize':
                logger.info(f"Processing finalize for user {user_id}")
                success, message, new_balance = await game_manager.finalize_selection(game_id, user_id)
                
                # Send response back to client
                await websocket.send_json({
                    'type': 'finalized',
                    'success': success,
                    'message': message,
                    'new_balance': new_balance
                })
                logger.info(f"Sent finalized response to user {user_id}: success={success}, balance={new_balance}, message={message}")
            
            elif data['type'] == 'start_game':
                success, message = await game_manager.start_game(game_id, user_id)
                await websocket.send_json({
                    'type': 'start_result',
                    'success': success,
                    'message': message
                })
            
            elif data['type'] == 'reset_game':
                if str(user_id) != ADMIN_USER_ID:
                    continue
                await game_manager.reset_game(game_id)
                await websocket.send_json({'type': 'game_reset', 'success': True})
            
            elif data['type'] == 'call_number':
                if str(user_id) != ADMIN_USER_ID:
                    continue
                # This would be handled by the game logic
                # For now, just acknowledge
                await websocket.send_json({
                    'type': 'number_called',
                    'number': data.get('number'),
                    'called': game_manager.active_games[game_id]['called_numbers']
                })
            
            elif data['type'] == 'set_pattern':
                if str(user_id) != ADMIN_USER_ID:
                    continue
                pattern_id = data.get('pattern_id')
                if pattern_id:
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE games SET pattern_id = ? WHERE id = ?", (pattern_id, game_id))
                        conn.commit()
                        
                        # Get pattern name
                        cursor.execute("SELECT name FROM patterns WHERE id = ?", (pattern_id,))
                        pattern = cursor.fetchone()
                        pattern_name = pattern[0] if pattern else "Unknown"
                        
                        await game_manager.broadcast(game_id, {
                            'type': 'pattern_updated',
                            'pattern_id': pattern_id,
                            'pattern_name': pattern_name
                        })
            
            elif data['type'] == 'mark_number':
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
                    player = game_manager.active_games[game_id]['players'].get(user_id)
                    if player and card_id in player['card_ids']:
                        card_index = player['card_ids'].index(card_id)
                        card = player['cards'][card_index]
                        marked_set = set(player['marked'].get(card_id, []))
                        
                        if game_manager.check_card_bingo(card, marked_set):
                            last_number = game_manager.active_games[game_id]['called_numbers'][-1] if game_manager.active_games[game_id]['called_numbers'] else 0
                            await game_manager.declare_winner(game_id, user_id, card_id, last_number)
                        else:
                            await websocket.send_json({
                                'type': 'error',
                                'message': 'Not a valid bingo'
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