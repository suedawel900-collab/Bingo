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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="Bingo WebApp")

# Setup templates and static files
templates = Jinja2Templates(directory="templates")
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize database
db = Database()

# Admin ID from environment
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID', '8741250511')
CARD_PRICE = 1000  # 10 ETB per card in cents
MAX_CARDS_PER_PLAYER = 20
WELCOME_BONUS = 1000  # 10 ETB welcome bonus

# Load pre-generated cards from JSON file
CARDS_FILE = "static/bingo_cards.json"

# Generate default cards if file doesn't exist
def generate_default_cards():
    cards = []
    for i in range(1, 101):  # Generate 100 cards for now
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

# Game manager with database persistence
class GameManager:
    def __init__(self):
        self.active_connections = {}  # game_id -> list of websockets
        self.number_tasks = {}         # game_id -> asyncio task
        self.countdown_timers = {}     # game_id -> timer task

    async def connect(self, game_id: int, websocket: WebSocket, user_id: int):
        await websocket.accept()
        logger.info(f"User {user_id} connected to game {game_id}")

        # Get or create user
        user = db.get_or_create_user(user_id)

        # Get game from DB
        game = db.get_game(game_id)
        if not game:
            await websocket.send_json({"type": "error", "message": "Game not found"})
            await websocket.close()
            return

        # Add to active connections
        if game_id not in self.active_connections:
            self.active_connections[game_id] = []
        self.active_connections[game_id].append(websocket)

        # Start countdown timer for this game if not already running
        if game_id not in self.countdown_timers:
            self.countdown_timers[game_id] = asyncio.create_task(self.run_countdown(game_id))

        # Get players for this game
        players = db.get_game_players(game_id)
        player_list = []
        taken_cards = set()
        for p in players:
            player_list.append({
                'id': p['user_id'],
                'name': p['first_name'] or f"Player{p['user_id']}",
                'card_count': len(p['card_ids']),
                'ready': p['ready']
            })
            taken_cards.update(p['card_ids'])

        # Get called numbers
        called_numbers = json.loads(game['called_numbers']) if game['called_numbers'] else []

        # Get user's active games and stake
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)

        # Send current state
        await websocket.send_json({
            'type': 'connected',
            'taken_cards': list(taken_cards),
            'players': player_list,
            'round': game['round_number'],
            'game_started': game['status'] == 'active',
            'winner': game['winner_user_id'],
            'called_numbers': called_numbers,
            'countdown': 15,  # will be updated by timer
            'balance': user['balance'] / 100,
            'active_games': active_games,
            'total_stake': total_stake / 100
        })

        # Notify others
        await self.broadcast(game_id, {
            'type': 'player_joined',
            'players': player_list,
            'taken_cards': list(taken_cards)
        })

    async def run_countdown(self, game_id: int):
        """Run countdown timer for a game (15 seconds)"""
        count = 15
        while True:
            await asyncio.sleep(1)
            count -= 1
            if count < 0:
                count = 15
            await self.broadcast(game_id, {'type': 'countdown', 'time': count})
            # If game ended, break
            game = db.get_game(game_id)
            if not game or game['status'] == 'completed':
                break

    def disconnect(self, game_id: int, websocket: WebSocket, user_id: int):
        if game_id in self.active_connections:
            if websocket in self.active_connections[game_id]:
                self.active_connections[game_id].remove(websocket)
            if not self.active_connections[game_id]:
                del self.active_connections[game_id]
                # Stop timer if no connections
                if game_id in self.countdown_timers:
                    self.countdown_timers[game_id].cancel()
                    del self.countdown_timers[game_id]
        logger.info(f"User {user_id} disconnected from game {game_id}")

    async def broadcast(self, game_id: int, message: dict):
        if game_id in self.active_connections:
            for conn in self.active_connections[game_id][:]:
                try:
                    await conn.send_json(message)
                except:
                    if conn in self.active_connections[game_id]:
                        self.active_connections[game_id].remove(conn)

    async def select_cards(self, game_id: int, user_id: int, card_ids: List[int]):
        """Select multiple cards at once"""
        game = db.get_game(game_id)
        if not game:
            return False, "Game not found", 0
        if game['status'] != 'waiting':
            return False, "Game already started or ended", 0

        user = db.get_user(user_id)
        if not user:
            return False, "User not found", 0

        # Check if player already has cards in this game
        players = db.get_game_players(game_id)
        player = next((p for p in players if p['user_id'] == user_id), None)
        if player and len(player['card_ids']) + len(card_ids) > MAX_CARDS_PER_PLAYER:
            return False, f"Maximum {MAX_CARDS_PER_PLAYER} cards per player", 0

        # Check if cards are available (not taken by others)
        taken_cards = set()
        for p in players:
            if p['user_id'] != user_id:
                taken_cards.update(p['card_ids'])
        for cid in card_ids:
            if cid in taken_cards:
                return False, f"Card {cid} already taken", 0

        # Calculate total cost
        total_cost = len(card_ids) * CARD_PRICE

        # Check balance
        if user['balance'] < total_cost:
            return False, f"Insufficient balance. Need {total_cost/100} ETB", total_cost

        # Prepare marked numbers for new cards
        if player:
            existing_card_ids = player['card_ids']
            marked = player['marked_numbers']
        else:
            existing_card_ids = []
            marked = {}

        for cid in card_ids:
            marked[str(cid)] = []

        all_card_ids = existing_card_ids + card_ids

        # Save to DB (upsert)
        db.add_player_to_game(game_id, user_id, all_card_ids, marked)

        # Do NOT update prize pool here – only after payment (finalize)
        logger.info(f"User {user_id} selected {len(card_ids)} cards in game {game_id}")

        return True, f"Selected {len(card_ids)} cards", total_cost

    async def finalize_selection(self, game_id: int, user_id: int):
        """Finalize card selection and deduct balance"""
        game = db.get_game(game_id)
        if not game:
            return False, "Game not found"
        if game['status'] != 'waiting':
            return False, "Game already started"

        players = db.get_game_players(game_id)
        player = next((p for p in players if p['user_id'] == user_id), None)
        if not player:
            return False, "No cards selected"

        if player['ready']:
            return False, "Already ready"

        # Deduct balance from database
        total_cost = len(player['card_ids']) * CARD_PRICE
        result = db.update_balance(
            user_id=user_id,
            amount=-total_cost,
            transaction_type='game_fee',
            description=f'Joined game #{game_id} with {len(player["card_ids"])} cards'
        )

        if not result:
            return False, "Failed to deduct balance"

        # Track active game
        db.add_active_game(user_id, game_id, player['card_ids'], total_cost)

        # Mark player as ready
        db.set_player_ready(game_id, user_id, True)

        # Update prize pool based on READY players only
        all_players = db.get_game_players(game_id)
        total_cards = sum(len(p['card_ids']) for p in all_players if p['ready'])
        prize_pool = total_cards * CARD_PRICE
        db.update_game_state(game_id, prize_pool=prize_pool)

        # Broadcast player ready
        await self.broadcast(game_id, {
            'type': 'player_ready',
            'players': self._get_player_list(game_id),
            'user_id': user_id,
            'card_count': len(player['card_ids'])
        })

        return True, "Ready to play"

    def _get_player_list(self, game_id):
        players = db.get_game_players(game_id)
        return [{
            'id': p['user_id'],
            'name': p['first_name'] or f"Player{p['user_id']}",
            'card_count': len(p['card_ids']),
            'ready': p['ready']
        } for p in players]

    async def start_game(self, game_id: int, user_id: int):
        """Start the game (admin only)"""
        if str(user_id) != ADMIN_USER_ID:
            return False, "Not authorized"

        game = db.get_game(game_id)
        if not game:
            return False, "Game not found"
        if game['status'] != 'waiting':
            return False, "Game already started or ended"

        # Check if any players are ready
        players = db.get_game_players(game_id)
        ready_count = sum(1 for p in players if p['ready'])
        if ready_count < 1:
            return False, "No players ready"

        # Start the game
        db.update_game_state(game_id, status='active', called_numbers='[]')

        # Start number generation task
        if game_id in self.number_tasks:
            self.number_tasks[game_id].cancel()
        self.number_tasks[game_id] = asyncio.create_task(self.generate_numbers(game_id))

        await self.broadcast(game_id, {
            'type': 'game_started',
            'round': game['round_number']
        })
        logger.info(f"Game {game_id} started by admin")

        return True, "Game started"

    async def generate_numbers(self, game_id: int):
        """Generate numbers every 2 seconds"""
        try:
            while True:
                await asyncio.sleep(2)

                game = db.get_game(game_id)
                if not game or game['status'] != 'active':
                    break

                called = json.loads(game['called_numbers'])
                available = [n for n in range(1, 76) if n not in called]

                if not available:
                    # No more numbers – game over
                    await self.broadcast(game_id, {'type': 'game_over', 'message': 'All numbers called'})
                    db.update_game_state(game_id, status='completed', completed_at=datetime.now())
                    break

                number = random.choice(available)
                called.append(number)
                db.update_game_state(game_id, called_numbers=json.dumps(called))

                # Check for winners
                await self.check_winners(game_id, number)

                await self.broadcast(game_id, {
                    'type': 'number_called',
                    'number': number,
                    'called': called,
                    'left': len(available) - 1
                })

        except asyncio.CancelledError:
            logger.info(f"Number generation stopped for game {game_id}")
        except Exception as e:
            logger.error(f"Error in number generation: {e}")

    async def check_winners(self, game_id: int, last_number: int):
        """Check if anyone won after a number is called"""
        game = db.get_game(game_id)
        if not game or game['status'] != 'active' or game['winner_user_id']:
            return

        players = db.get_game_players(game_id)
        called = set(json.loads(game['called_numbers']))

        for player in players:
            if not player['ready']:
                continue
            card_ids = player['card_ids']
            marked_dict = player['marked_numbers']
            for card_id in card_ids:
                marked = set(marked_dict.get(str(card_id), []))
                # Get card from JSON
                card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                if not card_data:
                    continue
                card = card_data['card']
                # Add FREE space
                if card[2][2] == 'FREE':
                    marked.add('FREE')
                if self._check_card_bingo(card, marked):
                    await self.declare_winner(game_id, player['user_id'], card_id, last_number)
                    return

    def _check_card_bingo(self, card, marked):
        """Check if a single card has bingo"""
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

    async def declare_winner(self, game_id: int, user_id: int, card_id: int, winning_number: int = None):
        """Declare winner and stop the game"""
        game = db.get_game(game_id)
        if not game or game['winner_user_id']:
            return

        player = next((p for p in db.get_game_players(game_id) if p['user_id'] == user_id), None)
        if not player:
            return

        # If winning_number not provided, use the last called number
        if winning_number is None:
            called = json.loads(game['called_numbers'])
            winning_number = called[-1] if called else 0

        # Prize pool is already based on ready players
        prize_pool = game['prize_pool']
        winner_prize = int(prize_pool * 0.9)
        house_fee = prize_pool - winner_prize

        # Update game state
        db.update_game_state(
            game_id,
            status='completed',
            winner_user_id=user_id,
            winner_card_id=card_id,
            winning_number=winning_number,
            completed_at=datetime.now()
        )

        # Add prize to winner's balance
        db.update_balance(
            user_id=user_id,
            amount=winner_prize,
            transaction_type='game_win',
            description=f'Won round {game["round_number"]} in game #{game_id} with card #{card_id}'
        )

        # Broadcast winner
        await self.broadcast(game_id, {
            'type': 'game_won',
            'winner': {
                'user_id': user_id,
                'name': player.get('first_name') or f"Player{user_id}",
                'card_id': card_id,
                'winning_number': winning_number,
                'round': game['round_number']
            },
            'prize': winner_prize / 100,
            'house_fee': house_fee / 100
        })

        logger.info(f"Game {game_id} winner: {player.get('first_name')} with card #{card_id}, prize: {winner_prize/100} ETB")

        # Cancel number generation
        if game_id in self.number_tasks:
            self.number_tasks[game_id].cancel()
            del self.number_tasks[game_id]

    async def start_new_round(self, old_game_id: int):
        """Create a new game (next round) and notify players"""
        old_game = db.get_game(old_game_id)
        if not old_game:
            return None

        # Create new game with incremented round number
        new_round = old_game['round_number'] + 1
        new_game_id = db.create_game(round_number=new_round)

        # Broadcast reset message to all connected clients
        await self.broadcast(old_game_id, {
            'type': 'game_reset',
            'round': new_round,
            'players': []  # Players will need to reselect cards
        })

        logger.info(f"New round {new_round} created with game ID {new_game_id}")
        return new_game_id

    async def mark_number(self, game_id: int, user_id: int, card_id: int, number: int):
        """Mark number on a specific card"""
        game = db.get_game(game_id)
        if not game or game['status'] != 'active':
            return False

        # Check if number is called
        called = set(json.loads(game['called_numbers']))
        if number not in called:
            return False

        # Get player
        players = db.get_game_players(game_id)
        player = next((p for p in players if p['user_id'] == user_id), None)
        if not player:
            return False

        marked_dict = player['marked_numbers']
        marked_list = marked_dict.get(str(card_id), [])
        if number in marked_list:
            return False

        marked_list.append(number)
        marked_dict[str(card_id)] = marked_list

        # Update DB
        db.update_player_marked(game_id, user_id, card_id, marked_list)

        return True

    async def claim_bingo(self, game_id: int, user_id: int, card_id: int):
        """Manual bingo claim from player"""
        game = db.get_game(game_id)
        if not game or game['status'] != 'active' or game['winner_user_id']:
            return False, "Game not active or already won"

        players = db.get_game_players(game_id)
        player = next((p for p in players if p['user_id'] == user_id), None)
        if not player or not player['ready']:
            return False, "You are not ready or have no cards"

        if card_id not in player['card_ids']:
            return False, "Card not yours"

        marked = set(player['marked_numbers'].get(str(card_id), []))
        card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
        if not card_data:
            return False, "Card not found"

        card = card_data['card']
        if card[2][2] == 'FREE':
            marked.add('FREE')

        if self._check_card_bingo(card, marked):
            # Declare winner with the last called number
            called = json.loads(game['called_numbers'])
            last_num = called[-1] if called else 0
            await self.declare_winner(game_id, user_id, card_id, last_num)
            return True, "Bingo confirmed! You win!"
        else:
            return False, "Not a valid bingo"

    async def get_user_stats(self, user_id: int):
        """Get user statistics"""
        user = db.get_user(user_id)
        if not user:
            return {}
        active_games = db.get_active_games_count(user_id)
        total_stake = db.get_total_stake(user_id)
        return {
            'balance': user['balance'] / 100,
            'active_games': active_games,
            'total_stake': total_stake / 100,
            'games_played': user['games_played'],
            'games_won': user['games_won']
        }

# Global game manager instance
game_manager = GameManager()

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
        "games_won": user['games_won'],
        "welcome_bonus_claimed": user['welcome_bonus_claimed']
    }

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    user = db.get_or_create_user(user_id)
    active_games = db.get_active_games_count(user_id)
    total_stake = db.get_total_stake(user_id)

    # Get current round for this game
    game = db.get_game(game_id)
    current_round = game['round_number'] if game else 1

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
        "initial_stake": total_stake / 100,
        "initial_round": current_round
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
                    # Send each selected card
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

                # Send updated stats
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

            elif data['type'] == 'mark_number':
                success = await game_manager.mark_number(
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
                if not card_id:
                    await websocket.send_json({'type': 'error', 'message': 'Card ID required'})
                    continue
                success, message = await game_manager.claim_bingo(game_id, user_id, card_id)
                if success:
                    await websocket.send_json({'type': 'bingo_result', 'success': True, 'message': message})
                else:
                    await websocket.send_json({'type': 'bingo_result', 'success': False, 'message': message})

            elif data['type'] == 'get_stats':
                stats = await game_manager.get_user_stats(user_id)
                await websocket.send_json({
                    'type': 'stats_update',
                    **stats
                })

            elif data['type'] == 'ping':
                await websocket.send_json({'type': 'pong'})

            elif data['type'] == 'start_next_round':
                if str(user_id) == ADMIN_USER_ID:
                    new_game_id = await game_manager.start_new_round(game_id)
                    if new_game_id:
                        await websocket.send_json({
                            'type': 'redirect',
                            'url': f"/game?user_id={ADMIN_USER_ID}&game_id={new_game_id}"
                        })
                    else:
                        await websocket.send_json({'type': 'error', 'message': 'Could not create new round'})
                else:
                    await websocket.send_json({'type': 'error', 'message': 'Not authorized'})

    except WebSocketDisconnect:
        game_manager.disconnect(game_id, websocket, user_id)
        logger.info(f"User {user_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

@app.post("/api/join-game")
async def join_game(request: Request):
    """Deduct card costs when player finalizes selection (already handled via WebSocket)"""
    # This endpoint might be deprecated, but keep for backward compatibility
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
        # Update games played
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
    game = db.get_game(game_id)
    if not game:
        return {'error': 'Game not found'}
    players = db.get_game_players(game_id)
    player_list = [{
        'id': p['user_id'],
        'name': p['first_name'] or f"Player{p['user_id']}",
        'card_count': len(p['card_ids']),
        'ready': p['ready']
    } for p in players]

    return {
        'players': player_list,
        'started': game['status'] == 'active',
        'winner': game['winner_user_id'],
        'round': game['round_number'],
        'called_numbers': json.loads(game['called_numbers']) if game['called_numbers'] else [],
        'prize_pool': game['prize_pool'] / 100 if game['prize_pool'] else 0,
        'total_cards': sum(len(p['card_ids']) for p in players if p['ready']),  # only ready
        'countdown': 15  # not stored, but broadcast via timer
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)