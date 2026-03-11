# web_app.py
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import json
import os
from datetime import datetime
import random
from typing import Optional
import uvicorn

app = FastAPI(title="MK BINGO Web App")

# Database file
DB_FILE = "bot_database.json"

def load_db():
    """Load database"""
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {"users": {}, "statistics": {}}

def save_db(data):
    """Save database"""
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# ==================== BINGO CARD GENERATOR ====================
def generate_bingo_card() -> list:
    """Generate a 5x5 bingo card"""
    card = []
    
    # Column ranges: B:1-15, I:16-30, N:31-45, G:46-60, O:61-75
    col_ranges = [(1, 15), (16, 30), (31, 45), (46, 60), (61, 75)]
    
    for col in range(5):
        numbers = random.sample(range(col_ranges[col][0], col_ranges[col][1] + 1), 5)
        card.append(numbers)
    
    # Free space in center
    card[2][2] = "FREE"
    
    return card

# ==================== ROUTES ====================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MK BINGO - Buy Cards</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }
            
            body {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            
            /* Header */
            .header {
                background: rgba(255, 255, 255, 0.95);
                border-radius: 20px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            }
            
            .header h1 {
                color: #667eea;
                font-size: 2.5em;
                margin-bottom: 10px;
            }
            
            .user-info {
                display: flex;
                gap: 20px;
                flex-wrap: wrap;
                background: #f8f9fa;
                padding: 15px;
                border-radius: 15px;
            }
            
            .balance-box {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 15px 25px;
                border-radius: 15px;
                font-size: 1.2em;
            }
            
            .balance-box .amount {
                font-size: 2em;
                font-weight: bold;
            }
            
            /* Card Grid */
            .cards-section {
                background: rgba(255, 255, 255, 0.95);
                border-radius: 20px;
                padding: 20px;
                margin-bottom: 20px;
            }
            
            .section-title {
                color: #333;
                margin-bottom: 20px;
                font-size: 1.5em;
            }
            
            .card-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                gap: 20px;
            }
            
            .bingo-card {
                background: white;
                border-radius: 15px;
                padding: 15px;
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                border: 2px solid #667eea;
                transition: transform 0.3s;
            }
            
            .bingo-card:hover {
                transform: translateY(-5px);
            }
            
            .card-header {
                text-align: center;
                font-weight: bold;
                color: #667eea;
                margin-bottom: 10px;
                font-size: 1.1em;
            }
            
            .bingo-table {
                width: 100%;
                border-collapse: collapse;
            }
            
            .bingo-table th {
                color: #667eea;
                font-size: 1.2em;
                padding: 5px;
            }
            
            .bingo-table td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: center;
                font-weight: bold;
            }
            
            .free-space {
                background: #ffd700;
                color: #333;
            }
            
            .marked {
                background: #4CAF50;
                color: white;
            }
            
            /* Buy Cards Section */
            .buy-section {
                background: rgba(255, 255, 255, 0.95);
                border-radius: 20px;
                padding: 20px;
                margin-bottom: 20px;
                text-align: center;
            }
            
            .price-info {
                font-size: 1.2em;
                color: #333;
                margin-bottom: 20px;
            }
            
            .price-info span {
                color: #667eea;
                font-weight: bold;
                font-size: 1.5em;
            }
            
            .buy-btn {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 15px 40px;
                font-size: 1.2em;
                border-radius: 50px;
                cursor: pointer;
                transition: transform 0.3s;
                margin: 10px;
            }
            
            .buy-btn:hover:not(:disabled) {
                transform: scale(1.05);
            }
            
            .buy-btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            
            .message {
                padding: 15px;
                border-radius: 10px;
                margin: 10px 0;
                display: none;
            }
            
            .success {
                background: #d4edda;
                color: #155724;
                display: block;
            }
            
            .error {
                background: #f8d7da;
                color: #721c24;
                display: block;
            }
            
            /* Telegram Login */
            .login-section {
                text-align: center;
                padding: 40px;
            }
            
            .user-id-input {
                padding: 15px;
                font-size: 1.1em;
                border: 2px solid #667eea;
                border-radius: 10px;
                width: 300px;
                margin: 10px;
            }
            
            .login-btn {
                background: #667eea;
                color: white;
                border: none;
                padding: 15px 30px;
                border-radius: 10px;
                font-size: 1.1em;
                cursor: pointer;
            }
            
            /* Back to Bot */
            .back-to-bot {
                text-align: center;
                margin-top: 20px;
            }
            
            .back-btn {
                background: #28a745;
                color: white;
                text-decoration: none;
                padding: 10px 20px;
                border-radius: 50px;
                display: inline-block;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div id="login-section" class="login-section">
                <h1>🎯 MK BINGO</h1>
                <p>Enter your Telegram User ID to access your cards</p>
                <input type="text" id="user-id" class="user-id-input" placeholder="Telegram User ID">
                <button onclick="login()" class="login-btn">Login</button>
                <p style="margin-top: 20px; color: #666;">
                    Don't know your ID? Send /start to the bot and check the logs
                </p>
            </div>
            
            <div id="main-content" style="display: none;">
                <div class="header">
                    <h1>🎯 MK BINGO - My Cards</h1>
                    <div class="user-info">
                        <div class="balance-box">
                            <div>Your Balance</div>
                            <div class="amount" id="balance">0 ETB</div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <div>User ID: <span id="display-user-id"></span></div>
                            <button onclick="logout()" style="padding: 5px 10px;">Logout</button>
                        </div>
                    </div>
                </div>
                
                <div class="buy-section">
                    <h2>🎮 Buy New Cards</h2>
                    <div class="price-info">
                        Price: <span id="card-price">10 ETB</span> per card
                    </div>
                    <button class="buy-btn" onclick="buyCard()" id="buy-btn">Buy One Card</button>
                    <div id="message" class="message"></div>
                </div>
                
                <div class="cards-section">
                    <h2 class="section-title">📊 Your Bingo Cards</h2>
                    <div id="cards-grid" class="card-grid">
                        <!-- Cards will be loaded here -->
                    </div>
                </div>
                
                <div class="back-to-bot">
                    <a href="https://t.me/YOUR_BOT_USERNAME" class="back-btn">⬅️ Back to Telegram Bot</a>
                </div>
            </div>
        </div>
        
        <script>
            let currentUserId = null;
            
            function login() {
                const userId = document.getElementById('user-id').value.trim();
                if (!userId) {
                    alert('Please enter your User ID');
                    return;
                }
                
                currentUserId = userId;
                document.getElementById('display-user-id').textContent = userId;
                document.getElementById('login-section').style.display = 'none';
                document.getElementById('main-content').style.display = 'block';
                
                loadUserData();
            }
            
            function logout() {
                currentUserId = null;
                document.getElementById('login-section').style.display = 'block';
                document.getElementById('main-content').style.display = 'none';
                document.getElementById('user-id').value = '';
            }
            
            async function loadUserData() {
                try {
                    const response = await fetch(`/api/user/${currentUserId}`);
                    const data = await response.json();
                    
                    document.getElementById('balance').textContent = data.balance + ' ETB';
                    
                    // Load cards
                    const cardsGrid = document.getElementById('cards-grid');
                    if (data.cards && data.cards.length > 0) {
                        cardsGrid.innerHTML = data.cards.map(card => renderBingoCard(card)).join('');
                    } else {
                        cardsGrid.innerHTML = '<p style="grid-column: 1/-1; text-align: center;">You don\'t have any cards yet. Buy one above!</p>';
                    }
                } catch (error) {
                    showMessage('Error loading data', 'error');
                }
            }
            
            function renderBingoCard(card) {
                // Card is stored as JSON string in database
                let cardData;
                try {
                    cardData = typeof card === 'string' ? JSON.parse(card) : card;
                } catch {
                    cardData = card;
                }
                
                // If it's just a card ID, show placeholder
                if (!cardData.numbers) {
                    return `
                        <div class="bingo-card">
                            <div class="card-header">Card #${card.id || 'New'}</div>
                            <div style="text-align: center; padding: 20px;">
                                Card details loading...
                            </div>
                        </div>
                    `;
                }
                
                const numbers = cardData.numbers;
                
                let html = `
                    <div class="bingo-card">
                        <div class="card-header">Card #${card.id || cardData.id || 'New'}</div>
                        <table class="bingo-table">
                            <tr>
                                <th>B</th><th>I</th><th>N</th><th>G</th><th>O</th>
                            </tr>
                `;
                
                for (let row = 0; row < 5; row++) {
                    html += '<tr>';
                    for (let col = 0; col < 5; col++) {
                        let value = numbers[col][row];
                        let className = '';
                        
                        if (value === 'FREE') {
                            className = 'free-space';
                            value = '★';
                        }
                        
                        html += `<td class="${className}">${value}</td>`;
                    }
                    html += '</tr>';
                }
                
                html += '</table></div>';
                return html;
            }
            
            async function buyCard() {
                try {
                    document.getElementById('buy-btn').disabled = true;
                    
                    const response = await fetch('/api/buy-card', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            user_id: currentUserId
                        })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        showMessage('✅ Card purchased successfully!', 'success');
                        loadUserData(); // Reload cards and balance
                    } else {
                        showMessage('❌ ' + data.message, 'error');
                    }
                } catch (error) {
                    showMessage('Error purchasing card', 'error');
                } finally {
                    document.getElementById('buy-btn').disabled = false;
                }
            }
            
            function showMessage(text, type) {
                const msgDiv = document.getElementById('message');
                msgDiv.textContent = text;
                msgDiv.className = 'message ' + type;
                
                setTimeout(() => {
                    msgDiv.style.display = 'none';
                }, 3000);
            }
            
            // Auto-refresh every 30 seconds
            setInterval(() => {
                if (currentUserId) {
                    loadUserData();
                }
            }, 30000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/api/user/{user_id}")
async def get_user(user_id: str):
    """Get user data"""
    db = load_db()
    users = db.get("users", {})
    
    if user_id not in users:
        return {
            "balance": 0,
            "cards": [],
            "username": None
        }
    
    user = users[user_id]
    return {
        "balance": user.get("balance", 0),
        "cards": user.get("cards", []),
        "username": user.get("username")
    }

@app.post("/api/buy-card")
async def buy_card(request: Request):
    """Buy a new bingo card"""
    data = await request.json()
    user_id = str(data.get("user_id"))
    
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID required")
    
    db = load_db()
    users = db.setdefault("users", {})
    
    # Initialize user if not exists
    if user_id not in users:
        users[user_id] = {
            "balance": 100,  # Welcome bonus
            "cards": [],
            "purchases": []
        }
    
    user = users[user_id]
    
    # Check balance (10 ETB per card)
    if user["balance"] < 10:
        return JSONResponse({
            "success": False,
            "message": "Insufficient balance"
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
    user["balance"] -= 10
    user["cards"].append(new_card)
    user.setdefault("total_spent", 0)
    user["total_spent"] += 10
    
    # Save to database
    save_db(db)
    
    return JSONResponse({
        "success": True,
        "message": "Card purchased successfully",
        "card": new_card,
        "new_balance": user["balance"]
    })

@app.get("/api/card/{user_id}/{card_id}")
async def get_card(user_id: str, card_id: int):
    """Get specific card details"""
    db = load_db()
    users = db.get("users", {})
    
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found")
    
    cards = users[user_id].get("cards", [])
    for card in cards:
        if card.get("id") == card_id:
            return card
    
    raise HTTPException(status_code=404, detail="Card not found")

@app.post("/api/mark-number")
async def mark_number(request: Request):
    """Mark a number on a card"""
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
            return {"success": True, "marked": card["marked"]}
    
    raise HTTPException(status_code=404, detail="Card not found")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)