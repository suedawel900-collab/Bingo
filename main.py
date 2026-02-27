import os
import json
import random
import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Dict, Set, List, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
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

# Conversation states for payment
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

# Game Manager
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
        
    async def connect(self, game_id: int, websocket: WebSocket, user_id: int):
        await websocket.accept()
        logger.info(f"User {user_id} connected to game {game_id}")
        
        user = db.get_or_create_user(
            user_id=user_id,
            username=None,
            first_name=None,
            last_name=None
        )
        
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
        
        player_name = user['first_name'] if user and user.get('first_name') else f"Player{user_id}"
        
        # Check if player already exists in this game
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
        else:
            # Reconnecting player - restore their state from database
            logger.info(f"🔄 Reconnecting player {user_id} - restoring game state")
            
            # Get their cards from database
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT card_id, card_data, marked_numbers 
                    FROM user_cards 
                    WHERE game_id = ? AND user_id = ?
                """, (game_id, user_id))
                
                cards = cursor.fetchall()
                player = self.active_games[game_id]['players'][user_id]
                
                # Restore cards
                for card in cards:
                    card_id = card[0]
                    card_data = json.loads(card[1])
                    marked_numbers = json.loads(card[2])
                    
                    player['cards'].append(card_data)
                    player['card_ids'].append(card_id)
                    player['marked'][card_id] = marked_numbers
                    
                    # Add to taken cards if not already there
                    self.taken_cards[game_id].add(card_id)
                
                # Restore ready status
                if len(player['card_ids']) > 0:
                    player['ready'] = True
                    logger.info(f"✅ Restored {len(player['card_ids'])} cards for user {user_id}")
        
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
        # Send complete game state to reconnecting client
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
        
        # Send all their cards
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
        
        # If game is active, send current state
        if self.game_started[game_id]:
            await websocket.send_json({
                'type': 'game_active',
                'called_numbers': self.active_games[game_id]['called_numbers'],
                'last_number': self.active_games[game_id]['called_numbers'][-1] if self.active_games[game_id]['called_numbers'] else None
            })
        
        asyncio.create_task(self.update_countdown(game_id))
        
        await self.broadcast(game_id, {
            'type': 'player_reconnected',
            'players': self.get_players(game_id),
            'user_id': user_id
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
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Countdown error: {e}")
    
    def disconnect(self, game_id: int, websocket: WebSocket, user_id: int):
        if game_id in self.game_connections:
            if websocket in self.game_connections[game_id]:
                self.game_connections[game_id].remove(websocket)
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
                'card_ids': data['card_ids'],
                'card_count': len(data['card_ids']),
                'ready': data['ready'],
                'total_spent': data['total_spent'],
                'winner': data['winner'],
                'cards_won': data['cards_won']
            })
        return players
    
    async def select_cards(self, game_id: int, user_id: int, card_ids: List[int]):
        if game_id not in self.active_games:
            return False, "Game not found", 0
        
        if self.game_started[game_id]:
            return False, "Game already started", 0
        
        if self.game_winner[game_id]:
            return False, "Game ended, wait for next round", 0
        
        if user_id not in self.active_games[game_id]['players']:
            return False, "Player not found", 0
        
        player = self.active_games[game_id]['players'][user_id]
        
        if len(player['card_ids']) + len(card_ids) > MAX_CARDS_PER_PLAYER:
            return False, f"Maximum {MAX_CARDS_PER_PLAYER} cards per player", 0
        
        new_cards = []
        for card_id in card_ids:
            if card_id in self.taken_cards[game_id]:
                return False, f"Card {card_id} already taken", 0
            
            card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
            if not card_data:
                return False, f"Card {card_id} not found", 0
            
            new_cards.append(card_data)
        
        total_cost = len(card_ids) * CARD_PRICE
        
        if player['balance'] < total_cost:
            return False, f"Insufficient balance. Need {total_cost/100} ETB", total_cost
        
        for card_id in card_ids:
            self.taken_cards[game_id].add(card_id)
        
        for card_data in new_cards:
            player['cards'].append(card_data['card'])
            player['card_ids'].append(card_data['id'])
            player['marked'][card_data['id']] = []
        
        player['total_spent'] += total_cost
        player['balance'] -= total_cost
        self.active_games[game_id]['total_cards_sold'] += len(card_ids)
        self.active_games[game_id]['prize_pool'] = self.active_games[game_id]['total_cards_sold'] * CARD_PRICE
        
        logger.info(f"User {user_id} selected {len(card_ids)} cards in game {game_id}")
        
        return True, f"Selected {len(card_ids)} cards", total_cost
    
    async def finalize_selection(self, game_id: int, user_id: int):
        if game_id not in self.active_games:
            return False, "Game not found"
        
        if user_id not in self.active_games[game_id]['players']:
            return False, "Player not found"
        
        player = self.active_games[game_id]['players'][user_id]
        
        if len(player['card_ids']) == 0:
            return False, "No cards selected"
        
        if player['ready']:
            return False, "Already ready"
        
        total_cost = player['total_spent']
        result = db.update_balance(
            user_id=user_id,
            amount=-total_cost,
            transaction_type='game_fee',
            description=f'Joined game #{game_id} with {len(player["card_ids"])} cards'
        )
        
        if not result:
            return False, "Failed to deduct balance"
        
        db.add_active_game(user_id, game_id, player['card_ids'], total_cost)
        player['ready'] = True
        player['balance'] = result['new_balance']
        
        await self.broadcast(game_id, {
            'type': 'player_ready',
            'players': self.get_players(game_id),
            'user_id': user_id,
            'card_count': len(player['card_ids'])
        })
        
        return True, "Ready to play"
    
    async def start_game(self, game_id: int, user_id: int):
        if str(user_id) != ADMIN_USER_ID:
            return False, "Not authorized"
        
        if game_id not in self.active_games:
            return False, "Game not found"
        
        if self.game_started[game_id]:
            return False, "Game already started"
        
        if self.game_winner[game_id]:
            await self.reset_game(game_id)
        
        ready_count = sum(1 for p in self.active_games[game_id]['players'].values() if p['ready'])
        if ready_count < 1:
            return False, "No players ready"
        
        self.game_started[game_id] = True
        self.game_winner[game_id] = None
        self.active_games[game_id]['called_numbers'] = []
        self.countdown_timers[game_id] = 15
        
        if self.number_tasks[game_id]:
            self.number_tasks[game_id].cancel()
        
        self.number_tasks[game_id] = asyncio.create_task(
            self.generate_numbers(game_id)
        )
        
        await self.broadcast(game_id, {
            'type': 'game_started',
            'round': self.round_number[game_id]
        })
        logger.info(f"Game {game_id} started by admin")
        
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
        
        for player in self.active_games[game_id]['players'].values():
            player['ready'] = False
            player['winner'] = False
            for card_id in player['marked']:
                player['marked'][card_id] = []
        
        self.round_number[game_id] += 1
        
        await self.broadcast(game_id, {
            'type': 'game_reset',
            'round': self.round_number[game_id],
            'players': self.get_players(game_id),
            'countdown': 15
        })
        
        logger.info(f"Game {game_id} reset for round {self.round_number[game_id]}")
    
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
            logger.info(f"Number generation stopped for game {game_id}")
        except Exception as e:
            logger.error(f"Error in number generation: {e}")
    
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
        diag1 = True
        diag2 = True
        for i in range(5):
            val1 = card[i][i]
            val2 = card[4-i][i]
            
            if val1 != 'FREE' and val1 not in marked:
                diag1 = False
            if val2 != 'FREE' and val2 not in marked:
                diag2 = False
        
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
            description=f'Won round {self.round_number[game_id]} in game #{game_id} with card #{card_id}'
        )
        
        player['balance'] += winner_prize
        
        await self.broadcast(game_id, {
            'type': 'game_won',
            'winner': self.game_winner[game_id],
            'prize': winner_prize / 100,
            'house_fee': house_fee / 100
        })
        
        logger.info(f"Game {game_id} winner: {player['name']} with card #{card_id}, prize: {winner_prize/100} ETB")
        
        if self.number_tasks[game_id]:
            self.number_tasks[game_id].cancel()
            self.number_tasks[game_id] = None
        
        # Send Telegram notification to winner
        if self.bot_app:
            try:
                await self.bot_app.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 **CONGRATULATIONS!** 🎉\n\n"
                         f"You won round {self.round_number[game_id]}!\n"
                         f"Prize: **{winner_prize/100} ETB**\n"
                         f"Card #{card_id} with number {winning_number}\n\n"
                         f"Check your balance with /balance",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Failed to send win notification: {e}")
    
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
        user = db.get_or_create_user(user_id)
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
        return {
            'balance': user['balance'] / 100,
            'active_games': active_games,
            'total_stake': total_stake / 100,
            'games_played': user['games_played'],
            'games_won': user['games_won']
        }

game_manager = GameManager()

# ==================== TELEGRAM BOT HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    db.get_or_create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # Get user balance
    user_data = db.get_user(user.id)
    balance = user_data['balance'] / 100 if user_data else 0
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [
            InlineKeyboardButton("💰 Balance", callback_data="balance"),
            InlineKeyboardButton("💳 Deposit", callback_data="deposit")
        ],
        [
            InlineKeyboardButton("📊 History", callback_data="history"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    
    if str(user.id) == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎯 Welcome to Bingo Bot, {user.first_name}!\n\n"
        f"💰 Your balance: **{balance:.2f} ETB**\n"
        f"Get ready to play Bingo and win prizes!\n"
        f"• Game fee: {CARD_PRICE/100} ETB per card\n"
        f"• Max cards: {MAX_CARDS_PER_PLAYER}\n"
        f"• Welcome bonus: {WELCOME_BONUS/100} ETB",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def play_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle play button"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    game_id = 1  # Default game
    
    # Check if user has balance
    user_data = db.get_user(user.id)
    if not user_data or user_data['balance'] < CARD_PRICE:
        keyboard = [
            [InlineKeyboardButton("💳 Deposit Now", callback_data="deposit")],
            [InlineKeyboardButton("◀️ Back", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            f"❌ **Insufficient Balance**\n\n"
            f"You need at least {CARD_PRICE/100} ETB to play.\n"
            f"Your balance: **{user_data['balance']/100 if user_data else 0:.2f} ETB**\n\n"
            f"Please deposit funds to continue.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    webapp_url = f"{BASE_URL}/game?user_id={user.id}&game_id={game_id}"
    
    keyboard = [[
        InlineKeyboardButton("🎮 Open Game", web_app={'url': webapp_url})
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🎮 **Ready to Play!**\n\n"
        f"Click below to open the game.\n\n"
        f"• Cards available: {len(BINGO_CARDS)}\n"
        f"• Price per card: {CARD_PRICE/100} ETB\n"
        f"• Your balance: {user_data['balance']/100:.2f} ETB\n"
        f"• Max cards: {MAX_CARDS_PER_PLAYER}",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle balance button"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        user_data = {'balance': WELCOME_BONUS, 'games_played': 0, 'games_won': 0}
    
    active_games = db.get_active_games_count(user.id)
    total_stake = db.get_total_stake(user.id)
    
    # Get pending payments
    pending_payments = db.get_user_payment_requests(user.id, limit=5)
    pending_count = len([p for p in pending_payments if p['status'] == 'pending'])
    
    balance_text = (
        f"💰 **Your Balance**\n\n"
        f"**Current:** {user_data['balance']/100:.2f} ETB\n"
        f"**Active Games:** {active_games}\n"
        f"**Total Stake:** {total_stake/100:.2f} ETB\n"
        f"**Games Played:** {user_data['games_played']}\n"
        f"**Games Won:** {user_data['games_won']}\n"
    )
    
    if pending_count > 0:
        balance_text += f"\n⏳ **Pending Payments:** {pending_count}"
    
    await query.edit_message_text(
        balance_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("◀️ Back", callback_data="main_menu")]
        ])
    )

async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle history button"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    transactions = db.get_user_transactions(user.id, limit=10)
    payments = db.get_user_payment_requests(user.id, limit=5)
    
    if (not transactions or len(transactions) == 0) and (not payments or len(payments) == 0):
        await query.edit_message_text(
            "📊 No transaction history yet.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="main_menu")
            ]])
        )
        return
    
    history_text = "📊 **Recent Activity**\n\n"
    
    # Add transactions
    if transactions:
        history_text += "**💰 Transactions:**\n"
        for t in transactions[:5]:
            amount = t['amount'] / 100
            date = datetime.fromisoformat(t['created_at']).strftime("%m/%d %H:%M")
            emoji = "➕" if t['amount'] > 0 else "➖"
            history_text += f"{emoji} {date}: {amount:.2f} ETB ({t['type']})\n"
        history_text += "\n"
    
    # Add payments
    if payments:
        history_text += "**💳 Payments:**\n"
        for p in payments[:3]:
            amount = p['amount'] / 100
            date = datetime.fromisoformat(p['created_at']).strftime("%m/%d %H:%M")
            status_emoji = "✅" if p['status'] == 'completed' else "⏳" if p['status'] == 'pending' else "❌"
            history_text += f"{status_emoji} {date}: {amount:.2f} ETB ({p['status']})\n"
    
    await query.edit_message_text(
        history_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="main_menu")
        ]])
    )

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle help button"""
    query = update.callback_query
    await query.answer()
    
    help_text = (
        "❓ **Bingo Bot Help**\n\n"
        "**How to Play:**\n"
        "1. Click 'Play Bingo' to start\n"
        "2. Choose your cards (max 20)\n"
        "3. Wait for admin to start the game\n"
        "4. Numbers are called every 2 seconds\n"
        "5. Click numbers on your card to mark them\n"
        "6. Get 5 in a row to win!\n\n"
        "**💰 Balance:**\n"
        "• Each game costs 10 ETB per card\n"
        "• Winner gets 90% of prize pool\n"
        "• Welcome bonus: 10 ETB\n\n"
        "**💳 Deposit Methods:**\n"
        "• 🔵 **Telebirr** - Dial *127#\n"
        "• 💚 **CBE Birr** - Dial *847#\n\n"
        "**📞 Contact:**\n"
        "• Bot: @Treeeeestbot\n"
        "• Admin: /admin (for support)"
    )
    
    await query.edit_message_text(
        help_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="main_menu")
        ]])
    )

# ==================== PAYMENT HANDLERS ====================

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start deposit process with Ethiopian mobile money"""
    query = update.callback_query
    await query.answer()
    
    # Get only mobile money methods (Telebirr and CBE Birr)
    methods = db.get_payment_methods(type='mobile_money', active_only=True)
    
    keyboard = []
    for method in methods:
        method_name = method['method_name']
        min_amt = method['min_amount'] / 100
        max_amt = method['max_amount'] / 100
        
        # Different emoji for each
        if 'CBE' in method_name or 'ሲቢኢ' in method_name:
            emoji = "💚"
        else:
            emoji = "🔵"
            
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {method_name} ({min_amt:.0f}-{max_amt:.0f} ETB)",
            callback_data=f"pay_{method['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("◀️ ተመለስ / Back", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💳 **የክፍያ ዘዴ ይምረጡ / Choose Payment Method**\n\n"
        "እባክዎ የሚፈልጉትን የክፍያ ዘዴ ይምረጡ:\n"
        "Please select your payment method:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    return PHONE_NUMBER

async def payment_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment method selection"""
    query = update.callback_query
    await query.answer()
    
    method_id = int(query.data.split('_')[1])
    context.user_data['payment_method_id'] = method_id
    
    method = db.get_payment_method(method_id)
    
    if not method:
        await query.edit_message_text("❌ Invalid payment method")
        return ConversationHandler.END
    
    # Ask for phone number
    await query.edit_message_text(
        f"📱 **{method['method_name']}**\n\n"
        f"እባክዎ የስልክ ቁጥርዎን ያስገቡ (09xxxxxxxx):\n"
        f"Please enter your phone number (09xxxxxxxx):",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ተመለስ / Cancel", callback_data="main_menu")
        ]])
    )
    return AMOUNT

async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number input"""
    phone = update.message.text.strip()
    
    # Validate Ethiopian phone number
    if not phone.startswith('09') or len(phone) != 10:
        await update.message.reply_text(
            "❌ ትክክለኛ የስልክ ቁጥር ያስገቡ (09xxxxxxxx)\n"
            "Please enter a valid phone number (09xxxxxxxx)"
        )
        return AMOUNT
    
    context.user_data['phone_number'] = phone
    method_id = context.user_data.get('payment_method_id')
    method = db.get_payment_method(method_id)
    
    min_amt = method['min_amount'] / 100
    max_amt = method['max_amount'] / 100
    
    await update.message.reply_text(
        f"💰 **መጠን ያስገቡ / Enter Amount**\n\n"
        f"ዝቅተኛ: {min_amt:.0f} ETB\n"
        f"ከፍተኛ: {max_amt:.0f} ETB\n\n"
        f"እባክዎ መጠኑን ያስገቡ:\n"
        f"Please enter the amount:",
        parse_mode='Markdown'
    )
    return REFERENCE

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle amount input"""
    try:
        amount = float(update.message.text.strip())
        amount_cents = int(amount * 100)
        
        method_id = context.user_data.get('payment_method_id')
        method = db.get_payment_method(method_id)
        phone = context.user_data.get('phone_number')
        
        # Validate amount
        if amount_cents < method['min_amount']:
            await update.message.reply_text(f"❌ Minimum amount is {method['min_amount']/100:.0f} ETB")
            return REFERENCE
        if amount_cents > method['max_amount']:
            await update.message.reply_text(f"❌ Maximum amount is {method['max_amount']/100:.0f} ETB")
            return REFERENCE
        
        # Create payment request with retry logic
        request_id = None
        retries = 3
        while retries > 0 and not request_id:
            try:
                request_id = db.create_payment_request(
                    user_id=update.effective_user.id,
                    method_id=method_id,
                    amount=amount_cents,
                    sender_phone=phone
                )
            except Exception as e:
                logger.error(f"Payment request error: {e}")
                retries -= 1
                if retries == 0:
                    await update.message.reply_text("❌ Database error. Please try again.")
                    return ConversationHandler.END
                await asyncio.sleep(1)
        
        # Store in context for recovery
        context.user_data['payment_request_id'] = request_id
        context.user_data['amount'] = amount_cents
        
        # Get account number
        account_number = "0953933030"  # Default
        
        # Show payment instructions based on method
        method_name = method['method_name']
        if 'CBE' in method_name or 'ሲቢኢ' in method_name:
            emoji = "💚"
            instructions = (
                f"**የ CBE ቢር ክፍያ መመሪያ / CBE Birr Instructions**\n\n"
                f"1. *847# ይደውሉ / Dial *847#\n"
                f"2. ገንዘብ ላክ ይምረጡ / Select Send Money\n"
                f"3. ቁጥር **{account_number}** ያስገቡ / Enter number\n"
                f"4. {amount:.0f} ETB ያስገቡ / Enter amount\n"
                f"5. ፒንዎን ያስገቡ / Enter your PIN\n"
                f"6. የግብይት መለያ ቁጥር ያስቀምጡ / Save transaction ID"
            )
        else:
            emoji = "🔵"
            instructions = (
                f"**የቴሌቢር ክፍያ መመሪያ / Telbirr Instructions**\n\n"
                f"1. *127# ይደውሉ / Dial *127#\n"
                f"2. ገንዘብ ላክ ይምረጡ / Select Send Money\n"
                f"3. ቁጥር **{account_number}** ያስገቡ / Enter number\n"
                f"4. {amount:.0f} ETB ያስገቡ / Enter amount\n"
                f"5. ፒንዎን ያስገቡ / Enter your PIN\n"
                f"6. የደረሰኝ ቁጥር ያስቀምጡ / Save reference number"
            )
        
        message = (
            f"{emoji} **{method_name}**\n\n"
            f"💰 **መጠን / Amount:** {amount:.0f} ETB\n"
            f"📱 **ስልክ / Phone:** {phone}\n"
            f"🆔 **መለያ / Request ID:** `{request_id}`\n\n"
            f"**📋 መመሪያ / Instructions:**\n{instructions}\n\n"
            f"✅ **ከተከፈለ በኋላ የክፍያ ማረጋገጫ ቁጥር ይላኩልን**\n"
            f"**After payment, send us the transaction reference:**"
        )
        
        await update.message.reply_text(
            message,
            parse_mode='Markdown'
        )
        
        # Ask for reference
        await update.message.reply_text(
            "📝 **የክፍያ ማረጋገጫ ቁጥር ያስገቡ**\n"
            "**Enter the transaction reference:**\n\n"
            "ለምሳሌ / Example: `TRX123456`",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return REFERENCE

async def handle_reference(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment reference with retry logic and connection loss recovery"""
    reference = update.message.text.strip()
    request_id = context.user_data.get('payment_request_id')
    
    # If request_id not in context, try to recover from database
    if not request_id:
        user_id = update.effective_user.id
        # Get most recent pending payment for this user
        pending = db.get_user_payment_requests(user_id, limit=1)
        if pending and pending[0]['status'] == 'pending':
            request_id = pending[0]['request_id']
            context.user_data['payment_request_id'] = request_id
            logger.info(f"Recovered payment request {request_id} for user {user_id}")
        else:
            await update.message.reply_text(
                "❌ Session expired. Please start over with /deposit",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💳 Deposit", callback_data="deposit")
                ]])
            )
            return ConversationHandler.END
    
    # Add payment proof with retry
    retries = 3
    success = False
    while retries > 0 and not success:
        try:
            success = db.add_payment_proof(
                request_id=request_id,
                proof_type='text',
                proof_data=reference
            )
            if success:
                break
        except Exception as e:
            logger.error(f"Error adding payment proof: {e}")
            retries -= 1
            if retries == 0:
                await update.message.reply_text(
                    "❌ Failed to save reference. Please contact admin.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📞 Contact Admin", url=f"tg://user?id={ADMIN_USER_ID}"),
                        InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")
                    ]])
                )
                return ConversationHandler.END
            await asyncio.sleep(1)
    
    await update.message.reply_text(
        f"✅ **ክፍያ ሪፖርት ተልኳል! / Payment Reported!**\n\n"
        f"የክፍያ ማረጋገጫ ቁጥርዎ: `{reference}`\n"
        f"Your reference: `{reference}`\n\n"
        f"⏳ አስተዳዳሪ ክፍያዎን በቅርቡ ያረጋግጣል።\n"
        f"Admin will verify your payment shortly.\n"
        f"ቀሪ ሂሳብዎ ሲሞላ ይነገርዎታል።\n"
        f"You'll be notified once your balance is updated.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ዋና መደብ / Main Menu", callback_data="main_menu")
        ]])
    )
    
    # Clear user data
    context.user_data.clear()
    
    # Notify admin
    if ADMIN_USER_ID:
        user = update.effective_user
        user_data = db.get_user(user.id)
        phone = user_data.get('phone_number') if user_data else 'Unknown'
        
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"💰 **New Payment Report**\n\n"
                 f"**User:** {user.first_name} (ID: `{user.id}`)\n"
                 f"**Phone:** {phone}\n"
                 f"**Request ID:** `{request_id}`\n"
                 f"**Reference:** `{reference}`\n\n"
                 f"Use admin panel to approve/reject.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 View Payments", callback_data="admin_pending_payments")
            ]])
        )
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    await update.message.reply_text(
        "Cancelled.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ==================== ADMIN HANDLERS ====================

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin panel"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    stats = db.get_system_stats()
    pending_payments = len(db.get_pending_payment_requests(limit=100))
    
    await query.edit_message_text(
        f"👑 **Admin Panel**\n\n"
        f"**System Stats:**\n"
        f"Total Users: {stats.get('total_users', 0)}\n"
        f"Total Balance: {stats.get('total_balance', 0)/100:.2f} ETB\n"
        f"Total Deposits: {stats.get('total_deposits', 0)/100:.2f} ETB\n"
        f"Total Withdrawals: {stats.get('total_withdrawals', 0)/100:.2f} ETB\n"
        f"Active Games: {stats.get('active_games', 0)}\n\n"
        f"**Pending Approvals:**\n"
        f"💰 Payments: {pending_payments}\n\n"
        f"Select an option:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Pending Payments", callback_data="admin_pending_payments")],
            [InlineKeyboardButton("📊 System Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")]
        ])
    )

async def admin_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending payments for admin"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    pending = db.get_pending_payment_requests(limit=20)
    
    if not pending:
        await query.edit_message_text(
            "📊 No pending payments.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="admin_panel")
            ]])
        )
        return
    
    await query.edit_message_text(
        f"📋 **Found {len(pending)} Pending Payments**\n\n"
        f"Check the messages below for details.",
        parse_mode='Markdown'
    )
    
    for p in pending:
        # Determine emoji based on method
        if 'CBE' in p['method_name'] or 'ሲቢኢ' in p['method_name']:
            emoji = "💚"
        else:
            emoji = "🔵"
        
        keyboard = [
            [
                InlineKeyboardButton(f"{emoji} ✅ Approve", callback_data=f"approve_pay_{p['request_id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_pay_{p['request_id']}")
            ]
        ]
        
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"{emoji} **Pending Payment**\n\n"
                 f"**Request ID:** `{p['request_id']}`\n"
                 f"**User:** {p['first_name']} (ID: `{p['user_id']}`)\n"
                 f"**Amount:** {p['amount']/100:.0f} ETB\n"
                 f"**Method:** {p['method_name']}\n"
                 f"**Phone:** {p['sender_phone']}\n"
                 f"**Time:** {p['created_at']}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    await context.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text="📊 Use buttons above to approve/reject payments.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_pending_payments"),
            InlineKeyboardButton("◀️ Admin Panel", callback_data="admin_panel")
        ]])
    )

async def approve_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve payment and add balance to user"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    request_id = query.data.split('_')[2]  # approve_pay_REQUESTID
    
    # Get payment request details
    request = db.get_payment_request(request_id)
    
    if not request:
        await query.edit_message_text("❌ Payment request not found")
        return
    
    try:
        # Update payment request status
        db.update_payment_request_status(
            request_id=request_id,
            status='completed',
            admin_notes=f"Approved by admin {update.effective_user.id}"
        )
        
        # Add funds to user balance
        result = db.update_balance(
            user_id=request['user_id'],
            amount=request['amount'],
            transaction_type='deposit',
            description=f'Payment via {request_id}'
        )
        
        # Get method emoji
        if 'CBE' in request['method_name'] or 'ሲቢኢ' in request['method_name']:
            emoji = "💚"
        else:
            emoji = "🔵"
        
        # Notify user
        await context.bot.send_message(
            chat_id=request['user_id'],
            text=f"{emoji} **Payment Approved!**\n\n"
                 f"✅ Your payment of **{request['amount']/100:.0f} ETB** has been approved.\n"
                 f"Funds have been added to your balance.\n\n"
                 f"**New Balance:** {result['new_balance']/100:.2f} ETB\n"
                 f"Request ID: `{request_id}`\n\n"
                 f"Thank you for using Bingo Bot!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Play Bingo", callback_data="play")
            ]])
        )
        
        # Confirm to admin
        await query.edit_message_text(
            f"✅ **Payment Approved Successfully!**\n\n"
            f"**Request ID:** `{request_id}`\n"
            f"**User ID:** {request['user_id']}\n"
            f"**Amount:** {request['amount']/100:.0f} ETB\n\n"
            f"The user has been notified.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error approving payment: {e}")
        await query.edit_message_text(
            f"❌ Error approving payment: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="admin_pending_payments")
            ]])
        )

async def reject_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject payment"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    request_id = query.data.split('_')[2]  # reject_pay_REQUESTID
    
    # Get payment request details
    request = db.get_payment_request(request_id)
    
    if not request:
        await query.edit_message_text("❌ Payment request not found")
        return
    
    try:
        # Update payment request status
        db.update_payment_request_status(
            request_id=request_id,
            status='rejected',
            admin_notes=f"Rejected by admin {update.effective_user.id}"
        )
        
        # Get method emoji
        if 'CBE' in request['method_name'] or 'ሲቢኢ' in request['method_name']:
            emoji = "💚"
        else:
            emoji = "🔵"
        
        # Notify user
        await context.bot.send_message(
            chat_id=request['user_id'],
            text=f"{emoji} **Payment Rejected**\n\n"
                 f"❌ Your payment of **{request['amount']/100:.0f} ETB** has been rejected.\n\n"
                 f"**Request ID:** `{request_id}`\n\n"
                 f"Please contact admin if you believe this is an error.\n"
                 f"Make sure you sent the payment to the correct number and provided the right reference.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📞 Contact Admin", url=f"tg://user?id={ADMIN_USER_ID}")
            ]])
        )
        
        # Confirm to admin
        await query.edit_message_text(
            f"❌ **Payment Rejected**\n\n"
            f"**Request ID:** `{request_id}`\n"
            f"**User ID:** {request['user_id']}\n"
            f"**Amount:** {request['amount']/100:.0f} ETB\n\n"
            f"The user has been notified.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error rejecting payment: {e}")
        await query.edit_message_text(
            f"❌ Error rejecting payment: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="admin_pending_payments")
            ]])
        )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed system statistics"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    stats = db.get_system_stats()
    
    await query.edit_message_text(
        f"📊 **Detailed System Statistics**\n\n"
        f"**Users:** {stats.get('total_users', 0)}\n"
        f"**Total Balance:** {stats.get('total_balance', 0)/100:.2f} ETB\n"
        f"**Total Deposits:** {stats.get('total_deposits', 0)/100:.2f} ETB\n"
        f"**Total Withdrawals:** {stats.get('total_withdrawals', 0)/100:.2f} ETB\n"
        f"**Game Fees:** {stats.get('total_game_fees', 0)/100:.2f} ETB\n"
        f"**Game Wins:** {stats.get('total_game_wins', 0)/100:.2f} ETB\n\n"
        f"**Active Games:** {stats.get('active_games', 0)}\n"
        f"**Today's Volume:** {stats.get('today_volume', 0)/100:.2f} ETB\n"
        f"**Total Games:** {stats.get('total_games', 0)}",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats"),
            InlineKeyboardButton("◀️ Back", callback_data="admin_panel")
        ]])
    )

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_data = db.get_user(user.id)
    balance = user_data['balance'] / 100 if user_data else 0
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [
            InlineKeyboardButton("💰 Balance", callback_data="balance"),
            InlineKeyboardButton("💳 Deposit", callback_data="deposit")
        ],
        [
            InlineKeyboardButton("📊 History", callback_data="history"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    
    if str(user.id) == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🎯 **Main Menu**\n\n"
        f"Welcome back, {user.first_name}!\n"
        f"💰 Balance: **{balance:.2f} ETB**\n\n"
        f"Choose an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

# ==================== BOT SETUP ====================

async def setup_bot():
    """Initialize and start the Telegram bot"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Payment conversation handler
    payment_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(deposit_command, pattern='^deposit$')],
        states={
            PHONE_NUMBER: [CallbackQueryHandler(payment_method_selected, pattern='^pay_')],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_number)],
            REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(main_menu_callback, pattern='^main_menu$')],
        name="payment_conversation",
        persistent=False,
        allow_reentry=True
    )
    
    # Add handlers with CORRECT callback patterns
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(payment_conv)
    
    # Main menu handlers - FIXED PATTERNS
    application.add_handler(CallbackQueryHandler(play_callback, pattern="^play$"))
    application.add_handler(CallbackQueryHandler(balance_callback, pattern="^balance$"))
    application.add_handler(CallbackQueryHandler(deposit_command, pattern="^deposit$"))
    application.add_handler(CallbackQueryHandler(history_callback, pattern="^history$"))
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin$"))
    application.add_handler(CallbackQueryHandler(admin_pending_payments, pattern="^admin_pending_payments$"))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    application.add_handler(CallbackQueryHandler(approve_payment_callback, pattern="^approve_pay_"))
    application.add_handler(CallbackQueryHandler(reject_payment_callback, pattern="^reject_pay_"))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reference))
    
    # Initialize and start
    await application.initialize()
    await application.start()
    
    # Delete webhook and use polling
    await application.bot.delete_webhook()
    await application.updater.start_polling()
    
    logger.info("🤖 Telegram bot started successfully")
    
    return application

async def shutdown_bot(application):
    """Shutdown the bot gracefully"""
    if application:
        logger.info("🛑 Stopping bot...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

# ==================== LIFESPAN MANAGEMENT ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting up...")
    game_manager.bot_app = await setup_bot()
    yield
    # Shutdown
    logger.info("🛑 Shutting down...")
    await shutdown_bot(game_manager.bot_app)

# Create FastAPI app with lifespan
app = FastAPI(title="Bingo Game", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== FASTAPI ROUTES ====================

@app.get("/")
async def root():
    return {
        "status": "online",
        "cards": len(BINGO_CARDS),
        "price_per_card": CARD_PRICE / 100,
        "max_cards_per_player": MAX_CARDS_PER_PLAYER,
        "welcome_bonus": WELCOME_BONUS / 100
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/cards")
async def get_cards():
    return {
        "total": len(BINGO_CARDS),
        "cards": [{"id": c["id"]} for c in BINGO_CARDS],
        "price_per_card": CARD_PRICE / 100
    }

@app.get("/api/card/{card_id}")
async def get_card(card_id: int):
    card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
    if card:
        return card
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
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
        "games_won": user['games_won']
    }

@app.get("/api/patterns")
async def list_patterns():
    """Return list of all bingo patterns"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, description FROM patterns ORDER BY id")
        rows = cursor.fetchall()
        return [{"id": r[0], "name": r[1], "description": r[2]} for r in rows]

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    user = db.get_or_create_user(user_id)
    active_games = db.get_active_games_count(user_id)
    total_stake = db.get_total_stake(user_id)
    
    return templates.TemplateResponse("bingo.html", {
        "request": request,
        "user_id": user_id,
        "game_id": game_id,
        "total_cards": len(BINGO_CARDS),
        "admin_id": ADMIN_USER_ID,
        "price_per_card": CARD_PRICE / 100,
        "max_cards": MAX_CARDS_PER_PLAYER,
        "welcome_bonus": WELCOME_BONUS / 100,
        "initial_balance": user['balance'] / 100,
        "initial_active_games": active_games,
        "initial_stake": total_stake / 100
    })

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    await game_manager.connect(game_id, websocket, user_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            logger.info(f"Received: {data['type']} from user {user_id}")
            
            if data['type'] == 'select_cards':
                success, message, cost = await game_manager.select_cards(
                    game_id, user_id, data['card_ids']
                )
                await websocket.send_json({
                    'type': 'cards_selected',
                    'success': success,
                    'message': message,
                    'cost': cost,
                    'card_ids': data['card_ids'] if success else []
                })
                
                if success:
                    for card_id in data['card_ids']:
                        card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                        if card:
                            await websocket.send_json({
                                'type': 'your_card',
                                'card': card['card'],
                                'card_id': card_id
                            })
            
            elif data['type'] == 'finalize':
                success, message = await game_manager.finalize_selection(game_id, user_id)
                await websocket.send_json({
                    'type': 'finalized',
                    'success': success,
                    'message': message
                })
                
                if success:
                    stats = await game_manager.get_user_stats(user_id)
                    await websocket.send_json({
                        'type': 'stats_update',
                        **stats
                    })
            
            elif data['type'] == 'start_game':
                success, message = await game_manager.start_game(game_id, user_id)
                await websocket.send_json({
                    'type': 'start_result',
                    'success': success,
                    'message': message
                })
            
            elif data['type'] == 'set_pattern':
                if user_id != int(ADMIN_USER_ID):
                    await websocket.send_json({'type': 'error', 'message': 'Not authorized'})
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
            
            elif data['type'] == 'get_stats':
                stats = await game_manager.get_user_stats(user_id)
                await websocket.send_json({
                    'type': 'stats_update',
                    **stats
                })
            
            elif data['type'] == 'claim_bingo':
                # Handle bingo claim
                card_id = data.get('card_id')
                if card_id:
                    # Check if bingo is valid
                    player = game_manager.active_games[game_id]['players'].get(user_id)
                    if player:
                        card_index = player['card_ids'].index(card_id) if card_id in player['card_ids'] else -1
                        if card_index >= 0:
                            card = player['cards'][card_index]
                            marked_set = set(player['marked'].get(card_id, []))
                            
                            # Add FREE space
                            if card[2][2] == 'FREE':
                                marked_set.add('FREE')
                            
                            # Get current pattern
                            with db.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute("SELECT pattern_id FROM games WHERE id = ?", (game_id,))
                                pattern_id = cursor.fetchone()[0]
                                cursor.execute("SELECT positions FROM patterns WHERE id = ?", (pattern_id,))
                                pattern_data = cursor.fetchone()[0]
                            
                            # Check bingo using pattern
                            if game_manager.check_card_bingo(card, marked_set):
                                await game_manager.declare_winner(game_id, user_id, card_id, called[-1] if called else 0)
                            else:
                                await websocket.send_json({
                                    'type': 'error',
                                    'message': 'Not a valid bingo pattern'
                                })
            
            elif data['type'] == 'ping':
                await websocket.send_json({'type': 'pong'})
                
    except WebSocketDisconnect:
        game_manager.disconnect(game_id, websocket, user_id)
        logger.info(f"User {user_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

@app.post("/api/join-game")
async def join_game(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    game_id = data.get('game_id')
    card_count = data.get('card_count', 1)
    
    total_cost = card_count * CARD_PRICE
    
    result = db.update_balance(
        user_id=user_id,
        amount=-total_cost,
        transaction_type='game_fee',
        description=f'Joined game #{game_id} with {card_count} cards'
    )
    
    if result:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET games_played = games_played + 1 WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
        
        return JSONResponse({
            'success': True,
            'balance': result['new_balance'],
            'balance_etb': result['new_balance'] / 100,
            'cost': total_cost,
            'card_count': card_count
        })
    
    return JSONResponse({
        'success': False,
        'error': 'Insufficient balance'
    }, status_code=400)

@app.get("/api/game-state/{game_id}")
async def get_game_state(game_id: int):
    if game_id in game_manager.active_games:
        game = game_manager.active_games[game_id]
        return {
            'players': game_manager.get_players(game_id),
            'started': game_manager.game_started.get(game_id, False),
            'winner': game_manager.game_winner.get(game_id),
            'round': game_manager.round_number.get(game_id, 1),
            'called_numbers': game['called_numbers'],
            'prize_pool': game['prize_pool'] / 100,
            'total_cards': game['total_cards_sold'],
            'countdown': game_manager.countdown_timers.get(game_id, 15)
        }
    return {'error': 'Game not found'}

# Quick add funds endpoint for testing (remove in production)
@app.get("/api/add-funds/{user_id}/{amount}")
async def add_funds(user_id: int, amount: int):
    """Quick endpoint to add funds for testing"""
    result = db.update_balance(
        user_id=user_id,
        amount=amount * 100,  # Convert to cents
        transaction_type='deposit',
        description='Test deposit'
    )
    if result:
        return {"success": True, "new_balance": result['new_balance'] / 100}
    return {"success": False}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)