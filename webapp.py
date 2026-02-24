import os
import json
import random
import asyncio
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List, Dict, Any
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
                return json.load(f)
        else:
            # Create cards file from the HTML you provided
            # For now, use default cards
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
        
    async def connect_to_game(self, game_id: int, websocket: WebSocket, user_id: int):
        """Connect user to game"""
        await websocket.accept()
        
        if game_id not in self.game_connections:
            self.game_connections[game_id] = []
            # Start number calling for this game (every 2 seconds)
            self.number_call_tasks[game_id] = asyncio.create_task(
                self.call_numbers_periodically(game_id)
            )
            logger.info(f"Started number calling for game {game_id} (2-second interval)")
        
        self.game_connections[game_id].append(websocket)
        
        # Initialize game if not exists
        if game_id not in self.active_games:
            self.active_games[game_id] = {
                'called_numbers': [],
                'players': {},
                'winner': None,
                'prize_pool': 0
            }
        
        # Add player (card will be added when they select)
        user = db.get_user(user_id)
        player_name = user['first_name'] if user and 'first_name' in user.keys() else f"Player{user_id}"
        self.active_games[game_id]['players'][user_id] = {
            'name': player_name,
            'card': None,
            'card_id': None,
            'marked_numbers': [],
            'is_winner': False
        }
        
        # Update prize pool (20 ETB per player)
        player_count = len(self.active_games[game_id]['players'])
        self.active_games[game_id]['prize_pool'] = player_count * 2000  # 20 ETB in cents
        
        # Notify all players
        await self.broadcast_to_game(game_id, {
            'type': 'player_joined',
            'players': self.get_players_list(game_id),
            'player_id': user_id,
            'prize_pool': self.active_games[game_id]['prize_pool'] / 100
        })
        
        logger.info(f"User {user_id} joined game {game_id}. Total players: {player_count}")
    
    def disconnect_from_game(self, game_id: int, websocket: WebSocket, user_id: int):
        """Disconnect user from game"""
        if game_id in self.game_connections:
            if websocket in self.game_connections[game_id]:
                self.game_connections[game_id].remove(websocket)
            
            # Remove player from active game
            if game_id in self.active_games:
                if user_id in self.active_games[game_id]['players']:
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
    
    async def broadcast_to_game(self, game_id: int, message: Dict[str, Any]):
        """Broadcast message to all players in game"""
        if game_id in self.game_connections:
            for connection in self.game_connections[game_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass
    
    async def call_numbers_periodically(self, game_id: int):
        """Call numbers every 2 seconds"""
        try:
            while True:
                await asyncio.sleep(2)  # Call number every 2 seconds
                
                if game_id in self.active_games:
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
            logger.error(f"Error in number calling: {str(e)}")
    
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
                'is_winner': data.get('is_winner', False)
            })
        return players
    
    def select_card(self, game_id: int, user_id: int, card_id: int):
        """Select a card for player"""
        if game_id in self.active_games:
            if user_id in self.active_games[game_id]['players']:
                # Find the card
                card_data = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
                if card_data:
                    self.active_games[game_id]['players'][user_id]['card'] = card_data['card']
                    self.active_games[game_id]['players'][user_id]['card_id'] = card_id
                    return True
        return False
    
    def mark_number(self, game_id: int, user_id: int, number: int):
        """Mark number for player"""
        if game_id in self.active_games:
            if user_id in self.active_games[game_id]['players']:
                player = self.active_games[game_id]['players'][user_id]
                if player['card'] and number not in player['marked_numbers']:
                    player['marked_numbers'].append(number)
                    return True
        return False
    
    def check_bingo(self, game_id: int, user_id: int, marked: List[int]) -> bool:
        """Check if player has bingo"""
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
    
    def declare_winner(self, game_id: int, user_id: int):
        """Declare winner and distribute prize"""
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
async def get_cards(page: int = 1, limit: int = 20):
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

@app.get("/api/cards/preview")
async def get_card_preview(card_id: int):
    """Get card preview (first few numbers)"""
    card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
    if card:
        # Return a preview (first row and column)
        preview = {
            "id": card["id"],
            "first_row": [card["card"][col][0] for col in range(5)],
            "first_col": [card["card"][0][row] for row in range(5)],
            "has_free": card["card"][2][2] == "FREE"
        }
        return preview
    return JSONResponse({"error": "Card not found"}, status_code=404)

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = None):
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
                "total_cards": len(BINGO_CARDS)
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
            
            if data['type'] == 'select_card':
                # Select card
                success = game_manager.select_card(
                    game_id, user_id, data['card_id']
                )
                await websocket.send_json({
                    'type': 'card_selected',
                    'success': success,
                    'card_id': data['card_id'] if success else None
                })
                
                if success:
                    # Get the card and send it to client
                    card_data = next((c for c in BINGO_CARDS if c['id'] == data['card_id']), None)
                    if card_data:
                        await websocket.send_json({
                            'type': 'your_card',
                            'card': card_data['card'],
                            'card_id': data['card_id']
                        })
            
            elif data['type'] == 'mark_number':
                # Mark number on card
                success = game_manager.mark_number(
                    game_id, user_id, data['number']
                )
                if success:
                    await websocket.send_json({
                        'type': 'number_marked',
                        'number': data['number']
                    })
            
            elif data['type'] == 'check_bingo':
                # Check if player has bingo
                valid = game_manager.check_bingo(
                    game_id, user_id, data['marked']
                )
                await websocket.send_json({
                    'type': 'bingo_result',
                    'valid': valid
                })
                
    except WebSocketDisconnect:
        game_manager.disconnect_from_game(game_id, websocket, user_id)
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        game_manager.disconnect_from_game(game_id, websocket, user_id)

@app.post("/api/join-game")
async def join_game(request: Request):
    """API endpoint to join game"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        game_id = data.get('game_id')
        
        # Deduct game fee (20 ETB = 2000 cents)
        result = db.update_balance(
            user_id=user_id,
            amount=-2000,
            transaction_type='game_fee',
            description=f'Joined game #{game_id}'
        )
        
        if result:
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
                'prize_pool': game['prize_pool'] / 100
            })
        
        return JSONResponse({
            'called_numbers': [],
            'players': [],
            'winner': None,
            'prize_pool': 0
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