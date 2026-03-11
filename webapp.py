# web_app.py
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os
import json
from datetime import datetime

app = FastAPI()

# Store active connections
active_connections = {}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: str = None, game_id: str = "1"):
    """Serve the bingo game HTML"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bingo Game</title>
        <style>
            /* Add your CSS here */
            body { font-family: Arial; background: linear-gradient(135deg, #667eea, #764ba2); }
            .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎯 MK BINGO</h1>
            <p>User ID: """ + user_id + """</p>
            <p>Game ID: """ + game_id + """</p>
            <div id="game-area">
                <!-- Your bingo game HTML here -->
            </div>
        </div>
        <script>
            // Your JavaScript here
            console.log("Game loaded for user:", """ + user_id + """);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    """Get user data"""
    return {
        "id": user_id,
        "balance": 1000,
        "active_games": 0,
        "total_stake": 0
    }

@app.post("/api/payment/telebirr/initiate")
async def initiate_payment(request: Request):
    """Initiate TeleBirr payment"""
    data = await request.json()
    return {
        "success": True,
        "paymentUrl": f"https://pay.telebirr.et/pay/{data['userId']}",
        "transactionRef": "TB" + str(data['userId']) + str(datetime.now().timestamp())
    }

@app.get("/api/payment/status/{ref}")
async def payment_status(ref: str):
    """Check payment status"""
    return {
        "status": "approved",
        "ref": ref,
        "newBalance": 1000
    }

@app.post("/api/withdrawal/request")
async def withdrawal_request(request: Request):
    """Request withdrawal"""
    data = await request.json()
    return {
        "success": True,
        "withdrawalId": 123
    }

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    """WebSocket connection for real-time game"""
    await websocket.accept()
    active_connections[user_id] = websocket
    
    try:
        # Send initial connection data
        await websocket.send_json({
            "type": "connected",
            "taken_cards": [],
            "players": [],
            "game_started": False,
            "called_numbers": [],
            "round": 1,
            "auto_start_active": False
        })
        
        # Keep connection alive
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Handle different message types
            if message["type"] == "select_cards":
                await websocket.send_json({
                    "type": "cards_selected",
                    "success": True,
                    "message": "Cards selected successfully",
                    "card_ids": message["card_ids"],
                    "new_balance": 99000  # in cents
                })
            elif message["type"] == "claim_bingo":
                await websocket.send_json({
                    "type": "game_won",
                    "winner": {
                        "id": user_id,
                        "name": f"User_{user_id}"
                    },
                    "prize": 500
                })
                
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if user_id in active_connections:
            del active_connections[user_id]