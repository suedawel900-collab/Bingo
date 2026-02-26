import sqlite3
import json
import random
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ==========================
# DATABASE
# ==========================

conn = sqlite3.connect("bingo.db", check_same_thread=False)
cursor = conn.cursor()

PRICE_PER_CARD = 10
HOUSE_COMMISSION_PERCENT = 20
ADMIN_ID = int(os.getenv('ADMIN_ID', '123456789'))  # 🔥 Set your Telegram admin ID

# ==========================
# CREATE TABLES (with pattern support)
# ==========================

cursor.execute("""
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    positions TEXT NOT NULL  -- JSON array of [row, col] pairs
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round INTEGER DEFAULT 1,
    started INTEGER DEFAULT 0,
    called_numbers TEXT DEFAULT '[]',
    pattern_id INTEGER DEFAULT 1,
    FOREIGN KEY (pattern_id) REFERENCES patterns(id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER,
    user_id INTEGER,
    card_id INTEGER,
    card_data TEXT,
    marked_numbers TEXT DEFAULT '[]'
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0,
    wins INTEGER DEFAULT 0
)
""")

conn.commit()

# ==========================
# LOAD / GENERATE 100 PATTERNS
# ==========================

def generate_patterns():
    """Return a list of 100 pattern dicts with name and positions."""
    patterns = []

    # 1. Standard (any row, column, or diagonal)
    patterns.append({
        "name": "Standard",
        "description": "Any row, column, or diagonal",
        "positions": "standard"
    })

    # 2. Four Corners
    patterns.append({
        "name": "Four Corners",
        "description": "All four corners",
        "positions": [[0,0], [0,4], [4,0], [4,4]]
    })

    # 3. Blackout (full card)
    patterns.append({
        "name": "Blackout",
        "description": "All numbers on the card",
        "positions": "blackout"
    })

    # 4. X (both diagonals)
    patterns.append({
        "name": "X",
        "description": "Both diagonals",
        "positions": [[0,0],[1,1],[2,2],[3,3],[4,4], [0,4],[1,3],[3,1],[4,0]]
    })

    # 5. Plus (middle row + middle column)
    plus = []
    for col in range(5):
        plus.append([2, col])  # middle row
    for row in range(5):
        if row != 2:
            plus.append([row, 2])  # middle column, avoid double count center
    patterns.append({
        "name": "Plus",
        "description": "Middle row and middle column",
        "positions": plus
    })

    # 6. Top Row
    patterns.append({
        "name": "Top Row",
        "description": "First row",
        "positions": [[0,0],[0,1],[0,2],[0,3],[0,4]]
    })

    # 7. Middle Row
    patterns.append({
        "name": "Middle Row",
        "description": "Third row",
        "positions": [[2,0],[2,1],[2,2],[2,3],[2,4]]
    })

    # 8. Bottom Row
    patterns.append({
        "name": "Bottom Row",
        "description": "Fifth row",
        "positions": [[4,0],[4,1],[4,2],[4,3],[4,4]]
    })

    # 9. Left Column
    patterns.append({
        "name": "Left Column",
        "description": "First column",
        "positions": [[0,0],[1,0],[2,0],[3,0],[4,0]]
    })

    # 10. Right Column
    patterns.append({
        "name": "Right Column",
        "description": "Fifth column",
        "positions": [[0,4],[1,4],[2,4],[3,4],[4,4]]
    })

    # 11. Diagonal TL-BR
    patterns.append({
        "name": "Diagonal TL-BR",
        "description": "Top-left to bottom-right",
        "positions": [[0,0],[1,1],[2,2],[3,3],[4,4]]
    })

    # 12. Diagonal TR-BL
    patterns.append({
        "name": "Diagonal TR-BL",
        "description": "Top-right to bottom-left",
        "positions": [[0,4],[1,3],[2,2],[3,1],[4,0]]
    })

    # 13. Small Square (center 3x3)
    small_square = []
    for r in range(1,4):
        for c in range(1,4):
            small_square.append([r,c])
    patterns.append({
        "name": "Small Square",
        "description": "3x3 center block",
        "positions": small_square
    })

    # 14. Large Square (border)
    border = []
    for i in range(5):
        border.append([0,i])  # top row
        border.append([4,i])  # bottom row
    for i in range(1,4):
        border.append([i,0])  # left column except corners
        border.append([i,4])  # right column except corners
    patterns.append({
        "name": "Large Square",
        "description": "Outer border",
        "positions": border
    })

    # 15. Letter L (top-left L shape)
    l_shape = [[0,0],[1,0],[2,0],[3,0],[4,0], [4,1],[4,2],[4,3],[4,4]]
    patterns.append({
        "name": "L Shape",
        "description": "Left column and bottom row",
        "positions": l_shape
    })

    # 16. Letter T
    t_shape = []
    for c in range(5):
        t_shape.append([0,c])  # top row
    for r in range(1,5):
        t_shape.append([r,2])  # middle column down
    patterns.append({
        "name": "T Shape",
        "description": "Top row and middle column",
        "positions": t_shape
    })

    # 17. Letter U
    u_shape = []
    for r in range(5):
        u_shape.append([r,0])  # left column
        u_shape.append([r,4])  # right column
    for c in range(1,4):
        u_shape.append([4,c])  # bottom row except corners
    patterns.append({
        "name": "U Shape",
        "description": "Left, right columns and bottom row",
        "positions": u_shape
    })

    # Generate remaining patterns to reach 100
    base = patterns.copy()
    while len(patterns) < 100:
        for p in base:
            if len(patterns) >= 100:
                break
            new_p = p.copy()
            new_p["name"] = p["name"] + f" variant {len(patterns)}"
            patterns.append(new_p)

    return patterns[:100]

def init_patterns():
    """Insert patterns into database if table is empty."""
    cursor.execute("SELECT COUNT(*) FROM patterns")
    count = cursor.fetchone()[0]
    if count > 0:
        return

    patterns = generate_patterns()
    for p in patterns:
        positions = p["positions"]
        if isinstance(positions, str):
            positions_json = json.dumps({"type": positions})
        else:
            positions_json = json.dumps(positions)
        cursor.execute("INSERT INTO patterns (name, description, positions) VALUES (?, ?, ?)",
                       (p["name"], p["description"], positions_json))
    conn.commit()
    logger.info(f"✅ Inserted {len(patterns)} patterns into database.")

init_patterns()

# ==========================
# HELPER FUNCTIONS
# ==========================

def get_game_state(game_id: int):
    cursor.execute("SELECT round, started, called_numbers, pattern_id FROM games WHERE id = ?", (game_id,))
    row = cursor.fetchone()
    if not row:
        return None
    round_number, started, called_numbers, pattern_id = row
    cursor.execute("SELECT card_id FROM user_cards WHERE game_id = ?", (game_id,))
    taken_cards = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT name, description FROM patterns WHERE id = ?", (pattern_id,))
    pattern = cursor.fetchone()
    pattern_name = pattern[0] if pattern else "Standard"
    return {
        "round": round_number,
        "started": bool(started),
        "called_numbers": json.loads(called_numbers),
        "taken_cards": taken_cards,
        "pattern_id": pattern_id,
        "pattern_name": pattern_name
    }

def calculate_prize_pool(game_id: int):
    cursor.execute("SELECT COUNT(*) FROM user_cards WHERE game_id = ?", (game_id,))
    total_cards = cursor.fetchone()[0]
    return total_cards * PRICE_PER_CARD

def pay_winner(game_id: int, winner_id: int):
    prize_pool = calculate_prize_pool(game_id)
    if prize_pool <= 0:
        logger.warning("⚠ Prize pool is 0")
        return 0

    house_cut = round(prize_pool * HOUSE_COMMISSION_PERCENT / 100, 2)
    winner_prize = round(prize_pool - house_cut, 2)

    # Pay winner
    cursor.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE id = ?", (winner_prize, winner_id))
    # Pay house (admin)
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (house_cut, ADMIN_ID))
    conn.commit()

    logger.info(f"🏆 Winner {winner_id} received {winner_prize} ETB")
    logger.info(f"🏦 House received {house_cut} ETB")
    return winner_prize

def reset_round(game_id: int):
    cursor.execute("UPDATE games SET round = round + 1, started = 0, called_numbers = '[]' WHERE id = ?", (game_id,))
    cursor.execute("DELETE FROM user_cards WHERE game_id = ?", (game_id,))
    conn.commit()
    logger.info(f"🔄 Round reset for game {game_id}")

def check_bingo(card_data, called_numbers_set, pattern_data):
    """
    Return True if the card meets the pattern requirements.
    """
    if isinstance(pattern_data, dict):
        if pattern_data.get("type") == "standard":
            # Standard: any row, column, or diagonal
            # rows
            for row in range(5):
                if all(card_data[col][row] == 'FREE' or card_data[col][row] in called_numbers_set for col in range(5)):
                    return True
            # columns
            for col in range(5):
                if all(card_data[col][row] == 'FREE' or card_data[col][row] in called_numbers_set for row in range(5)):
                    return True
            # diagonals
            if all(card_data[i][i] == 'FREE' or card_data[i][i] in called_numbers_set for i in range(5)):
                return True
            if all(card_data[i][4-i] == 'FREE' or card_data[i][4-i] in called_numbers_set for i in range(5)):
                return True
            return False
        elif pattern_data.get("type") == "blackout":
            for row in range(5):
                for col in range(5):
                    val = card_data[col][row]
                    if val != 'FREE' and val not in called_numbers_set:
                        return False
            return True
        else:
            return False
    else:
        # pattern_data is a list of positions
        for pos in pattern_data:
            row, col = pos
            val = card_data[col][row]
            if val != 'FREE' and val not in called_numbers_set:
                return False
        return True

# ==========================
# API ENDPOINTS
# ==========================

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {"message": "Bingo Game Server", "status": "running"}

@app.get("/game", response_class=HTMLResponse)
async def game_page(request: Request, user_id: int, game_id: int = 1):
    # Ensure user exists
    cursor.execute("INSERT OR IGNORE INTO users (id, balance) VALUES (?, ?)", (user_id, 10.0))
    conn.commit()
    cursor.execute("SELECT balance, wins FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    balance = user[0] if user else 10.0
    wins = user[1] if user else 0

    # Ensure game exists with default pattern (id 1 = Standard)
    cursor.execute("INSERT OR IGNORE INTO games (id, pattern_id) VALUES (?, 1)", (game_id,))
    conn.commit()

    return templates.TemplateResponse("bingo.html", {
        "request": request,
        "user_id": user_id,
        "game_id": game_id,
        "admin_id": ADMIN_ID,
        "price_per_card": PRICE_PER_CARD,
        "max_cards": 20,
        "initial_balance": balance,
        "initial_active_games": 0,
        "initial_stake": 0
    })

@app.get("/api/game/{game_id}/state")
async def game_state_api(game_id: int):
    return get_game_state(game_id)

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    cursor.execute("SELECT balance, wins FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        return {"balance": row[0], "wins": row[1]}
    return {"balance": 10.0, "wins": 0}

@app.get("/api/patterns")
async def list_patterns():
    cursor.execute("SELECT id, name, description FROM patterns ORDER BY id")
    rows = cursor.fetchall()
    return [{"id": r[0], "name": r[1], "description": r[2]} for r in rows]

# ==========================
# WEBSOCKET
# ==========================

connections = {}

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    await websocket.accept()
    if game_id not in connections:
        connections[game_id] = []
    connections[game_id].append(websocket)

    # Send initial state
    state = get_game_state(game_id)
    if state:
        await websocket.send_json({"type": "connected", **state})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            logger.info(f"Received {msg_type} from user {user_id} in game {game_id}")

            if msg_type == "select_cards":
                card_ids = data["card_ids"]
                # Check if cards are available
                cursor.execute("SELECT card_id FROM user_cards WHERE game_id = ?", (game_id,))
                taken = {r[0] for r in cursor.fetchall()}
                available = [cid for cid in card_ids if cid not in taken]
                if len(available) != len(card_ids):
                    await websocket.send_json({"type": "error", "message": "Some cards already taken"})
                    continue
                # Store each card (generate random cards)
                for cid in card_ids:
                    card = []
                    for col in range(5):
                        min_num = col * 15 + 1
                        max_num = (col + 1) * 15
                        numbers = random.sample(range(min_num, max_num + 1), 5)
                        card.append(numbers)
                    card[2][2] = "FREE"
                    cursor.execute("INSERT INTO user_cards (game_id, user_id, card_id, card_data, marked_numbers) VALUES (?, ?, ?, ?, ?)",
                                   (game_id, user_id, cid, json.dumps(card), "[]"))
                conn.commit()
                await websocket.send_json({"type": "cards_selected", "success": True})

            elif msg_type == "call_number":
                if user_id != ADMIN_ID:
                    await websocket.send_json({"type": "error", "message": "Not authorized"})
                    continue
                number = data["number"]
                cursor.execute("SELECT called_numbers FROM games WHERE id = ?", (game_id,))
                called = json.loads(cursor.fetchone()[0])
                if number not in called:
                    called.append(number)
                    cursor.execute("UPDATE games SET called_numbers = ? WHERE id = ?", (json.dumps(called), game_id))
                    conn.commit()
                # Broadcast to all
                for ws in connections.get(game_id, []):
                    try:
                        await ws.send_json({"type": "number_called", "number": number})
                    except:
                        pass
                # Check for winner after each call
                await check_for_winner(game_id)

            elif msg_type == "set_pattern":
                if user_id != ADMIN_ID:
                    await websocket.send_json({"type": "error", "message": "Not authorized"})
                    continue
                pattern_id = data["pattern_id"]
                cursor.execute("UPDATE games SET pattern_id = ? WHERE id = ?", (pattern_id, game_id))
                conn.commit()
                # Broadcast pattern change
                cursor.execute("SELECT name FROM patterns WHERE id = ?", (pattern_id,))
                pattern_name = cursor.fetchone()[0]
                for ws in connections.get(game_id, []):
                    try:
                        await ws.send_json({"type": "pattern_changed", "pattern_id": pattern_id, "pattern_name": pattern_name})
                    except:
                        pass

            elif msg_type == "start_game":
                # Admin only
                if user_id != ADMIN_ID:
                    await websocket.send_json({"type": "error", "message": "You are not authorized to start the game"})
                    continue

                try:
                    cursor.execute("UPDATE games SET started = 1 WHERE id = ?", (game_id,))
                    conn.commit()
                    logger.info(f"Game {game_id} started by admin {user_id}")

                    # Broadcast to all players
                    for ws in connections.get(game_id, []):
                        try:
                            await ws.send_json({"type": "game_started"})
                        except:
                            pass

                    # Confirm to admin
                    await websocket.send_json({"type": "start_game", "success": True})

                except Exception as e:
                    logger.error(f"Failed to start game {game_id}: {e}")
                    await websocket.send_json({"type": "error", "message": "Database error, could not start game"})

            elif msg_type == "winner":
                # Admin can manually trigger winner (for testing)
                if user_id != ADMIN_ID:
                    continue
                winner_id = data.get("user_id")
                if not winner_id:
                    continue
                cursor.execute("SELECT card_id, card_data FROM user_cards WHERE game_id = ? AND user_id = ?", (game_id, winner_id))
                rows = cursor.fetchall()
                if not rows:
                    await websocket.send_json({"type": "error", "message": "User has no cards in this game"})
                    continue
                # Use the first card for simplicity
                card_id, card_json = rows[0]
                card_data = json.loads(card_json)
                cursor.execute("SELECT called_numbers, pattern_id FROM games WHERE id = ?", (game_id,))
                called_json, pattern_id = cursor.fetchone()
                called = set(json.loads(called_json))
                cursor.execute("SELECT positions FROM patterns WHERE id = ?", (pattern_id,))
                pattern_positions = json.loads(cursor.fetchone()[0])
                if check_bingo(card_data, called, pattern_positions):
                    await handle_win(game_id, winner_id, card_id)
                else:
                    await websocket.send_json({"type": "error", "message": "No bingo with current pattern"})

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                logger.warning(f"Unknown message type: {msg_type}")

    except Exception as e:
        logger.error(f"WebSocket error for user {user_id} in game {game_id}: {e}")
    finally:
        if websocket in connections.get(game_id, []):
            connections[game_id].remove(websocket)

async def check_for_winner(game_id: int):
    """Check all players' cards for a bingo using the active pattern."""
    cursor.execute("SELECT called_numbers, pattern_id FROM games WHERE id = ?", (game_id,))
    row = cursor.fetchone()
    if not row:
        return
    called_json, pattern_id = row
    called = set(json.loads(called_json))
    if not called:
        return

    cursor.execute("SELECT positions FROM patterns WHERE id = ?", (pattern_id,))
    pattern_positions = json.loads(cursor.fetchone()[0])

    cursor.execute("SELECT user_id, card_id, card_data FROM user_cards WHERE game_id = ?", (game_id,))
    for user_id, card_id, card_json in cursor.fetchall():
        card_data = json.loads(card_json)
        if check_bingo(card_data, called, pattern_positions):
            await handle_win(game_id, user_id, card_id)
            return  # only one winner per round

async def handle_win(game_id: int, winner_id: int, card_id: int):
    """Process a win: pay, announce, reset."""
    # 1. Pay
    prize = pay_winner(game_id, winner_id)

    # 2. Announce winner
    for ws in connections.get(game_id, []):
        try:
            await ws.send_json({
                "type": "game_won",
                "winner": {"id": winner_id, "card_id": card_id},
                "prize": prize
            })
        except:
            pass

    # 3. Reset round
    reset_round(game_id)

    # 4. Send new state
    new_state = get_game_state(game_id)
    for ws in connections.get(game_id, []):
        try:
            await ws.send_json({"type": "new_round", **new_state})
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)