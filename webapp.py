import os
import json
import random
import asyncio
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
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
# Create static directory if it doesn't exist
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize database
db = Database()

# Admin ID from environment
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')

# Load pre-generated cards from JSON file
CARDS_FILE = "static/bingo_cards.json"

# Default cards if file doesn't exist
DEFAULT_CARDS = []
for i in range(1, 101):  # Generate 100 sample cards
    card = []
    for col in range(5):
        column = []
        min_num = col * 15 + 1
        max_num = (col + 1) * 15
        numbers = random.sample(range(min_num, max_num + 1), 5)
        column.extend(numbers)
        card.append(column)
    card[2][2] = "FREE"
    DEFAULT_CARDS.append({
        "id": i,
        "card": card
    })

# Load or create cards
def load_cards():
    """Load pre-generated bingo cards"""
    try:
        if os.path.exists(CARDS_FILE):
            with open(CARDS_FILE, 'r') as f:
                cards = json.load(f)
                logger.info(f"✅ Loaded {len(cards)} cards from {CARDS_FILE}")
                return cards
        else:
            logger.warning(f"⚠️ Cards file not found, using {len(DEFAULT_CARDS)} default cards")
            # Create cards file
            os.makedirs(os.path.dirname(CARDS_FILE) or '.', exist_ok=True)
            with open(CARDS_FILE, 'w') as f:
                json.dump(DEFAULT_CARDS, f)
            return DEFAULT_CARDS
    except Exception as e:
        logger.error(f"Error loading cards: {e}")
        return DEFAULT_CARDS

# Global cards list
BINGO_CARDS = load_cards()

# Game manager for WebSocket connections
class GameManager:
    def __init__(self):
        self.active_games: Dict[int, Dict[str, Any]] = {}
        self.game_connections: Dict[int, List[WebSocket]] = {}
        self.number_call_tasks: Dict[int, asyncio.Task] = {}
        self.taken_cards: Dict[int, Set[int]] = {}  # Track taken cards per game
        self.game_started: Dict[int, bool] = {}  # Track if game has started
        
    async def connect_to_game(self, game_id: int, websocket: WebSocket, user_id: int):
        """Connect user to game"""
        try:
            await websocket.accept()
            logger.info(f"User {user_id} connected to game {game_id}")
            
            self.game_connections.setdefault(game_id, []).append(websocket)
            self.taken_cards.setdefault(game_id, set())
            self.game_started.setdefault(game_id, False)
            
            # Initialize game if not exists
            if game_id not in self.active_games:
                self.active_games[game_id] = {
                    'called_numbers': [],
                    'players': {},
                    'winner': None,
                    'prize_pool': 0,
                    'admin_id': ADMIN_USER_ID
                }
            
            # Add player
            user = db.get_user(user_id)
            player_name = user['first_name'] if user and 'first_name' in user.keys() else f"Player{user_id}"
            self.active_games[game_id]['players'][user_id] = {
                'name': player_name,
                'card': None,
                'card_id': None,
                'marked_numbers': [],
                'is_winner': False,
                'ready': False
            }
            
            # Update prize pool
            player_count = len(self.active_games[game_id]['players'])
            self.active_games[game_id]['prize_pool'] = player_count * 2000  # 20 ETB in cents
            
            # Send current taken cards to new player
            await websocket.send_json({
                'type': 'card_taken',
                'taken_cards': list(self.taken_cards[game_id])
            })
            
            # Send game state to new player
            await websocket.send_json({
                'type': 'game_state',
                'players': self.get_players_list(game_id),
                'prize_pool': self.active_games[game_id]['prize_pool'] / 100,
                'game_started': self.game_started[game_id],
                'called_numbers': self.active_games[game_id]['called_numbers']
            })
            
            # Notify all players about new player
            await self.broadcast_to_game(game_id, {
                'type': 'player_joined',
                'players': self.get_players_list(game_id),
                'player_id': user_id,
                'prize_pool': self.active_games[game_id]['prize_pool'] / 100,
                'taken_cards': list(self.taken_cards[game_id])
            })
            
            logger.info(f"User {user_id} joined game {game_id}. Total players: {player_count}")
            
        except Exception as e:
            logger.error(f"Error connecting user {user_id} to game {game_id}: {e}")
    
    def disconnect_from_game(self, game_id: int, websocket: WebSocket, user_id: int):
        """Disconnect user from game"""
        try:
            if game_id in self.game_connections:
                if websocket in self.game_connections[game_id]:
                    self.game_connections[game_id].remove(websocket)
                
                # Remove player from active game
                if game_id in self.active_games:
                    if user_id in self.active_games[game_id]['players']:
                        player = self.active_games[game_id]['players'][user_id]
                        # Free up their card
                        if player['card_id']:
                            self.taken_cards[game_id].discard(player['card_id'])
                        del self.active_games[game_id]['players'][user_id]
                        
                        # Update prize pool
                        player_count = len(self.active_games[game_id]['players'])
                        self.active_games[game_id]['prize_pool'] = player_count * 2000
                
                # Stop number calling if no players left
                if len(self.game_connections.get(game_id, [])) == 0:
                    if game_id in self.number_call_tasks:
                        self.number_call_tasks[game_id].cancel()
                        del self.number_call_tasks[game_id]
                        logger.info(f"Stopped number calling for game {game_id} - no players")
                        
            logger.info(f"User {user_id} disconnected from game {game_id}")
            
        except Exception as e:
            logger.error(f"Error disconnecting user {user_id} from game {game_id}: {e}")
    
    async def broadcast_to_game(self, game_id: int, message: Dict[str, Any]):
        """Broadcast message to all players in game"""
        if game_id in self.game_connections:
            disconnected = []
            for connection in self.game_connections[game_id]:
                try:
                    await connection.send_json(message)
                except:
                    disconnected.append(connection)
            
            # Remove disconnected connections
            for conn in disconnected:
                if conn in self.game_connections[game_id]:
                    self.game_connections[game_id].remove(conn)
    
    async def handle_select_card(self, game_id: int, user_id: int, card_id: int, websocket: WebSocket):
        """Handle card selection with better error handling"""
        try:
            logger.info(f"User {user_id} attempting to select card #{card_id} in game {game_id}")
            
            # Check if game exists
            if game_id not in self.active_games:
                await websocket.send_json({
                    'type': 'error',
                    'message': 'Game not found'
                })
                return False
            
            # Check if game already started
            if self.game_started.get(game_id, False):
                await websocket.send_json({
                    'type': 'error',
                    'message': 'Game already started, cannot select card now'
                })
                return False
            
            # Check if card is already taken
            if card_id in self.taken_cards.get(game_id, set()):
                await websocket.send_json({
                    'type': 'error',
                    'message': f'Card #{card_id} is already taken by another player'
                })
                return False
            
            # Check if player exists
            if user_id not in self.active_games[game_id]['players']:
                await websocket.send_json({
                    'type': 'error',
                    'message': 'Player not found in game'
                })
                return False
            
            # Check if player already has a card
            if self.active_games[game_id]['players'][user_id]['card_id']:
                await websocket.send_json({
                    'type': 'error',
                    'message': 'You already have a card selected'
                })
                return False
            
            # Find the card
            card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
            if not card_data:
                await websocket.send_json({
                    'type': 'error',
                    'message': f'Card #{card_id} not found'
                })
                return False
            
            # Mark card as taken
            self.taken_cards.setdefault(game_id, set()).add(card_id)
            
            # Assign to player
            player = self.active_games[game_id]['players'][user_id]
            player['card'] = card_data['card']
            player['card_id'] = card_id
            player['ready'] = True
            
            logger.info(f"User {user_id} successfully selected card #{card_id}")
            
            # Send success message
            await websocket.send_json({
                'type': 'card_selected',
                'success': True,
                'card_id': card_id
            })
            
            # Send the actual card
            await websocket.send_json({
                'type': 'your_card',
                'card': card_data['card'],
                'card_id': card_id
            })
            
            # Broadcast updated taken cards to all players
            await self.broadcast_to_game(game_id, {
                'type': 'card_taken',
                'taken_cards': list(self.taken_cards[game_id])
            })
            
            # Update players list
            await self.broadcast_to_game(game_id, {
                'type': 'players_updated',
                'players': self.get_players_list(game_id),
                'taken_cards': list(self.taken_cards[game_id])
            })
            
            return True
            
        except Exception as e:
            logger.error(f"Error selecting card: {e}")
            await websocket.send_json({
                'type': 'error',
                'message': f'Error selecting card: {str(e)}'
            })
            return False
    
    def get_players_list(self, game_id: int) -> List[Dict[str, Any]]:
        """Get list of players in game"""
        if game_id not in self.active_games:
            return []
        
        players = []
        for user_id, data in self.active_games[game_id]['players'].items():
            players.append({
                'id': user_id,
                'name': data['name'],
                'card_id': data.get('card_id'),
                'is_winner': data.get('is_winner', False),
                'ready': data.get('ready', False)
            })
        return players
    
    def start_game(self, game_id: int, admin_id: int):
        """Start the game (admin only)"""
        try:
            if game_id not in self.active_games:
                logger.warning(f"Game {game_id} not found")
                return False
            
            # Check if user is admin
            if str(admin_id) != ADMIN_USER_ID:
                logger.warning(f"User {admin_id} is not admin")
                return False
            
            # Check if game already started
            if self.game_started[game_id]:
                logger.warning(f"Game {game_id} already started")
                return False
            
            # Check if enough players (at least 1 with card)
            players = self.active_games[game_id]['players']
            ready_players = [p for p in players.values() if p['ready']]
            
            if len(ready_players) < 1:
                logger.warning(f"Game {game_id} cannot start - no players ready")
                return False
            
            # Start the game
            self.game_started[game_id] = True
            
            # Start number calling
            self.number_call_tasks[game_id] = asyncio.create_task(
                self.call_numbers_periodically(game_id)
            )
            
            logger.info(f"Game {game_id} started by admin {admin_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error starting game {game_id}: {e}")
            return False
    
    async def call_numbers_periodically(self, game_id: int):
        """Call numbers every 2 seconds"""
        try:
            logger.info(f"Started number calling for game {game_id}")
            
            while True:
                await asyncio.sleep(2)  # Call number every 2 seconds
                
                if game_id in self.active_games and self.game_started.get(game_id, False):
                    game = self.active_games[game_id]
                    
                    # Generate random number 1-75 not called yet
                    available_numbers = [n for n in range(1, 76) 
                                       if n not in game['called_numbers']]
                    
                    if available_numbers:
                        number = random.choice(available_numbers)
                        game['called_numbers'].append(number)
                        
                        # Broadcast to all players
                        await self.broadcast_to_game(game_id, {
                            'type': 'number_called',
                            'number': number,
                            'called_numbers': game['called_numbers'],
                            'numbers_left': len(available_numbers) - 1
                        })
                        
                        logger.info(f"Game {game_id} called number: {number} ({len(available_numbers)-1} numbers left)")
                    else:
                        # All numbers called, game over
                        await self.broadcast_to_game(game_id, {
                            'type': 'game_over',
                            'message': 'All numbers have been called!'
                        })
                        break
                        
        except asyncio.CancelledError:
            logger.info(f"Number calling stopped for game {game_id}")
        except Exception as e:
            logger.error(f"Error in number calling for game {game_id}: {e}")
    
    def mark_number(self, game_id: int, user_id: int, number: int):
        """Mark number for player"""
        try:
            if game_id in self.active_games:
                if user_id in self.active_games[game_id]['players']:
                    player = self.active_games[game_id]['players'][user_id]
                    if player['card'] and number not in player['marked_numbers']:
                        player['marked_numbers'].append(number)
                        return True
            return False
        except Exception as e:
            logger.error(f"Error marking number: {e}")
            return False
    
    def check_bingo(self, game_id: int, user_id: int, marked: List[int]) -> bool:
        """Check if player has bingo"""
        try:
            if game_id not in self.active_games:
                return False
            
            game = self.active_games[game_id]
            player = game['players'].get(user_id)
            
            if not player or not player['card']:
                return False
            
            # Check if game already has winner
            if game['winner']:
                return False
            
            card = player['card']
            
            # Check rows
            for row in range(5):
                bingo = True
                for col in range(5):
                    num = card[col][row]
                    if num != 'FREE' and num not in marked:
                        bingo = False
                        break
                if bingo:
                    self.declare_winner(game_id, user_id)
                    return True
            
            # Check columns
            for col in range(5):
                bingo = True
                for row in range(5):
                    num = card[col][row]
                    if num != 'FREE' and num not in marked:
                        bingo = False
                        break
                if bingo:
                    self.declare_winner(game_id, user_id)
                    return True
            
            # Check diagonals
            diag1 = True
            diag2 = True
            for i in range(5):
                num1 = card[i][i]
                num2 = card[4-i][i]
                
                if num1 != 'FREE' and num1 not in marked:
                    diag1 = False
                if num2 != 'FREE' and num2 not in marked:
                    diag2 = False
            
            if diag1 or diag2:
                self.declare_winner(game_id, user_id)
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking bingo: {e}")
            return False
    
    def declare_winner(self, game_id: int, user_id: int):
        """Declare winner and distribute prize"""
        try:
            if game_id in self.active_games:
                game = self.active_games[game_id]
                
                # Calculate prize (total buy-ins minus house fee)
                player_count = len(game['players'])
                prize_pool = player_count * 2000  # 20 ETB per player in cents
                house_fee = int(prize_pool * 0.1)  # 10% house fee
                winner_prize = prize_pool - house_fee
                
                # Update winner
                game['winner'] = user_id
                if user_id in game['players']:
                    game['players'][user_id]['is_winner'] = True
                
                # Add prize to winner's balance
                db.update_balance(
                    user_id=user_id,
                    amount=winner_prize,
                    transaction_type='game_win',
                    description=f'Won Bingo game #{game_id}'
                )
                
                # Update game record
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE games 
                        SET status = 'completed', 
                            prize_pool = ?,
                            ended_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (prize_pool, game_id))
                    conn.commit()
                
                # Broadcast winner to all players
                asyncio.create_task(self.broadcast_to_game(game_id, {
                    'type': 'game_won',
                    'winner_id': user_id,
                    'winner_name': game['players'][user_id]['name'],
                    'prize': f"{winner_prize/100:.2f} ETB"
                }))
                
                logger.info(f"Game {game_id} winner: User {user_id}, Prize: {winner_prize/100:.2f} ETB")
                
                # Stop number calling
                if game_id in self.number_call_tasks:
                    self.number_call_tasks[game_id].cancel()
                    del self.number_call_tasks[game_id]
                    
        except Exception as e:
            logger.error(f"Error declaring winner: {e}")

# Initialize game manager
game_manager = GameManager()

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "status": "online",
        "service": "Bingo WebApp",
        "version": "1.0.0",
        "message": "Numbers called every 2 seconds!",
        "total_cards": len(BINGO_CARDS),
        "endpoints": [
            "/game - Bingo game page",
            "/health - Health check",
            "/api/cards - Get available cards",
            "/api/card/{card_id} - Get specific card",
            "/api/join-game - Join game API",
            "/ws/{game_id}/{user_id} - WebSocket connection"
        ]
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "bingo-webapp",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/cards")
async def get_cards(page: int = 1, limit: int = 1000):
    """Get paginated list of available cards"""
    start = (page - 1) * limit
    end = start + limit
    total_pages = (len(BINGO_CARDS) + limit - 1) // limit
    
    cards = BINGO_CARDS[start:end]
    return {
        "total": len(BINGO_CARDS),
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "cards": [{"id": c["id"]} for c in cards]  # Only send IDs, not full cards
    }

@app.get("/api/card/{card_id}")
async def get_card(card_id: int):
    """Get specific card by ID"""
    card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
    if card:
        return card
    return JSONResponse({"error": "Card not found"}, status_code=404)

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = None, admin_id: str = None):
    """Serve bingo game page"""
    try:
        # Get or create game
        if not game_id:
            game = db.get_active_game()
            if game:
                game_id = game['id']
            else:
                # Create new game
                game_code = f"GAME{random.randint(1000, 9999)}"
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO games (game_code) VALUES (?)
                    ''', (game_code,))
                    game_id = cursor.lastrowid
        
        return templates.TemplateResponse(
            "bingo.html",
            {
                "request": request,
                "user_id": user_id,
                "game_id": game_id,
                "total_cards": len(BINGO_CARDS),
                "admin_id": admin_id or ADMIN_USER_ID
            }
        )
        
    except Exception as e:
        logger.error(f"Game page error: {str(e)}")
        return HTMLResponse(
            content=f"<h1>Error Loading Game</h1><p>{str(e)}</p><p>Please try again later.</p>",
            status_code=500
        )

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    """WebSocket connection for real-time game updates"""
    await game_manager.connect_to_game(game_id, websocket, user_id)
    
    try:
        while True:
            # Wait for messages from client
            data = await websocket.receive_json()
            logger.info(f"Received message from user {user_id}: {data['type']}")
            
            if data['type'] == 'select_card':
                await game_manager.handle_select_card(game_id, user_id, data['card_id'], websocket)
            
            elif data['type'] == 'start_game':
                # Check if user is admin
                success = game_manager.start_game(game_id, user_id)
                if success:
                    await websocket.send_json({
                        'type': 'game_started'
                    })
                    # Broadcast to all players that game started
                    await game_manager.broadcast_to_game(game_id, {
                        'type': 'game_started'
                    })
                else:
                    await websocket.send_json({
                        'type': 'error',
                        'message': 'Cannot start game. Need at least 1 player with card or you are not admin.'
                    })
            
            elif data['type'] == 'mark_number':
                success = game_manager.mark_number(game_id, user_id, data['number'])
                if success:
                    await websocket.send_json({
                        'type': 'number_marked',
                        'number': data['number']
                    })
            
            elif data['type'] == 'check_bingo':
                valid = game_manager.check_bingo(game_id, user_id, data['marked'])
                await websocket.send_json({
                    'type': 'bingo_result',
                    'valid': valid
                })
            
            elif data['type'] == 'ping':
                await websocket.send_json({
                    'type': 'pong',
                    'timestamp': datetime.now().isoformat()
                })
                
    except WebSocketDisconnect:
        logger.info(f"User {user_id} disconnected from game {game_id}")
        game_manager.disconnect_from_game(game_id, websocket, user_id)
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id} in game {game_id}: {str(e)}")
        try:
            await websocket.send_json({
                'type': 'error',
                'message': f'Server error: {str(e)}'
            })
        except:
            pass
        game_manager.disconnect_from_game(game_id, websocket, user_id)

@app.post("/api/join-game")
async def join_game(request: Request):
    """API endpoint to join game"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        game_id = data.get('game_id')
        
        # Check if user exists
        user_data = db.get_user(user_id)
        if not user_data:
            return JSONResponse({
                'success': False,
                'error': 'User not found'
            }, status_code=400)
        
        # Deduct game fee (20 ETB = 2000 cents)
        result = db.update_balance(
            user_id=user_id,
            amount=-2000,
            transaction_type='game_fee',
            description=f'Joined game #{game_id}'
        )
        
        if result:
            logger.info(f"User {user_id} joined game {game_id}, new balance: {result['new_balance']}")
            return JSONResponse({
                'success': True,
                'new_balance': result['new_balance']
            })
        else:
            return JSONResponse({
                'success': False,
                'error': 'Insufficient balance'
            }, status_code=400)
            
    except Exception as e:
        logger.error(f"Join game error: {str(e)}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)

@app.post("/api/mark-number")
async def mark_number(request: Request):
    """API endpoint to mark number"""
    try:
        data = await request.json()
        game_id = data.get('game_id')
        user_id = data.get('user_id')
        number = data.get('number')
        
        success = game_manager.mark_number(game_id, user_id, number)
        
        return JSONResponse({
            'success': success
        })
    except Exception as e:
        logger.error(f"Mark number error: {str(e)}")
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)

@app.post("/api/check-bingo")
async def check_bingo(request: Request):
    """API endpoint to check bingo"""
    try:
        data = await request.json()
        game_id = data.get('game_id')
        user_id = data.get('user_id')
        marked = data.get('marked')
        
        valid = game_manager.check_bingo(game_id, user_id, marked)
        
        return JSONResponse({
            'valid': valid
        })
    except Exception as e:
        logger.error(f"Check bingo error: {str(e)}")
        return JSONResponse({
            'valid': False,
            'error': str(e)
        }, status_code=500)

@app.get("/api/game-state/{game_id}")
async def get_game_state(game_id: int):
    """Get current game state"""
    try:
        if game_id in game_manager.active_games:
            game = game_manager.active_games[game_id]
            return JSONResponse({
                'called_numbers': game['called_numbers'],
                'players': game_manager.get_players_list(game_id),
                'winner': game['winner'],
                'prize_pool': game['prize_pool'] / 100,
                'game_started': game_manager.game_started.get(game_id, False),
                'taken_cards': list(game_manager.taken_cards.get(game_id, set()))
            })
        
        return JSONResponse({
            'called_numbers': [],
            'players': [],
            'winner': None,
            'prize_pool': 0,
            'game_started': False,
            'taken_cards': []
        })
    except Exception as e:
        logger.error(f"Game state error: {str(e)}")
        return JSONResponse({
            'error': str(e)
        }, status_code=500)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)