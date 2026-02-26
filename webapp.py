import os
import json
import random
import asyncio
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List, Dict, Any, Set
import logging

from models import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bingo WebApp")

templates = Jinja2Templates(directory="templates")
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

db = Database()

ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '8741250511'))
CARD_PRICE = 1000  # 10 ETB in cents
MAX_CARDS_PER_PLAYER = 20
WELCOME_BONUS = 1000
HOUSE_COMMISSION_PERCENT = 20  # 20% house fee

# Load cards
CARDS_FILE = "static/bingo_cards.json"
def generate_default_cards():
    cards = []
    for i in range(1, 101):
        card = []
        for col in range(5):
            min_num = col * 15 + 1
            max_num = (col + 1) * 15
            numbers = random.sample(range(min_num, max_num + 1), 5)
            card.append(numbers)
        card[2][2] = "FREE"
        cards.append({"id": i, "card": card})
    return cards

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
        
    async def connect(self, game_id: int, websocket: WebSocket, user_id: int):
        await websocket.accept()
        logger.info(f"User {user_id} connected to game {game_id}")
        
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
        
        player_name = user['first_name'] if user and user.get('first_name') else f"Player{user_id}"
        if user_id not in self.active_games[game_id]['players']:
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
        
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        
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
        
        asyncio.create_task(self.update_countdown(game_id))
        
        await self.broadcast(game_id, {
            'type': 'player_joined',
            'players': self.get_players(game_id),
            'taken_cards': list(self.taken_cards[game_id])
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
        
        self.active_games[game_id]['total_cards_sold'] += len(player['card_ids'])
        self.active_games[game_id]['prize_pool'] = self.active_games[game_id]['total_cards_sold'] * CARD_PRICE
        
        await self.broadcast(game_id, {
            'type': 'player_ready',
            'players': self.get_players(game_id),
            'user_id': user_id,
            'card_count': len(player['card_ids'])
        })
        return True, "Ready to play"
    
    async def start_game(self, game_id: int, user_id: int):
        if user_id != ADMIN_USER_ID:
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
        self.number_tasks[game_id] = asyncio.create_task(self.generate_numbers(game_id))
        
        await self.broadcast(game_id, {
            'type': 'game_started',
            'round': self.round_number[game_id]
        })
        logger.info(f"Game {game_id} started by admin")
        return True, "Game started"
    
    async def reset_game(self, game_id: int):
        if game_id not in self.active_games:
            return
        if self.number_tasks.get(game_id):
            self.number_tasks[game_id].cancel()
            self.number_tasks[game_id] = None
        
        self.game_started[game_id] = False
        self.game_winner[game_id] = None
        self.active_games[game_id]['called_numbers'] = []
        self.active_games[game_id]['total_cards_sold'] = 0
        self.active_games[game_id]['prize_pool'] = 0
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
                available = [n for n in range(1, 76) if n not in self.active_games[game_id]['called_numbers']]
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
                    await self.broadcast(game_id, {'type': 'game_over', 'message': 'All numbers called'})
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
                if self.check_card_bingo(card, called):
                    await self.declare_winner(game_id, user_id, card_id, last_number)
                    return
    
    def check_card_bingo(self, card, marked):
        # rows
        for row in range(5):
            if all(card[col][row] == 'FREE' or card[col][row] in marked for col in range(5)):
                return True
        # columns
        for col in range(5):
            if all(card[col][row] == 'FREE' or card[col][row] in marked for row in range(5)):
                return True
        # diagonals
        if all(card[i][i] == 'FREE' or card[i][i] in marked for i in range(5)):
            return True
        if all(card[i][4-i] == 'FREE' or card[i][4-i] in marked for i in range(5)):
            return True
        return False
    
    async def declare_winner(self, game_id: int, user_id: int, card_id: int, winning_number: int):
        # Get total paid cards from database
        total_cards = db.get_total_cards_for_game(game_id)
        prize_pool = total_cards * CARD_PRICE
        house_cut = int(prize_pool * HOUSE_COMMISSION_PERCENT / 100)
        winner_prize = prize_pool - house_cut

        # Credit winner
        result = db.update_balance(
            user_id=user_id,
            amount=winner_prize,
            transaction_type='game_win',
            description=f'Won round {self.round_number[game_id]} in game #{game_id} with card #{card_id}'
        )
        if not result:
            logger.error(f"Failed to credit winner {user_id}")
            return

        # Credit house (admin)
        db.update_balance(
            user_id=ADMIN_USER_ID,
            amount=house_cut,
            transaction_type='house_fee',
            description=f'House commission from game #{game_id}'
        )

        # Mark all active games as completed
        db.complete_all_games_for_round(game_id)

        # Update in‑memory state
        player = self.active_games[game_id]['players'][user_id]
        player['winner'] = True
        player['cards_won'] += 1
        player['balance'] = result['new_balance']

        self.game_winner[game_id] = {
            'user_id': user_id,
            'name': player['name'],
            'card_id': card_id,
            'winning_number': winning_number,
            'round': self.round_number[game_id]
        }
        self.game_started[game_id] = False

        if self.number_tasks.get(game_id):
            self.number_tasks[game_id].cancel()
            self.number_tasks[game_id] = None

        await self.broadcast(game_id, {
            'type': 'game_won',
            'winner': self.game_winner[game_id],
            'prize': winner_prize / 100,
            'house_fee': house_cut / 100
        })

        logger.info(f"Game {game_id} winner: {player['name']} with card #{card_id}, prize: {winner_prize/100} ETB")
    
    def mark_number(self, game_id: int, user_id: int, card_id: int, number: int):
        if game_id not in self.active_games or not self.game_started[game_id] or self.game_winner[game_id]:
            return False
        if user_id not in self.active_games[game_id]['players']:
            return False
        player = self.active_games[game_id]['players'][user_id]
        if card_id not in player['marked'] or number in player['marked'][card_id]:
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

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Webapp starting up...")
    try:
        db.get_system_stats()  # test DB connection
        logger.info("✅ Database connection verified")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        raise e
    logger.info("✅ Webapp ready")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {
        "status": "online",
        "cards": len(BINGO_CARDS),
        "price_per_card": CARD_PRICE / 100,
        "max_cards_per_player": MAX_CARDS_PER_PLAYER,
        "welcome_bonus": WELCOME_BONUS / 100,
        "endpoints": ["/game", "/health", "/api/cards", "/api/user/{user_id}"]
    }

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
        "games_won": user['games_won'],
        "welcome_bonus_claimed": user['welcome_bonus_claimed']
    }

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
                success, message, cost = await game_manager.select_cards(game_id, user_id, data['card_ids'])
                await websocket.send_json({'type': 'cards_selected', 'success': success, 'message': message, 'cost': cost, 'card_ids': data['card_ids'] if success else []})
                if success:
                    for card_id in data['card_ids']:
                        card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                        if card:
                            await websocket.send_json({'type': 'your_card', 'card': card['card'], 'card_id': card_id})
            elif data['type'] == 'finalize':
                success, message = await game_manager.finalize_selection(game_id, user_id)
                await websocket.send_json({'type': 'finalized', 'success': success, 'message': message})
                if success:
                    stats = await game_manager.get_user_stats(user_id)
                    await websocket.send_json({'type': 'stats_update', **stats})
            elif data['type'] == 'start_game':
                success, message = await game_manager.start_game(game_id, user_id)
                await websocket.send_json({'type': 'start_result', 'success': success, 'message': message})
            elif data['type'] == 'mark_number':
                success = game_manager.mark_number(game_id, user_id, data['card_id'], data['number'])
                if success:
                    await websocket.send_json({'type': 'number_marked', 'card_id': data['card_id'], 'number': data['number']})
            elif data['type'] == 'get_stats':
                stats = await game_manager.get_user_stats(user_id)
                await websocket.send_json({'type': 'stats_update', **stats})
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
            cursor.execute('UPDATE users SET games_played = games_played + 1 WHERE user_id = ?', (user_id,))
            conn.commit()
        return JSONResponse({'success': True, 'balance': result['new_balance'], 'balance_etb': result['new_balance']/100, 'cost': total_cost, 'card_count': card_count})
    return JSONResponse({'success': False, 'error': 'Insufficient balance'}, status_code=400)

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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)