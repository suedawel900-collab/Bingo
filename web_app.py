# web_app.py
"""
MK BINGO Web Application - Complete Version
Serves the bingo.html interface and handles all API requests
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import json
import os
import random
from datetime import datetime
from typing import Optional
import uvicorn
from pathlib import Path

# ==================== CONFIGURATION ====================
CARD_PRICE = 10  # ETB per card
WELCOME_BONUS = 100  # ETB
DB_FILE = "bot_database.json"

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="MK BINGO Web App",
    description="Buy and view your bingo cards",
    version="1.0.0"
)

# ==================== DATABASE FUNCTIONS ====================
def load_db():
    """Load database from file"""
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print(f"✅ Database loaded: {len(data.get('users', {}))} users")
                return data
    except Exception as e:
        print(f"Error loading database: {e}")
    
    # Default structure
    return {
        "users": {},
        "games": [],
        "withdrawals": [],
        "transactions": [],
        "statistics": {
            "total_users": 0,
            "total_cards_sold": 0,
            "total_revenue": 0,
            "total_withdrawals": 0,
            "created_at": datetime.now().isoformat()
        }
    }

def save_db(data):
    """Save database to file"""
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("💾 Database saved")
        return True
    except Exception as e:
        print(f"Error saving database: {e}")
        return False

# ==================== BINGO CARD GENERATOR ====================
def generate_bingo_card() -> list:
    """Generate a 5x5 bingo card with numbers 1-75"""
    card = []
    col_ranges = [(1, 15), (16, 30), (31, 45), (46, 60), (61, 75)]
    
    for col in range(5):
        numbers = random.sample(range(col_ranges[col][0], col_ranges[col][1] + 1), 5)
        card.append(numbers)
    
    # Free space in center
    card[2][2] = "FREE"
    
    return card

# ==================== API ENDPOINTS ====================

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/bingo", response_class=HTMLResponse)
async def serve_bingo_page():
    """Serve the bingo.html interface"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MK BINGO - Buy Cards</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', sans-serif; }
            body {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            .header {
                background: white;
                border-radius: 20px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            }
            .header h1 { color: #667eea; margin-bottom: 10px; }
            .balance-box {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 15px 25px;
                border-radius: 15px;
                font-size: 1.2em;
                display: inline-block;
            }
            .balance-box .amount { font-size: 2em; font-weight: bold; }
            .buy-section {
                background: white;
                border-radius: 20px;
                padding: 30px;
                margin-bottom: 30px;
                text-align: center;
            }
            .price-info { font-size: 1.3em; margin-bottom: 20px; }
            .price-info span { color: #667eea; font-weight: bold; font-size: 2em; }
            .buy-btn {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 15px 40px;
                font-size: 1.2em;
                border-radius: 50px;
                cursor: pointer;
                margin: 10px;
            }
            .buy-btn:hover { transform: scale(1.05); }
            .cards-section {
                background: white;
                border-radius: 20px;
                padding: 20px;
            }
            .card-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                gap: 20px;
                margin-top: 20px;
            }
            .bingo-card {
                background: #f8f9fa;
                border-radius: 15px;
                padding: 15px;
                border: 2px solid #667eea;
            }
            .bingo-table {
                width: 100%;
                border-collapse: collapse;
            }
            .bingo-table th { color: #667eea; padding: 5px; }
            .bingo-table td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: center;
                font-weight: bold;
            }
            .free-space { background: #ffd700; }
            .login-section {
                background: white;
                border-radius: 20px;
                padding: 40px;
                text-align: center;
                max-width: 400px;
                margin: 50px auto;
            }
            .user-id-input {
                padding: 10px;
                font-size: 1em;
                border: 2px solid #667eea;
                border-radius: 10px;
                width: 100%;
                margin: 10px 0;
            }
            .login-btn {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 10px 30px;
                border-radius: 10px;
                cursor: pointer;
            }
            .message {
                padding: 10px;
                border-radius: 5px;
                margin: 10px 0;
                display: none;
            }
            .message.success { background: #d4edda; color: #155724; display: block; }
            .message.error { background: #f8d7da; color: #721c24; display: block; }
        </style>
    </head>
    <body>
        <div class="container">
            <div id="login-section" class="login-section">
                <h1>🎯 MK BINGO</h1>
                <p>Enter your Telegram User ID</p>
                <input type="text" id="user-id" class="user-id-input" placeholder="e.g., 6975815871">
                <button onclick="login()" class="login-btn">Login</button>
            </div>
            
            <div id="main-content" style="display: none;">
                <div class="header">
                    <h1>🎯 MK BINGO</h1>
                    <div class="balance-box">
                        <div>Your Balance</div>
                        <div class="amount" id="balance">0 ETB</div>
                    </div>
                    <p>User ID: <span id="display-user-id"></span></p>
                </div>
                
                <div class="buy-section">
                    <h2>🎮 Buy Bingo Cards</h2>
                    <div class="price-info">
                        Price: <span id="card-price">10 ETB</span> per card
                    </div>
                    <button class="buy-btn" onclick="buyCard()" id="buy-btn">Buy 1 Card</button>
                    <div id="message" class="message"></div>
                </div>
                
                <div class="cards-section">
                    <h2>📊 My Cards</h2>
                    <div id="cards-grid" class="card-grid"></div>
                </div>
            </div>
        </div>
        
        <script>
            let currentUserId = null;
            
            function login() {
                const userId = document.getElementById('user-id').value.trim();
                if (!userId) return alert('Enter User ID');
                currentUserId = userId;
                document.getElementById('display-user-id').textContent = userId;
                document.getElementById('login-section').style.display = 'none';
                document.getElementById('main-content').style.display = 'block';
                loadUserData();
            }
            
            async function loadUserData() {
                const response = await fetch(`/api/user/${currentUserId}`);
                const data = await response.json();
                document.getElementById('balance').textContent = data.balance + ' ETB';
                
                const grid = document.getElementById('cards-grid');
                if (data.cards && data.cards.length > 0) {
                    grid.innerHTML = data.cards.map(card => renderCard(card)).join('');
                } else {
                    grid.innerHTML = '<p>No cards yet. Buy one above!</p>';
                }
            }
            
            function renderCard(card) {
                let numbers = card.numbers || [[1,2,3,4,5],[16,17,18,19,20],[31,32,'FREE',34,35],[46,47,48,49,50],[61,62,63,64,65]];
                let html = '<div class="bingo-card"><table class="bingo-table"><tr><th>B</th><th>I</th><th>N</th><th>G</th><th>O</th></tr>';
                for (let row = 0; row < 5; row++) {
                    html += '<tr>';
                    for (let col = 0; col < 5; col++) {
                        let val = numbers[col][row];
                        let cls = (val === 'FREE') ? 'free-space' : '';
                        html += `<td class="${cls}">${val}</td>`;
                    }
                    html += '</tr>';
                }
                html += '</table></div>';
                return html;
            }
            
            async function buyCard() {
                document.getElementById('buy-btn').disabled = true;
                const response = await fetch('/api/buy-card', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({user_id: currentUserId})
                });
                const data = await response.json();
                
                const msg = document.getElementById('message');
                if (data.success) {
                    msg.className = 'message success';
                    msg.textContent = '✅ Card purchased!';
                    loadUserData();
                } else {
                    msg.className = 'message error';
                    msg.textContent = '❌ ' + data.message;
                }
                document.getElementById('buy-btn').disabled = false;
                setTimeout(() => msg.style.display = 'none', 3000);
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/user/{user_id}")
async def get_user(user_id: str):
    """Get user data"""
    db = load_db()
    users = db.get("users", {})
    
    if user_id not in users:
        return {"balance": WELCOME_BONUS, "cards": []}
    
    user = users[user_id]
    return {
        "balance": user.get("balance", WELCOME_BONUS),
        "cards": user.get("cards", [])
    }

@app.post("/api/buy-card")
async def buy_card(request: Request):
    """Buy a single bingo card"""
    data = await request.json()
    user_id = str(data.get("user_id"))
    
    if not user_id:
        return JSONResponse({"success": False, "message": "User ID required"})
    
    db = load_db()
    users = db.setdefault("users", {})
    
    if user_id not in users:
        users[user_id] = {"balance": WELCOME_BONUS, "cards": []}
    
    user = users[user_id]
    
    if user["balance"] < CARD_PRICE:
        return JSONResponse({
            "success": False,
            "message": f"Insufficient balance. Need {CARD_PRICE} ETB"
        })
    
    # Generate new card
    card_numbers = generate_bingo_card()
    card_id = len(user["cards"]) + 1
    
    new_card = {
        "id": card_id,
        "numbers": card_numbers,
        "purchased_at": datetime.now().isoformat(),
        "marked": []
    }
    
    user["cards"].append(new_card)
    user["balance"] -= CARD_PRICE
    
    # Update statistics
    db["statistics"]["total_cards_sold"] = db["statistics"].get("total_cards_sold", 0) + 1
    db["statistics"]["total_revenue"] = db["statistics"].get("total_revenue", 0) + CARD_PRICE
    
    save_db(db)
    
    return JSONResponse({
        "success": True,
        "message": "Card purchased successfully",
        "new_balance": user["balance"]
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting MK BINGO Web App on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)