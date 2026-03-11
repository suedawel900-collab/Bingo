# web_app.py
"""
MK BINGO Web Application - Complete Version
Serves the bingo.html interface and handles all API requests
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import json
import os
import random
from datetime import datetime
from typing import Optional, List
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
        },
        "settings": {
            "card_price": CARD_PRICE,
            "welcome_bonus": WELCOME_BONUS
        }
    }

def save_db(data):
    """Save database to file"""
    try:
        # Create backup before saving
        if os.path.exists(DB_FILE):
            backup_name = f"{DB_FILE}.backup"
            if not os.path.exists(backup_name):
                import shutil
                shutil.copy2(DB_FILE, backup_name)
        
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
    
    # Column ranges: B:1-15, I:16-30, N:31-45, G:46-60, O:61-75
    col_ranges = [(1, 15), (16, 30), (31, 45), (46, 60), (61, 75)]
    
    for col in range(5):
        numbers = random.sample(range(col_ranges[col][0], col_ranges[col][1] + 1), 5)
        card.append(numbers)
    
    # Free space in center
    card[2][2] = "FREE"
    
    return card

def generate_multiple_cards(count: int) -> list:
    """Generate multiple bingo cards"""
    return [generate_bingo_card() for _ in range(count)]

# ==================== API ENDPOINTS ====================

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "database": os.path.exists(DB_FILE)
    }

@app.get("/")
async def root():
    """Root endpoint - redirect to bingo page"""
    return {"message": "MK BINGO API", "docs": "/docs", "bingo": "/bingo"}

@app.get("/bingo", response_class=HTMLResponse)
async def serve_bingo_page():
    """Serve the bingo.html interface"""
    try:
        # Try multiple possible locations for the HTML file
        possible_paths = [
            Path("templates/bingo.html"),
            Path("bingo.html"),
            Path("static/bingo.html"),
            Path("../templates/bingo.html")
        ]
        
        for html_path in possible_paths:
            if html_path.exists():
                print(f"✅ Found bingo.html at: {html_path}")
                return HTMLResponse(content=html_path.read_text(encoding='utf-8'))
        
        # If file not found, return embedded HTML
        print("❌ bingo.html not found, using embedded HTML")
        return HTMLResponse(content=EMBEDDED_BINGO_HTML)
        
    except Exception as e:
        print(f"Error serving bingo page: {e}")
        return HTMLResponse(content=f"<h1>Error loading page</h1><p>{str(e)}</p>")

@app.get("/api/user/{user_id}")
async def get_user(user_id: str):
    """Get user data including balance and cards"""
    try:
        db = load_db()
        users = db.get("users", {})
        
        if user_id not in users:
            return JSONResponse({
                "balance": 0,
                "cards": [],
                "username": None,
                "first_name": None
            })
        
        user = users[user_id]
        
        # Format cards for display
        formatted_cards = []
        for card in user.get("cards", []):
            if isinstance(card, dict):
                formatted_cards.append(card)
            else:
                # Handle legacy card format
                formatted_cards.append({
                    "id": len(formatted_cards) + 1,
                    "numbers": card if isinstance(card, list) else generate_bingo_card(),
                    "purchased_at": datetime.now().isoformat(),
                    "marked": []
                })
        
        return JSONResponse({
            "balance": user.get("balance", 0),
            "cards": formatted_cards,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "total_spent": user.get("total_spent", 0),
            "games_played": user.get("games_played", 0)
        })
        
    except Exception as e:
        print(f"Error getting user: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/buy-card")
async def buy_single_card(request: Request):
    """Buy a single bingo card (legacy endpoint)"""
    data = await request.json()
    return await buy_cards_logic(
        user_id=data.get("user_id"),
        count=1
    )

@app.post("/api/buy-cards")
async def buy_multiple_cards(request: Request):
    """Buy multiple bingo cards"""
    data = await request.json()
    user_id = data.get("user_id")
    count = int(data.get("count", 1))
    
    return await buy_cards_logic(user_id, count)

async def buy_cards_logic(user_id: str, count: int):
    """Shared logic for buying cards"""
    try:
        if not user_id:
            return JSONResponse({
                "success": False,
                "message": "User ID required"
            })
        
        if count < 1 or count > 10:
            return JSONResponse({
                "success": False,
                "message": "Count must be between 1 and 10"
            })
        
        db = load_db()
        users = db.setdefault("users", {})
        
        # Initialize user if not exists
        if user_id not in users:
            users[user_id] = {
                "balance": WELCOME_BONUS,
                "cards": [],
                "username": None,
                "first_name": None,
                "joined_at": datetime.now().isoformat(),
                "total_spent": 0
            }
            db["statistics"]["total_users"] = db["statistics"].get("total_users", 0) + 1
        
        user = users[user_id]
        total_cost = count * CARD_PRICE
        
        # Check balance
        if user["balance"] < total_cost:
            return JSONResponse({
                "success": False,
                "message": f"Insufficient balance. Need {total_cost} ETB, you have {user['balance']} ETB"
            })
        
        # Generate multiple cards
        new_cards = []
        current_cards = user.get("cards", [])
        
        for i in range(count):
            card_numbers = generate_bingo_card()
            card_id = len(current_cards) + i + 1
            
            new_card = {
                "id": card_id,
                "numbers": card_numbers,
                "purchased_at": datetime.now().isoformat(),
                "marked": []
            }
            new_cards.append(new_card)
            current_cards.append(new_card)
        
        # Update user
        user["cards"] = current_cards
        user["balance"] -= total_cost
        user["total_spent"] = user.get("total_spent", 0) + total_cost
        user["last_active"] = datetime.now().isoformat()
        
        # Update statistics
        db["statistics"]["total_cards_sold"] = db["statistics"].get("total_cards_sold", 0) + count
        db["statistics"]["total_revenue"] = db["statistics"].get("total_revenue", 0) + total_cost
        
        # Save to database
        save_success = save_db(db)
        
        return JSONResponse({
            "success": True,
            "message": f"Successfully purchased {count} card(s)",
            "cards": new_cards,
            "new_balance": user["balance"],
            "total_cards": len(current_cards),
            "saved": save_success
        })
        
    except Exception as e:
        print(f"Error buying cards: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        })

@app.get("/api/card/{user_id}/{card_id}")
async def get_card(user_id: str, card_id: int):
    """Get specific card details"""
    try:
        db = load_db()
        users = db.get("users", {})
        
        if user_id not in users:
            raise HTTPException(status_code=404, detail="User not found")
        
        cards = users[user_id].get("cards", [])
        for card in cards:
            if card.get("id") == card_id:
                return JSONResponse(card)
        
        raise HTTPException(status_code=404, detail="Card not found")
        
    except Exception as e:
        print(f"Error getting card: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/mark-number")
async def mark_number(request: Request):
    """Mark a number on a card (for gameplay)"""
    try:
        data = await request.json()
        user_id = str(data.get("user_id"))
        card_id = data.get("card_id")
        number = data.get("number")
        
        db = load_db()
        users = db.get("users", {})
        
        if user_id not in users:
            raise HTTPException(status_code=404, detail="User not found")
        
        cards = users[user_id].get("cards", [])
        for card in cards:
            if card.get("id") == card_id:
                if "marked" not in card:
                    card["marked"] = []
                if number not in card["marked"]:
                    card["marked"].append(number)
                save_db(db)
                return JSONResponse({
                    "success": True,
                    "marked": card["marked"]
                })
        
        raise HTTPException(status_code=404, detail="Card not found")
        
    except Exception as e:
        print(f"Error marking number: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/statistics")
async def get_statistics():
    """Get global statistics"""
    try:
        db = load_db()
        stats = db.get("statistics", {})
        users = db.get("users", {})
        
        # Calculate additional stats
        active_today = sum(
            1 for u in users.values() 
            if u.get("last_active", "").startswith(datetime.now().date().isoformat())
        )
        
        total_balance = sum(u.get("balance", 0) for u in users.values())
        total_cards = sum(len(u.get("cards", [])) for u in users.values())
        
        return JSONResponse({
            "total_users": stats.get("total_users", 0),
            "active_today": active_today,
            "total_cards_sold": stats.get("total_cards_sold", 0),
            "total_cards_owned": total_cards,
            "total_revenue": stats.get("total_revenue", 0),
            "total_balance": total_balance,
            "total_withdrawals": stats.get("total_withdrawals", 0)
        })
        
    except Exception as e:
        print(f"Error getting statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== EMBEDDED HTML (FALLBACK) ====================
EMBEDDED_BINGO_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>MK BINGO - Buy Cards</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            font-family: Arial, sans-serif;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        h1 { color: #667eea; text-align: center; }
        .error { color: red; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎯 MK BINGO</h1>
        <div class="error">
            <p>❌ bingo.html file not found</p>
            <p>Please ensure the templates folder exists with bingo.html inside</p>
            <p>Current path: templates/bingo.html</p>
        </div>
    </div>
</body>
</html>
"""

# ==================== MAIN ====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting MK BINGO Web App on port {port}")
    print(f"📁 Database file: {DB_FILE}")
    print(f"💰 Card price: {CARD_PRICE} ETB")
    print(f"🎁 Welcome bonus: {WELCOME_BONUS} ETB")
    
    # Create templates directory if it doesn't exist
    Path("templates").mkdir(exist_ok=True)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )