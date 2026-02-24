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
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize database
db = Database()

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
            # Start number calling for this game
            self.number_call_tasks[game_id] = asyncio.create_task(
                self.call_numbers_periodically(game_id)
            )
        
        self.game_connections[game_id].append(websocket)
        
        # Initialize game if not exists
        if game_id not in self.active_games:
            self.active_games[game_id] = {
                'called_numbers': [],
                'players': {},
                'winner': None,
                'prize_pool': 0
            }
        
        # Add player
        user = db.get_user(user_id)
        self.active_games[game_id]['players'][user_id] = {
            'name': user['first_name'] or f"Player{user_id}",
            'card': None,
            'marked_numbers': [],
            'is_winner': False
        }
        
        # Notify all players
        await self.broadcast_to_game(game_id, {
            'type': 'player_joined',
            'players': self.get_players_list(game_id),
            'player_id': user_id
        })
        
        logger.info(f"User {user_id} joined game {game_id}")
    
    def disconnect_from_game(self, game_id: int, websocket: WebSocket, user_id: int):
        """Disconnect user from game"""
        if game_id in self.game_connections:
            if websocket in self.game_connections[game_id]:
                self.game_connections[game_id].remove(websocket)
            
            # Remove player from active game
            if game_id in self.active_games:
                if user_id in self.active_games[game_id]['players']:
                    del self.active_games[game_id]['players'][user_id]
            
            # Stop number calling if no players left
            if len(self.game_connections[game_id]) == 0:
                if game_id in self.number_call_tasks:
                    self.number_call_tasks[game_id].cancel()
                    del self.number_call_tasks[game_id]
    
    async def broadcast_to_game(self, game_id: int, message: Dict[str, Any]):
        """Broadcast message to all players in game"""
        if game_id in self.game_connections:
            for connection in self.game_connections[game_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass
    
    async def call_numbers_periodically(self, game_id: int):
        """Call numbers every 10 seconds"""
        try:
            while True:
                await asyncio.sleep(10)  # Call number every 10 seconds
                
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
                            'called_numbers': game['called_numbers']
                        })
                        
                        logger.info(f"Game {game_id} called number: {number}")
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
                'is_winner': data.get('is_winner', False)
            })
        return players
    
    def mark_number(self, game_id: int, user_id: int, number: int):
        """Mark number for player"""
        if game_id in self.active_games:
            if user_id in self.active_games[game_id]['players']:
                player = self.active_games[game_id]['players'][user_id]
                if number not in player['marked_numbers']:
                    player['marked_numbers'].append(number)
                    return True
        return False
    
    def check_bingo(self, game_id: int, user_id: int, card: List[List], marked: List[int]) -> bool:
        """Check if player has bingo"""
        if game_id not in self.active_games:
            return False
        
        game = self.active_games[game_id]
        
        # Check if game already has winner
        if game['winner']:
            return False
        
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
            prize_pool = player_count * 200  # $2 per player in cents
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
                'prize': f"${winner_prize/100:.2f}"
            }))
            
            logger.info(f"Game {game_id} winner: User {user_id}, Prize: ${winner_prize/100:.2f}")

# Initialize game manager
game_manager = GameManager()

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
                cursor = db.get_connection().cursor()
                cursor.execute('''
                    INSERT INTO games (game_code) VALUES (?)
                ''', (game_code,))
                game_id = cursor.lastrowid
        
        return templates.TemplateResponse(
            "bingo.html",
            {
                "request": request,
                "user_id": user_id,
                "game_id": game_id
            }
        )
        
    except Exception as e:
        logger.error(f"Game page error: {str(e)}")
        return HTMLResponse(
            content=f"<h1>Error loading game</h1><p>{str(e)}</p>",
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
            
            if data['type'] == 'mark_number':
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
                    game_id, user_id, data['card'], data['marked']
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
    data = await request.json()
    user_id = data.get('user_id')
    game_id = data.get('game_id')
    card = data.get('card')
    
    try:
        # Deduct game fee
        result = db.update_balance(
            user_id=user_id,
            amount=-200,  # $2 game fee
            transaction_type='game_fee',
            description=f'Joined game #{game_id}'
        )
        
        if result:
            # Add player to game in database
            db.join_game(game_id, user_id, card)
            
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
    data = await request.json()
    game_id = data.get('game_id')
    user_id = data.get('user_id')
    number = data.get('number')
    
    success = game_manager.mark_number(game_id, user_id, number)
    
    return JSONResponse({
        'success': success
    })

@app.post("/api/check-bingo")
async def check_bingo(request: Request):
    """API endpoint to check bingo"""
    data = await request.json()
    game_id = data.get('game_id')
    user_id = data.get('user_id')
    card = data.get('card')
    marked = data.get('marked')
    
    valid = game_manager.check_bingo(game_id, user_id, card, marked)
    
    return JSONResponse({
        'valid': valid
    })

@app.get("/api/game-state/{game_id}")
async def get_game_state(game_id: int):
    """Get current game state"""
    if game_id in game_manager.active_games:
        game = game_manager.active_games[game_id]
        return JSONResponse({
            'called_numbers': game['called_numbers'],
            'players': game_manager.get_players_list(game_id),
            'winner': game['winner']
        })
    
    return JSONResponse({
        'called_numbers': [],
        'players': [],
        'winner': None
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)