import sqlite3
import json
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================
# DATABASE
# ==============================

conn = sqlite3.connect("bingo.db", check_same_thread=False)
cursor = conn.cursor()

PRICE_PER_CARD = 10  # change if needed

# Create tables if not exist
cursor.execute("""
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round INTEGER DEFAULT 1,
    started INTEGER DEFAULT 0,
    called_numbers TEXT DEFAULT '[]'
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER,
    user_id INTEGER,
    card_id INTEGER,
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

# ==============================
# HELPER FUNCTIONS
# ==============================

def get_game_state(game_id: int):
    cursor.execute("SELECT round, started, called_numbers FROM games WHERE id = ?", (game_id,))
    row = cursor.fetchone()

    if not row:
        return None

    round_number, started, called_numbers = row

    cursor.execute("SELECT card_id FROM user_cards WHERE game_id = ?", (game_id,))
    taken_cards = [r[0] for r in cursor.fetchall()]

    return {
        "round": round_number,
        "started": started,
        "called_numbers": json.loads(called_numbers),
        "taken_cards": taken_cards
    }


def calculate_prize_pool(game_id: int):
    cursor.execute("SELECT COUNT(*) FROM user_cards WHERE game_id = ?", (game_id,))
    total_cards = cursor.fetchone()[0]
    return total_cards * PRICE_PER_CARD


def pay_winner(game_id: int, winner_id: int):
    prize = calculate_prize_pool(game_id)

    if prize <= 0:
        print("⚠ Prize pool is 0")
        return 0

    # Add balance to Telegram wallet
    cursor.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE id = ?
    """, (prize, winner_id))

    # Increase win count
    cursor.execute("""
        UPDATE users
        SET wins = wins + 1
        WHERE id = ?
    """, (winner_id,))

    conn.commit()

    print(f"🏆 Winner {winner_id} received {prize} ETB")

    return prize


def reset_round(game_id: int):
    # Increase round and reset state
    cursor.execute("""
        UPDATE games
        SET round = round + 1,
            started = 0,
            called_numbers = '[]'
        WHERE id = ?
    """, (game_id,))

    # Unlock all cards
    cursor.execute("DELETE FROM user_cards WHERE game_id = ?", (game_id,))

    conn.commit()

    print(f"🔄 Round reset for game {game_id}")


# ==============================
# API
# ==============================

@app.get("/api/game/{game_id}/state")
def game_state(game_id: int):
    return get_game_state(game_id)


# ==============================
# WEBSOCKET
# ==============================

connections = {}


@app.websocket("/ws/{game_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int):
    await websocket.accept()

    if game_id not in connections:
        connections[game_id] = []

    connections[game_id].append(websocket)

    try:
        while True:
            data = await websocket.receive_json()

            # ======================
            # CALL NUMBER
            # ======================
            if data["type"] == "call_number":
                number = data["number"]

                cursor.execute("SELECT called_numbers FROM games WHERE id = ?", (game_id,))
                current = json.loads(cursor.fetchone()[0])

                if number not in current:
                    current.append(number)

                cursor.execute("""
                    UPDATE games
                    SET called_numbers = ?
                    WHERE id = ?
                """, (json.dumps(current), game_id))

                conn.commit()

                for ws in connections[game_id]:
                    await ws.send_json({
                        "type": "number_called",
                        "number": number
                    })

            # ======================
            # WINNER DETECTED
            # ======================
            if data["type"] == "winner":
                winner_id = data["user_id"]  # must be Telegram user ID

                # 1️⃣ Pay winner FIRST
                prize = pay_winner(game_id, winner_id)

                # 2️⃣ Broadcast winner
                for ws in connections[game_id]:
                    await ws.send_json({
                        "type": "winner",
                        "winner_id": winner_id,
                        "prize": prize
                    })

                # 3️⃣ Reset round AFTER payment
                reset_round(game_id)

                # 4️⃣ Send new round state
                state = get_game_state(game_id)

                for ws in connections[game_id]:
                    await ws.send_json({
                        "type": "new_round",
                        **state
                    })

    except:
        connections[game_id].remove(websocket)