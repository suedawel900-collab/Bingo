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
    # Try to read the HTML file from templates folder
    html_path = Path("templates/bingo.html")
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding='utf-8'))
    
    # Try current directory
    html_path = Path("bingo.html")
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding='utf-8'))
    
    # Fallback HTML if file not found
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MK BINGO</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                background: linear-gradient(135deg, #667eea, #764ba2);
                font-family: Arial, sans-serif;
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            .container {
                background: white;
                border-radius: 20px;
                padding: 30px;
                max-width: 500px;
                width: 100%;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            }
            h1 { color: #667eea; text-align: center; margin-bottom: 20px; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; color: #333; }
            input, button {
                width: 100%;
                padding: 12px;
                border: 2px solid #ddd;
                border-radius: 10px;
                font-size: 16px;
            }
            button {
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white;
                border: none;
                cursor: pointer;
                font-weight: bold;
            }
            button:hover { opacity: 0.9; }
            .balance {
                background: #f0f0f0;
                padding: 15px;
                border-radius: 10px;
                margin: 20px 0;
                text-align: center;
                font-size: 20px;
            }
            .cards { margin-top: 20px; }
            .card {
                background: #f9f9f9;
                border: 2px solid #667eea;
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 10px;
            }
            .hidden { display: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎯 MK BINGO</h1>
            
            <div id="loginForm">
                <div class="form-group">
                    <label>Enter your Telegram User ID:</label>
                    <input type="text" id="userId" placeholder="e.g., 6975815871">
                </div>
                <button onclick="login()">Login</button>
            </div>
            
            <div id="gameArea" class="hidden">
                <div class="balance" id="balanceDisplay">Balance: 0 ETB</div>
                
                <button onclick="buyCard()" id="buyBtn">🎮 Buy Card (10 ETB)</button>
                
                <div id="message" style="margin-top: 10px; padding: 10px; border-radius: 5px; display: none;"></div>
                
                <div class="cards" id="cardsContainer">
                    <h3>Your Cards</h3>
                    <div id="cardsList"></div>
                </div>
                
                <button onclick="logout()" style="margin-top: 20px; background: #dc3545;">Logout</button>
            </div>
        </div>
        
        <script>
            let currentUserId = null;
            
            function showMessage(text, type) {
                const msg = document.getElementById('message');
                msg.textContent = text;
                msg.style.display = 'block';
                msg.style.background = type === 'success' ? '#d4edda' : '#f8d7da';
                msg.style.color = type === 'success' ? '#155724' : '#721c24';
                setTimeout(() => msg.style.display = 'none', 3000);
            }
            
            async function login() {
                const userId = document.getElementById('userId').value.trim();
                if (!userId) {
                    showMessage('Please enter User ID', 'error');
                    return;
                }
                
                currentUserId = userId;
                document.getElementById('loginForm').classList.add('hidden');
                document.getElementById('gameArea').classList.remove('hidden');
                
                await loadUserData();
            }
            
            function logout() {
                currentUserId = null;
                document.getElementById('loginForm').classList.remove('hidden');
                document.getElementById('gameArea').classList.add('hidden');
                document.getElementById('userId').value = '';
            }
            
            async function loadUserData() {
                try {
                    const response = await fetch(`/api/user/${currentUserId}`);
                    const data = await response.json();
                    
                    document.getElementById('balanceDisplay').textContent = `Balance: ${data.balance} ETB`;
                    
                    const cardsList = document.getElementById('cardsList');
                    if (data.cards && data.cards.length > 0) {
                        cardsList.innerHTML = data.cards.map(card => `
                            <div class="card">
                                Card #${card.id || 'New'} - Purchased: ${card.purchased_at ? new Date(card.purchased_at).toLocaleDateString() : 'Just now'}
                            </div>
                        `).join('');
                    } else {
                        cardsList.innerHTML = '<p>No cards yet. Buy your first card!</p>';
                    }
                } catch (error) {
                    showMessage('Error loading data', 'error');
                }
            }
            
            async function buyCard() {
                document.getElementById('buyBtn').disabled = true;
                
                try {
                    const response = await fetch('/api/buy-card', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({user_id: currentUserId})
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        showMessage('✅ Card purchased successfully!', 'success');
                        await loadUserData();
                    } else {
                        showMessage('❌ ' + data.message, 'error');
                    }
                } catch (error) {
                    showMessage('Error purchasing card', 'error');
                }
                
                document.getElementById('buyBtn').disabled = false;
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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)