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

# Load pre-generated cards from JSON file
CARDS_FILE = "static/bingo_cards.json"

# Generate default cards if file doesn't exist
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

# Game manager
class GameManager:
    def __init__(self):
        self.active_games = {}
        self.game_connections = {}
        self.taken_cards = {}
        self.game_started = {}
        
    async def connect(self, game_id: int, websocket: WebSocket, user_id: int):
        await websocket.accept()
        logger.info(f"User {user_id} connected to game {game_id}")
        
        if game_id not in self.game_connections:
            self.game_connections[game_id] = []
            self.taken_cards[game_id] = set()
            self.game_started[game_id] = False
            self.active_games[game_id] = {
                'called_numbers': [],
                'players': {},
                'winner': None,
                'prize_pool': 0
            }
        
        self.game_connections[game_id].append(websocket)
        
        # Add player
        user = db.get_user(user_id)
        player_name = user['first_name'] if user else f"Player{user_id}"
        self.active_games[game_id]['players'][user_id] = {
            'name': player_name,
            'card': None,
            'card_id': None,
            'marked': [],
            'ready': False,
            'winner': False
        }
        
        # Update prize pool
        player_count = len(self.active_games[game_id]['players'])
        self.active_games[game_id]['prize_pool'] = player_count * 2000
        
        # Send current state
        await websocket.send_json({
            'type': 'connected',
            'taken_cards': list(self.taken_cards[game_id]),
            'players': self.get_players(game_id)
        })
        
        # Notify others
        await self.broadcast(game_id, {
            'type': 'player_joined',
            'players': self.get_players(game_id),
            'taken_cards': list(self.taken_cards[game_id])
        })
    
    def disconnect(self, game_id: int, websocket: WebSocket, user_id: int):
        if game_id in self.game_connections:
            if websocket in self.game_connections[game_id]:
                self.game_connections[game_id].remove(websocket)
            
            if user_id in self.active_games[game_id]['players']:
                player = self.active_games[game_id]['players'][user_id]
                if player['card_id']:
                    self.taken_cards[game_id].discard(player['card_id'])
                del self.active_games[game_id]['players'][user_id]
    
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
                'card_id': data['card_id'],
                'ready': data['ready']
            })
        return players
    
    async def select_card(self, game_id: int, user_id: int, card_id: int):
        if game_id not in self.active_games:
            return False, "Game not found"
        
        if self.game_started[game_id]:
            return False, "Game already started"
        
        if card_id in self.taken_cards[game_id]:
            return False, f"Card {card_id} already taken"
        
        if user_id not in self.active_games[game_id]['players']:
            return False, "Player not found"
        
        card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
        if not card:
            return False, "Card not found"
        
        self.taken_cards[game_id].add(card_id)
        player = self.active_games[game_id]['players'][user_id]
        player['card'] = card['card']
        player['card_id'] = card_id
        player['ready'] = True
        
        await self.broadcast(game_id, {
            'type': 'card_taken',
            'taken_cards': list(self.taken_cards[game_id])
        })
        
        return True, card['card']
    
    async def start_game(self, game_id: int, user_id: int):
        if str(user_id) != ADMIN_USER_ID:
            return False, "Not authorized"
        
        if game_id not in self.active_games:
            return False, "Game not found"
        
        if self.game_started[game_id]:
            return False, "Game already started"
        
        ready_count = sum(1 for p in self.active_games[game_id]['players'].values() if p['ready'])
        if ready_count < 1:
            return False, "No players ready"
        
        self.game_started[game_id] = True
        
        # Start number generation task
        asyncio.create_task(self.generate_numbers(game_id))
        
        return True, "Game started"
    
    async def generate_numbers(self, game_id: int):
        while game_id in self.active_games and self.game_started[game_id]:
            await asyncio.sleep(2)
            
            available = [n for n in range(1, 76) 
                        if n not in self.active_games[game_id]['called_numbers']]
            
            if available:
                number = random.choice(available)
                self.active_games[game_id]['called_numbers'].append(number)
                
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
                break

game_manager = GameManager()

@app.get("/")
async def root():
    return {
        "status": "online",
        "cards": len(BINGO_CARDS),
        "endpoints": ["/game", "/health", "/api/cards"]
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/cards")
async def get_cards():
    return {"total": len(BINGO_CARDS), "cards": [{"id": c["id"]} for c in BINGO_CARDS]}

@app.get("/api/card/{card_id}")
async def get_card(card_id: int):
    card = next((c for c in BINGO_CARDS if c['id'] == card_id), None)
    if card:
        return card
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    return templates.TemplateResponse("bingo.html", {
        "request": request,
        "user_id": user_id,
        "game_id": game_id,
        "total_cards": len(BINGO_CARDS),
        "admin_id": ADMIN_USER_ID
    })

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    await game_manager.connect(game_id, websocket, user_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            logger.info(f"Received: {data}")
            
            if data['type'] == 'select_card':
                success, result = await game_manager.select_card(
                    game_id, user_id, data['card_id']
                )
                if success:
                    await websocket.send_json({
                        'type': 'card_selected',
                        'success': True,
                        'card_id': data['card_id']
                    })
                    await websocket.send_json({
                        'type': 'your_card',
                        'card': result,
                        'card_id': data['card_id']
                    })
                else:
                    await websocket.send_json({
                        'type': 'error',
                        'message': result
                    })
            
            elif data['type'] == 'start_game':
                success, message = await game_manager.start_game(game_id, user_id)
                if success:
                    await websocket.send_json({'type': 'game_started'})
                    await game_manager.broadcast(game_id, {'type': 'game_started'})
                else:
                    await websocket.send_json({'type': 'error', 'message': message})
            
            elif data['type'] == 'ping':
                await websocket.send_json({'type': 'pong'})
                
    except WebSocketDisconnect:
        game_manager.disconnect(game_id, websocket, user_id)
        logger.info(f"User {user_id} disconnected")

@app.post("/api/join-game")
async def join_game(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    game_id = data.get('game_id')
    
    result = db.update_balance(
        user_id=user_id,
        amount=-2000,
        transaction_type='game_fee',
        description=f'Joined game #{game_id}'
    )
    
    if result:
        return JSONResponse({'success': True, 'balance': result['new_balance']})
    return JSONResponse({'success': False, 'error': 'Insufficient balance'}, status_code=400)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)