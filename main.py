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
    # ... (same as before) ...
    # (keep your existing generate_patterns function)
    pass

def init_patterns():
    cursor.execute("SELECT COUNT(*) FROM patterns")
    count = cursor.fetchone()[0]
    if count == 0:
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
        logger.info(f"✅ Inserted {len(patterns)} patterns.")

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

    cursor.execute("UPDATE users SET balance = balance + ?, wins = wins + 1 WHERE id = ?", (winner_prize, winner_id))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (house_cut, ADMIN_ID))
    conn.commit()

    logger.info(f"🏆 Winner {winner_id} received {winner_prize} ETB")
    return winner_prize

def reset_round(game_id: int):
    cursor.execute("UPDATE games SET round = round + 1, started = 0, called_numbers = '[]' WHERE id = ?", (game_id,))
    cursor.execute("DELETE FROM user_cards WHERE game_id = ?", (game_id,))
    conn.commit()
    logger.info(f"🔄 Round reset for game {game_id}")

def check_bingo(card_data, called_numbers_set, pattern_data):
    # ... (same as before) ...
    pass

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

    # Ensure game exists
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
    logger.info(f"WebSocket connected: game={game_id}, user={user_id}")

    if game_id not in connections:
        connections[game_id] = []
    connections[game_id].append(websocket)

    # Ensure game exists (again, just in case)
    cursor.execute("INSERT OR IGNORE INTO games (id, pattern_id) VALUES (?, 1)", (game_id,))
    conn.commit()

    # Send initial state
    state = get_game_state(game_id)
    if state:
        await websocket.send_json({"type": "connected", **state})
    else:
        logger.error(f"Game {game_id} not found after creation?")
        await websocket.send_json({"type": "error", "message": "Game not found"})
        connections[game_id].remove(websocket)
        return

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            logger.info(f"Received {msg_type} from user {user_id} in game {game_id}")

            # ---------- SELECT CARDS ----------
            if msg_type == "select_cards":
                # ... (your existing code) ...

            # ---------- CALL NUMBER ----------
            elif msg_type == "call_number":
                # ... (existing) ...

            # ---------- SET PATTERN ----------
            elif msg_type == "set_pattern":
                # ... (existing) ...

            # ---------- START GAME (FIXED) ----------
            elif msg_type == "start_game":
                # Check admin
                if user_id != ADMIN_ID:
                    logger.warning(f"Non-admin {user_id} attempted to start game {game_id}")
                    await websocket.send_json({"type": "error", "message": "You are not authorized to start the game"})
                    continue

                try:
                    # Update database
                    cursor.execute("UPDATE games SET started = 1 WHERE id = ?", (game_id,))
                    if cursor.rowcount == 0:
                        # Game didn't exist – insert it
                        cursor.execute("INSERT INTO games (id, started) VALUES (?, 1)", (game_id,))
                    conn.commit()
                    logger.info(f"Game {game_id} started by admin {user_id}")

                    # Broadcast to all connected clients
                    for ws in connections.get(game_id, []):
                        try:
                            await ws.send_json({"type": "game_started"})
                        except Exception as e:
                            logger.error(f"Error broadcasting to client: {e}")

                    # Confirm to admin
                    await websocket.send_json({"type": "start_game", "success": True})

                except Exception as e:
                    logger.exception(f"Error starting game {game_id}: {e}")
                    await websocket.send_json({"type": "error", "message": f"Database error: {str(e)}"})

            # ---------- WINNER (manual) ----------
            elif msg_type == "winner":
                # ... (existing) ...

            # ---------- PING ----------
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                logger.warning(f"Unknown message type: {msg_type}")

    except Exception as e:
        logger.exception(f"WebSocket error for user {user_id} in game {game_id}: {e}")
    finally:
        if websocket in connections.get(game_id, []):
            connections[game_id].remove(websocket)
        logger.info(f"WebSocket disconnected: game={game_id}, user={user_id}")

async def check_for_winner(game_id: int):
    # ... (existing) ...

async def handle_win(game_id: int, winner_id: int, card_id: int):
    # ... (existing) ...

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)