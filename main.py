import json
import random
import sqlite3
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.templating import Jinja2Templates
import httpx

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ---------- Configuration ----------
BOT_TOKEN = "YOUR_BOT_TOKEN"               # Replace with your bot token
ADMIN_ID = 8741250511                       # Your admin Telegram ID
BASE_URL = "https://yourdomain.com/game"    # Replace with your domain
PRICE_PER_CARD = 10                          # ETB per card
MAX_CARDS = 20

# ---------- Database setup ----------
conn = sqlite3.connect("bingo.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round INTEGER DEFAULT 1,
        started BOOLEAN DEFAULT 0,
        called_numbers TEXT DEFAULT '[]'
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        balance_etb REAL DEFAULT 0,
        active_games INTEGER DEFAULT 0,
        total_stake_etb REAL DEFAULT 0
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_cards (
        game_id INTEGER,
        user_id INTEGER,
        card_id INTEGER,
        card_data TEXT,               -- JSON of the 5x5 grid
        marked_numbers TEXT DEFAULT '[]',
        FOREIGN KEY(game_id) REFERENCES games(id),
        FOREIGN KEY(user_id) REFERENCES players(user_id)
    )
""")

conn.commit()

# ---------- WebSocket connection manager ----------
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Set[WebSocket]] = {}

    async def connect(self, game_id: int, websocket: WebSocket):
        await websocket.accept()
        if game_id not in self.active_connections:
            self.active_connections[game_id] = set()
        self.active_connections[game_id].add(websocket)

    def disconnect(self, game_id: int, websocket: WebSocket):
        if game_id in self.active_connections:
            self.active_connections[game_id].discard(websocket)
            if not self.active_connections[game_id]:
                del self.active_connections[game_id]

    async def broadcast(self, game_id: int, message: dict):
        if game_id in self.active_connections:
            for ws in self.active_connections[game_id].copy():
                try:
                    await ws.send_json(message)
                except:
                    self.disconnect(game_id, ws)

manager = ConnectionManager()

# ---------- Telegram helper ----------
async def send_telegram_message(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "text": text})

# ---------- Bingo card generator ----------
def generate_bingo_card() -> list:
    """Returns a 5x5 bingo card as a list of lists (columns, rows)"""
    card = []
    # Column ranges: B:1-15, I:16-30, N:31-45, G:46-60, O:61-75
    ranges = [(1,15), (16,30), (31,45), (46,60), (61,75)]
    for col in range(5):
        numbers = random.sample(range(ranges[col][0], ranges[col][1]+1), 5)
        card.append(numbers)
    # FREE space in the center (row 2, col 2)
    card[2][2] = "FREE"
    return card

# ---------- Game state ----------
def get_game_state(game_id: int):
    cursor.execute("SELECT round, started, called_numbers FROM games WHERE id = ?", (game_id,))
    row = cursor.fetchone()
    if not row:
        return None
    round_num, started, called_json = row
    called = json.loads(called_json)

    # Get players and their card counts
    cursor.execute("""
        SELECT DISTINCT user_id FROM user_cards WHERE game_id = ?
    """, (game_id,))
    player_ids = [r[0] for r in cursor.fetchall()]

    players = []
    taken_cards = set()
    for uid in player_ids:
        cursor.execute("SELECT name FROM players WHERE user_id = ?", (uid,))
        name_row = cursor.fetchone()
        name = name_row[0] if name_row else f"User{uid}"
        cursor.execute("SELECT COUNT(*) FROM user_cards WHERE game_id = ? AND user_id = ?", (game_id, uid))
        card_count = cursor.fetchone()[0]
        players.append({"id": uid, "name": name, "card_count": card_count})
        # For taken cards, we need to know which card numbers are used. We store card_id separately.
        # For simplicity, we'll just treat all cards as taken by someone else if they are in user_cards.
        cursor.execute("SELECT card_id FROM user_cards WHERE game_id = ? AND user_id != ?", (game_id, uid))
        for cid in cursor.fetchall():
            taken_cards.add(cid[0])

    return {
        "round": round_num,
        "game_started": bool(started),
        "called_numbers": called,
        "players": players,
        "taken_cards": list(taken_cards)
    }

# ---------- API endpoints ----------
@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    cursor.execute("SELECT balance_etb, active_games, total_stake_etb FROM players WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        return {"balance_etb": row[0], "active_games": row[1], "total_stake_etb": row[2]}
    # Create new user with welcome bonus
    cursor.execute("INSERT INTO players (user_id, name, balance_etb) VALUES (?, ?, ?)",
                   (user_id, f"User{user_id}", 10.0))
    conn.commit()
    return {"balance_etb": 10.0, "active_games": 0, "total_stake_etb": 0}

@app.get("/api/game/{game_id}/state")
async def game_state(game_id: int):
    state = get_game_state(game_id)
    if not state:
        raise HTTPException(404, "Game not found")
    return state

# ---------- WebSocket endpoint ----------
@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, user_id: int):
    await manager.connect(game_id, websocket)
    try:
        # Send initial state
        state = get_game_state(game_id)
        if state:
            await websocket.send_json({"type": "connected", **state})
        else:
            await websocket.send_json({"type": "error", "message": "Game not found"})
            manager.disconnect(game_id, websocket)
            return

        while True:
            data = await websocket.receive_json()
            print(f"Received: {data} from user {user_id} in game {game_id}")

            if data["type"] == "ping":
                await websocket.send_json({"type": "pong"})

            elif data["type"] == "select_cards":
                card_ids = data["card_ids"]
                # Check balance
                cursor.execute("SELECT balance_etb FROM players WHERE user_id = ?", (user_id,))
                bal = cursor.fetchone()[0]
                total_cost = len(card_ids) * PRICE_PER_CARD
                if bal < total_cost:
                    await websocket.send_json({"type": "error", "message": "Insufficient balance"})
                    continue

                # Generate and store cards
                for cid in card_ids:
                    card_data = generate_bingo_card()
                    cursor.execute("""
                        INSERT INTO user_cards (game_id, user_id, card_id, card_data, marked_numbers)
                        VALUES (?, ?, ?, ?, ?)
                    """, (game_id, user_id, cid, json.dumps(card_data), json.dumps([])))

                # Deduct balance
                new_balance = bal - total_cost
                cursor.execute("UPDATE players SET balance_etb = ? WHERE user_id = ?", (new_balance, user_id))
                conn.commit()

                await websocket.send_json({"type": "cards_selected", "success": True})
                # Send each card individually so frontend can display
                for cid in card_ids:
                    cursor.execute("SELECT card_data FROM user_cards WHERE game_id = ? AND user_id = ? AND card_id = ?",
                                   (game_id, user_id, cid))
                    card_json = cursor.fetchone()[0]
                    card = json.loads(card_json)
                    await websocket.send_json({"type": "your_card", "card_id": cid, "card": card})

            elif data["type"] == "finalize":
                # No extra action needed; frontend just shows confirmation.
                await websocket.send_json({"type": "finalized", "success": True, "message": "Cards confirmed"})
                await manager.broadcast(game_id, {"type": "player_ready", "players": get_game_state(game_id)["players"]})

            elif data["type"] == "start_game":
                if user_id != ADMIN_ID:
                    await websocket.send_json({"type": "error", "message": "Only admin can start"})
                    continue
                cursor.execute("UPDATE games SET started = 1 WHERE id = ?", (game_id,))
                conn.commit()
                await manager.broadcast(game_id, {"type": "game_started", "round": get_game_state(game_id)["round"]})

            elif data["type"] == "call_number":
                if user_id != ADMIN_ID:
                    await websocket.send_json({"type": "error", "message": "Only admin can call numbers"})
                    continue
                number = data["number"]
                # Add to called numbers
                cursor.execute("SELECT called_numbers FROM games WHERE id = ?", (game_id,))
                called_json = cursor.fetchone()[0]
                called = json.loads(called_json)
                if number not in called:
                    called.append(number)
                    cursor.execute("UPDATE games SET called_numbers = ? WHERE id = ?", (json.dumps(called), game_id))
                    conn.commit()
                await manager.broadcast(game_id, {
                    "type": "number_called",
                    "number": number,
                    "called": called,
                    "left": 75 - len(called)
                })

            elif data["type"] == "mark_number":
                card_id = data["card_id"]
                number = data["number"]
                # Verify number is called
                cursor.execute("SELECT called_numbers FROM games WHERE id = ?", (game_id,))
                called = json.loads(cursor.fetchone()[0])
                if number not in called:
                    await websocket.send_json({"type": "error", "message": "Number not called yet"})
                    continue

                # Get current marked numbers for this card
                cursor.execute("SELECT marked_numbers FROM user_cards WHERE game_id = ? AND user_id = ? AND card_id = ?",
                               (game_id, user_id, card_id))
                row = cursor.fetchone()
                if not row:
                    await websocket.send_json({"type": "error", "message": "Card not found"})
                    continue
                marked = json.loads(row[0])
                if number not in marked:
                    marked.append(number)
                    cursor.execute("UPDATE user_cards SET marked_numbers = ? WHERE game_id = ? AND user_id = ? AND card_id = ?",
                                   (json.dumps(marked), game_id, user_id, card_id))
                    conn.commit()
                # Broadcast to all (so others see the mark if needed, though marks are per player)
                await manager.broadcast(game_id, {"type": "number_marked", "card_id": card_id, "number": number})

            elif data["type"] == "claim_bingo":
                # Validate bingo for the claimed card
                card_id = data["card_id"]
                cursor.execute("""
                    SELECT card_data, marked_numbers FROM user_cards
                    WHERE game_id = ? AND user_id = ? AND card_id = ?
                """, (game_id, user_id, card_id))
                row = cursor.fetchone()
                if not row:
                    await websocket.send_json({"type": "error", "message": "Card not found"})
                    continue

                card = json.loads(row[0])
                marked = set(json.loads(row[1]))

                # Check all 5 rows, 5 columns, 2 diagonals for a full line
                def is_bingo():
                    # rows
                    for r in range(5):
                        if all(card[c][r] == "FREE" or card[c][r] in marked for c in range(5)):
                            return True
                    # columns
                    for c in range(5):
                        if all(card[c][r] == "FREE" or card[c][r] in marked for r in range(5)):
                            return True
                    # diagonals
                    if all(card[i][i] == "FREE" or card[i][i] in marked for i in range(5)):
                        return True
                    if all(card[i][4-i] == "FREE" or card[i][4-i] in marked for i in range(5)):
                        return True
                    return False

                if not is_bingo():
                    await websocket.send_json({"type": "error", "message": "Not a valid bingo"})
                    continue

                # Bingo is valid! End game.
                cursor.execute("SELECT name FROM players WHERE user_id = ?", (user_id,))
                winner_name = cursor.fetchone()[0]

                # Calculate prize (sum of all cards in this game)
                cursor.execute("SELECT COUNT(*) FROM user_cards WHERE game_id = ?", (game_id,))
                total_cards = cursor.fetchone()[0]
                prize = total_cards * PRICE_PER_CARD

                # Credit winner
                cursor.execute("UPDATE players SET balance_etb = balance_etb + ? WHERE user_id = ?", (prize, user_id))
                conn.commit()

                # Broadcast win
                await manager.broadcast(game_id, {
                    "type": "game_won",
                    "winner": {"id": user_id, "name": winner_name, "card_id": card_id},
                    "prize": prize
                })

            elif data["type"] == "start_next_round":
                if user_id != ADMIN_ID:
                    await websocket.send_json({"type": "error", "message": "Only admin"})
                    continue

                # 1. Create new game
                cursor.execute("INSERT INTO games (round) VALUES (1)")
                new_game_id = cursor.lastrowid
                conn.commit()

                # 2. Get list of players from current game
                cursor.execute("SELECT DISTINCT user_id FROM user_cards WHERE game_id = ?", (game_id,))
                player_ids = [row[0] for row in cursor.fetchall()]

                # 3. Send them new game links via Telegram
                for pid in player_ids:
                    link = f"{BASE_URL}?user_id={pid}&game_id={new_game_id}&admin_id={ADMIN_ID}"
                    await send_telegram_message(pid, f"🚀 New round started! Click to play: {link}")

                # 4. Notify current game that it's ending
                await manager.broadcast(game_id, {"type": "game_reset", "players": []})

                # 5. Redirect admin to new game
                await websocket.send_json({
                    "type": "redirect",
                    "url": f"{BASE_URL}?user_id={ADMIN_ID}&game_id={new_game_id}&admin_id={ADMIN_ID}"
                })

    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
        print(f"User {user_id} disconnected from game {game_id}")

# ---------- Serve HTML page ----------
@app.get("/game")
async def game_page(request: Request, user_id: int, game_id: int, admin_id: int):
    # Fetch actual balance, active games, stake from DB
    cursor.execute("SELECT balance_etb, active_games, total_stake_etb FROM players WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        bal, active, stake = row
    else:
        bal, active, stake = 10.0, 0, 0  # new user, will be created when they connect

    return templates.TemplateResponse("bingo.html", {
        "request": request,
        "user_id": user_id,
        "game_id": game_id,
        "admin_id": admin_id,
        "price_per_card": PRICE_PER_CARD,
        "max_cards": MAX_CARDS,
        "initial_balance": bal,
        "initial_active_games": active,
        "initial_stake": stake
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)