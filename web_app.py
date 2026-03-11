# web_app.py
"""
MK BINGO Web Application - Serves bingo.html and handles API requests
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import json
import os
import random
from datetime import datetime
from pathlib import Path
import uvicorn

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
                return json.load(f)
    except Exception as e:
        print(f"Error loading database: {e}")
    
    # Default structure
    return {
        "users": {},
        "statistics": {
            "total_users": 0,
            "total_cards_sold": 0,
            "total_revenue": 0
        }
    }

def save_db(data):
    """Save database to file"""
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
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
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/bingo", response_class=HTMLResponse)
async def serve_bingo_page():
    """Serve the bingo.html interface"""
    # Try to read the HTML file
    html_path = Path("templates/bingo.html")
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding='utf-8'))
    
    # Fallback HTML if file not found
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MK BINGO</title>
        <style>
            body { font-family: Arial; background: linear-gradient(135deg, #667eea, #764ba2); padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 20px; }
            input, button { padding: 10px; margin: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎯 MK BINGO</h1>
            <p>Enter your User ID:</p>
            <input type="text" id="userId" placeholder="Telegram User ID">
            <button onclick="login()">Login</button>
            <div id="content" style="display:none">
                <h2>Your Cards</h2>
                <div id="cards"></div>
                <button onclick="buyCard()">Buy Card (10 ETB)</button>
                <p id="balance"></p>
            </div>
        </div>
        <script>
            let uid = null;
            async function login() {
                uid = document.getElementById('userId').value;
                document.getElementById('content').style.display = 'block';
                loadData();
            }
            async function loadData() {
                let res = await fetch('/api/user/' + uid);
                let data = await res.json();
                document.getElementById('balance').innerText = 'Balance: ' + data.balance + ' ETB';
            }
            async function buyCard() {
                let res = await fetch('/api/buy-card', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({user_id: uid})
                });
                let data = await res.json();
                if(data.success) loadData();
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
    
    return {
        "balance": users[user_id].get("balance", WELCOME_BONUS),
        "cards": users[user_id].get("cards", [])
    }

@app.post("/api/buy-card")
async def buy_card(request: Request):
    """Buy a single bingo card"""
    try:
        data = await request.json()
        user_id = str(data.get("user_id"))
        
        if not user_id:
            return JSONResponse({"success": False, "message": "User ID required"})
        
        db = load_db()
        users = db.setdefault("users", {})
        
        # Initialize user if not exists
        if user_id not in users:
            users[user_id] = {
                "balance": WELCOME_BONUS,
                "cards": []
            }
        
        user = users[user_id]
        
        # Check balance
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
        
        # Update user
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
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting web app on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)