import os
import random
import asyncio
import sqlite3
import uuid
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

# -------------------- CONFIGURATION --------------------
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CARD_PRICE = 10
HOUSE_PERCENT = 20
DRAW_INTERVAL = 5
TOTAL_CARDS = 1000
DB_PATH = "bingo.db"

# Conversation states
AMOUNT, REFERENCE = range(2)
WITHDRAW_AMOUNT, WITHDRAW_ADDRESS = range(2, 4)

# Payment methods
PAYMENT_METHODS = {
    "telebirr": {"name": "📱 Telebirr", "account": "0953933030"},
    "cbebirr": {"name": "💳 CBE Birr", "account": "0953933030"},
}

# -------------------- DATABASE FUNCTIONS --------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                type TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_requests (
                request_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                method TEXT,
                proof TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                address TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Ensure admin exists with some balance (optional)
        conn.execute("INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, ?)",
                     (ADMIN_ID, "admin", 0))
        conn.commit()

async def get_user(user_id: int, username: str = None):
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            return dict(row)
        else:
            conn.execute("INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)",
                         (user_id, username, 0))
            conn.commit()
            return {"user_id": user_id, "username": username, "balance": 0}

async def update_balance(user_id: int, amount: float, tx_type: str, desc: str = ""):
    with get_db() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
                     (user_id, amount, tx_type, desc))
        conn.commit()

async def create_payment_request(user_id: int, amount: float, method: str) -> str:
    request_id = str(uuid.uuid4())[:8].upper()
    with get_db() as conn:
        conn.execute("INSERT INTO payment_requests (request_id, user_id, amount, method) VALUES (?, ?, ?, ?)",
                     (request_id, user_id, amount, method))
        conn.commit()
    return request_id

async def add_payment_proof(request_id: str, proof: str):
    with get_db() as conn:
        conn.execute("UPDATE payment_requests SET proof = ? WHERE request_id = ?", (proof, request_id))
        conn.commit()

async def get_payment_request(request_id: str):
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM payment_requests WHERE request_id = ?", (request_id,))
        row = cur.fetchone()
        return dict(row) if row else None

async def approve_payment(request_id: str):
    with get_db() as conn:
        conn.execute("UPDATE payment_requests SET status = 'approved' WHERE request_id = ?", (request_id,))
        conn.commit()

async def reject_payment(request_id: str):
    with get_db() as conn:
        conn.execute("UPDATE payment_requests SET status = 'rejected' WHERE request_id = ?", (request_id,))
        conn.commit()

async def create_withdrawal(user_id: int, amount: float, address: str) -> int:
    with get_db() as conn:
        cur = conn.execute("INSERT INTO withdrawals (user_id, amount, address) VALUES (?, ?, ?) RETURNING id",
                           (user_id, amount, address))
        row = cur.fetchone()
        conn.commit()
        return row["id"] if row else None

async def get_withdrawal(withdrawal_id: int):
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))
        row = cur.fetchone()
        return dict(row) if row else None

async def update_withdrawal_status(withdrawal_id: int, status: str):
    with get_db() as conn:
        conn.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, withdrawal_id))
        conn.commit()

# -------------------- GLOBAL GAME STATE --------------------
# For simplicity, we keep in memory; for production, store in DB.
current_round = 1
game_active = False
drawn_numbers = []
players_cards = {}  # user_id -> list of cards (each card is list of 15 numbers)
pool_amount = 0
line_winner = None
full_winner = None

# -------------------- COMMAND HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id, update.effective_user.username)
    await update.message.reply_text(
        f"🎉 Welcome to Bingo\n"
        f"💰 Balance: {user['balance']:.2f}\n\n"
        f"🎟 Card Price: {CARD_PRICE}\n"
        f"Use /buy to buy a card\n"
        f"Use /deposit to add funds\n"
        f"Use /withdraw to withdraw"
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pool_amount, players_cards

    user_id = update.effective_user.id
    user = await get_user(user_id)

    if user["balance"] < CARD_PRICE:
        await update.message.reply_text("❌ Not enough balance. Use /deposit")
        return

    await update_balance(user_id, -CARD_PRICE, "buy", "Purchased bingo card")
    pool_amount += CARD_PRICE

    # Generate a random card: 15 unique numbers from 1-90
    card = sorted(random.sample(range(1, 91), 15))

    if user_id not in players_cards:
        players_cards[user_id] = []
    players_cards[user_id].append(card)

    # Format card for display (grid 3x5)
    rows = [card[i:i+5] for i in range(0, 15, 5)]
    card_text = "\n".join(" ".join(f"{num:2d}" for num in row) for row in rows)

    await update.message.reply_text(
        f"✅ Card Purchased!\n"
        f"```\n{card_text}\n```",
        parse_mode="Markdown"
    )

# -------------------- DEPOSIT FLOW --------------------

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(m["name"], callback_data=f"pay_{key}") for key, m in PAYMENT_METHODS.items()],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_deposit")]
    ]
    await update.message.reply_text(
        "💰 **Deposit Menu**\nChoose your payment method:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return AMOUNT

async def deposit_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_deposit":
        await query.edit_message_text("❌ Deposit cancelled.")
        return ConversationHandler.END

    method = query.data.replace("pay_", "")
    context.user_data["method"] = method
    method_info = PAYMENT_METHODS[method]

    await query.edit_message_text(
        f"💳 **{method_info['name']}**\n"
        f"Account: `{method_info['account']}`\n\n"
        f"📝 **Please enter the amount** (10–1000 ETB):",
        parse_mode="Markdown"
    )
    return AMOUNT

async def deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount < 10 or amount > 1000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number between 10 and 1000:")
        return AMOUNT

    context.user_data["amount"] = amount
    method = context.user_data["method"]
    request_id = await create_payment_request(update.effective_user.id, amount, method)
    context.user_data["request_id"] = request_id

    await update.message.reply_text(
        f"📝 **Request ID:** `{request_id}`\n"
        f"Send the payment reference number you received:"
    )
    return REFERENCE

async def deposit_reference(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reference = update.message.text.strip()
    request_id = context.user_data["request_id"]
    amount = context.user_data["amount"]
    method = context.user_data["method"]

    await add_payment_proof(request_id, reference)

    await update.message.reply_text(
        "✅ Deposit request submitted!\n"
        "Admin will verify it shortly."
    )

    # Notify admin
    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_payment_{request_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject_payment_{request_id}")
    ]]
    await context.bot.send_message(
        ADMIN_ID,
        f"💰 **New Deposit Request**\n"
        f"User: {update.effective_user.id}\n"
        f"Amount: {amount} ETB\n"
        f"Method: {PAYMENT_METHODS[method]['name']}\n"
        f"Ref: {reference}\n"
        f"Request ID: `{request_id}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    context.user_data.clear()
    return ConversationHandler.END

async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Deposit cancelled.")
    return ConversationHandler.END

# -------------------- WITHDRAWAL FLOW --------------------

async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    if user["balance"] <= 0:
        await update.message.reply_text("❌ You have no balance to withdraw.")
        return ConversationHandler.END

    await update.message.reply_text("💰 Enter the amount to withdraw:")
    return WITHDRAW_AMOUNT

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a positive number:")
        return WITHDRAW_AMOUNT

    user = await get_user(update.effective_user.id)
    if amount > user["balance"]:
        await update.message.reply_text(f"❌ Insufficient balance. Your balance is {user['balance']:.2f}.")
        return WITHDRAW_AMOUNT

    context.user_data["withdraw_amount"] = amount
    await update.message.reply_text("📤 Enter your wallet address or payment details:")
    return WITHDRAW_ADDRESS

async def withdraw_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip()
    amount = context.user_data["withdraw_amount"]
    user_id = update.effective_user.id

    # Deduct balance immediately (or hold? we deduct now and refund if rejected)
    await update_balance(user_id, -amount, "withdrawal_hold", f"Withdrawal request of {amount}")

    withdrawal_id = await create_withdrawal(user_id, amount, address)

    await update.message.reply_text(
        "✅ Withdrawal request submitted!\n"
        "Admin will process it shortly."
    )

    # Notify admin
    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_withdraw_{withdrawal_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject_withdraw_{withdrawal_id}")
    ]]
    await context.bot.send_message(
        ADMIN_ID,
        f"💸 **New Withdrawal Request**\n"
        f"User: {user_id}\n"
        f"Amount: {amount} ETB\n"
        f"Address: {address}\n"
        f"ID: `{withdrawal_id}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    context.user_data.clear()
    return ConversationHandler.END

async def withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Withdrawal cancelled.")
    return ConversationHandler.END

# -------------------- ADMIN CALLBACKS --------------------

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ Unauthorized")
        return

    data = query.data

    # Payment approval
    if data.startswith("approve_payment_"):
        request_id = data.replace("approve_payment_", "")
        req = await get_payment_request(request_id)
        if not req:
            await query.edit_message_text("❌ Request not found")
            return
        await approve_payment(request_id)
        await update_balance(req["user_id"], req["amount"], "deposit", f"Deposit approved {request_id}")
        await query.edit_message_text(f"✅ Deposit approved for {req['amount']} ETB")
        await context.bot.send_message(
            req["user_id"],
            f"✅ Your deposit of {req['amount']} ETB has been approved!"
        )

    elif data.startswith("reject_payment_"):
        request_id = data.replace("reject_payment_", "")
        req = await get_payment_request(request_id)
        if not req:
            await query.edit_message_text("❌ Request not found")
            return
        await reject_payment(request_id)
        await query.edit_message_text("❌ Deposit rejected")
        await context.bot.send_message(
            req["user_id"],
            "❌ Your deposit was rejected. Please contact admin."
        )

    # Withdrawal approval
    elif data.startswith("approve_withdraw_"):
        withdrawal_id = int(data.replace("approve_withdraw_", ""))
        wd = await get_withdrawal(withdrawal_id)
        if not wd:
            await query.edit_message_text("❌ Withdrawal not found")
            return
        await update_withdrawal_status(withdrawal_id, "approved")
        await query.edit_message_text(f"✅ Withdrawal approved for {wd['amount']} ETB")
        await context.bot.send_message(
            wd["user_id"],
            f"✅ Your withdrawal of {wd['amount']} ETB has been approved and will be sent to {wd['address']}."
        )

    elif data.startswith("reject_withdraw_"):
        withdrawal_id = int(data.replace("reject_withdraw_", ""))
        wd = await get_withdrawal(withdrawal_id)
        if not wd:
            await query.edit_message_text("❌ Withdrawal not found")
            return
        # Refund the held amount
        await update_balance(wd["user_id"], wd["amount"], "withdrawal_refund", f"Withdrawal {withdrawal_id} rejected")
        await update_withdrawal_status(withdrawal_id, "rejected")
        await query.edit_message_text("❌ Withdrawal rejected")
        await context.bot.send_message(
            wd["user_id"],
            f"❌ Your withdrawal of {wd['amount']} ETB was rejected. Funds returned."
        )

# -------------------- GAME LOGIC --------------------

async def auto_draw(context: ContextTypes.DEFAULT_TYPE):
    global drawn_numbers, line_winner, full_winner, game_active, pool_amount

    if not game_active:
        return

    if len(drawn_numbers) >= 90:
        await end_round(context)
        return

    # Pick a number not yet drawn
    available = [n for n in range(1, 91) if n not in drawn_numbers]
    number = random.choice(available)
    drawn_numbers.append(number)

    # Notify admin (optional: you could also broadcast to players)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🎱 Number Drawn: {number}"
    )

    await check_winners(context)

async def check_winners(context: ContextTypes.DEFAULT_TYPE):
    global line_winner, full_winner, game_active, pool_amount

    for user_id, cards in players_cards.items():
        for card in cards:
            matched = [n for n in card if n in drawn_numbers]

            # Line win (5 numbers)
            if len(matched) >= 5 and not line_winner:
                line_winner = user_id
                prize = pool_amount * 0.3
                await update_balance(user_id, prize, "line_win", "Line win prize")
                await context.bot.send_message(user_id, f"🏆 LINE WIN! You won {prize:.2f} ETB")

            # Full house (15 numbers)
            if len(matched) == 15 and not full_winner:
                full_winner = user_id
                # 70% of pool goes to full house, minus house cut
                full_prize = pool_amount * 0.7 * (1 - HOUSE_PERCENT / 100)
                house_cut = pool_amount * 0.7 * (HOUSE_PERCENT / 100)

                await update_balance(user_id, full_prize, "full_house", "Full house win")
                await context.bot.send_message(user_id, f"🎉 FULL HOUSE! You won {full_prize:.2f} ETB")

                await context.bot.send_message(
                    ADMIN_ID,
                    f"💰 House earned: {house_cut:.2f} ETB"
                )

                game_active = False
                await end_round(context)
                return

async def end_round(context: ContextTypes.DEFAULT_TYPE):
    global current_round, drawn_numbers, players_cards, pool_amount, game_active, line_winner, full_winner

    await context.bot.send_message(
        ADMIN_ID,
        f"🔄 Round {current_round} ended."
    )

    # Reset for next round
    current_round += 1
    drawn_numbers = []
    players_cards = {}
    pool_amount = 0
    line_winner = None
    full_winner = None
    game_active = True  # auto-start next round

    await context.bot.send_message(
        ADMIN_ID,
        f"🚀 Round {current_round} started."
    )

async def auto_start(context: ContextTypes.DEFAULT_TYPE):
    global game_active
    if not game_active:
        game_active = True
        await context.bot.send_message(ADMIN_ID, "🚀 Game started!")

# -------------------- MAIN --------------------

async def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    # Conversation handlers
    deposit_conv = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_start)],
        states={
            AMOUNT: [
                CallbackQueryHandler(deposit_method, pattern="^(pay_|cancel_)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_amount)
            ],
            REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_reference)],
        },
        fallbacks=[CommandHandler("cancel", deposit_cancel)],
    )

    withdraw_conv = ConversationHandler(
        entry_points=[CommandHandler("withdraw", withdraw_start)],
        states={
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
            WITHDRAW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_address)],
        },
        fallbacks=[CommandHandler("cancel", withdraw_cancel)],
    )

    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(deposit_conv)
    app.add_handler(withdraw_conv)
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(approve_|reject_)"))

    # Job queue for game automation
    job_queue = app.job_queue
    job_queue.run_repeating(auto_draw, interval=DRAW_INTERVAL, first=10)
    job_queue.run_once(auto_start, when=5)

    print("Bot is running...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())