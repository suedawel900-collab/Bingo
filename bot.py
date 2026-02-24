import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

from models import Database

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
db = Database()

# Conversation states
AMOUNT = 1
WITHDRAW_AMOUNT = 2
WITHDRAW_ADDRESS = 3

# Bot token and URLs
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_USERNAME = os.getenv('BOT_USERNAME', 'your_bot')
BASE_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'bingo-production-a078.up.railway.app')
WEBAPP_URL = os.getenv('WEBAPP_URL', f"https://{BASE_URL}")
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')

# Check for required environment variables
if not BOT_TOKEN:
    logger.error("❌ CRITICAL: BOT_TOKEN environment variable is not set!")
    logger.error("Please add BOT_TOKEN to your Railway environment variables")

if not ADMIN_USER_ID:
    logger.warning("⚠️ ADMIN_USER_ID not set. Admin features will be disabled.")

logger.info(f"✅ Using BASE_URL: {BASE_URL}")
logger.info(f"✅ Admin ID: {ADMIN_USER_ID if ADMIN_USER_ID else 'Not set'}")

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send notification to admin"""
    if not ADMIN_USER_ID:
        logger.warning("ADMIN_USER_ID not set, skipping admin notification")
        return
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"🔔 **Admin Alert**\n\n{message}",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

async def notify_admin_withdrawal(user_id: int, amount: int, address: str, context: ContextTypes.DEFAULT_TYPE):
    """Notify admin about withdrawal request"""
    if not ADMIN_USER_ID:
        return
    
    try:
        # Get user info
        user = await context.bot.get_chat(user_id)
        username = user.username or f"User {user_id}"
        
        # Create approval buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}_{amount}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}_{amount}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"💰 **New Withdrawal Request**\n\n"
            f"**User:** {username} (ID: `{user_id}`)\n"
            f"**Amount:** `${amount/100:.2f}`\n"
            f"**Destination:** `{address}`\n"
            f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to notify admin about withdrawal: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    
    # Create or update user
    db.create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # Check if phone number exists
    user_data = db.get_user(user.id)
    if not user_data or not user_data['phone_number']:
        contact_button = KeyboardButton("📱 Share Phone Number", request_contact=True)
        reply_markup = ReplyKeyboardMarkup(
            [[contact_button]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await update.message.reply_text(
            "👋 Welcome to Bingo Bot!\n\n"
            "Please share your phone number to continue:",
            reply_markup=reply_markup
        )
        return
    
    await show_main_menu(update, context)

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shared contact"""
    contact = update.message.contact
    user = update.effective_user
    
    db.create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        phone_number=contact.phone_number
    )
    
    await update.message.reply_text(
        "✅ Phone number saved!",
        reply_markup=ReplyKeyboardRemove()
    )
    
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu"""
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        user_data = {'balance': 0, 'games_played': 0, 'games_won': 0}
    
    keyboard = [
        [InlineKeyboardButton("🎮 Play Bingo", callback_data="play")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw"),
         InlineKeyboardButton("📊 History", callback_data="history")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    
    # Add admin button if user is admin
    if str(user.id) == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    balance = user_data['balance'] / 100
    message = (
        f"🎯 Welcome, {user.first_name}!\n\n"
        f"💰 Balance: **${balance:.2f}**\n"
        f"🎮 Games: {user_data['games_played']} | 🏆 Wins: {user_data['games_won']}\n\n"
        f"Choose an option:"
    )
    
    if update.message:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show balance"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        user_data = {'balance': 0, 'total_deposits': 0, 'total_withdrawals': 0}
    
    balance = user_data['balance'] / 100
    await query.edit_message_text(
        f"💰 **Your Balance**\n\n"
        f"Current: **${balance:.2f}**\n"
        f"Total Deposits: **${user_data.get('total_deposits', 0)/100:.2f}**\n"
        f"Total Withdrawals: **${user_data.get('total_withdrawals', 0)/100:.2f}**",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="main_menu")
        ]])
    )

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show deposit options"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("$10", callback_data="deposit_10"),
         InlineKeyboardButton("$20", callback_data="deposit_20"),
         InlineKeyboardButton("$50", callback_data="deposit_50")],
        [InlineKeyboardButton("$100", callback_data="deposit_100"),
         InlineKeyboardButton("$200", callback_data="deposit_200"),
         InlineKeyboardButton("Custom", callback_data="deposit_custom")],
        [InlineKeyboardButton("◀️ Back", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💳 **Deposit Funds**\n\n"
        "Select amount:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def deposit_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deposit amount selection"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "deposit_custom":
        await query.edit_message_text(
            "💰 Enter amount in USD (e.g., 25.50):",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Cancel", callback_data="deposit")
            ]])
        )
        return AMOUNT
    
    # Parse amount
    amount_map = {
        'deposit_10': 10,
        'deposit_20': 20,
        'deposit_50': 50,
        'deposit_100': 100,
        'deposit_200': 200
    }
    
    amount = amount_map.get(data, 0)
    if amount:
        await process_deposit(query, update.effective_user.id, amount)
    return ConversationHandler.END

async def process_deposit(message_obj, user_id: int, amount: int):
    """Process deposit with external payment page"""
    try:
        # Get the correct message object to edit
        if hasattr(message_obj, 'message'):  # It's a CallbackQuery
            target = message_obj.message
            logger.info(f"Processing deposit for user {user_id}, amount ${amount}")
        elif hasattr(message_obj, 'chat'):  # It's a Message/Update
            target = message_obj
            logger.info(f"Processing deposit from message for user {user_id}, amount ${amount}")
        else:
            logger.error(f"Unknown message object type: {type(message_obj)}")
            return
        
        # Create payment page URL with https://
        payment_url = f"https://{BASE_URL}/payment/page?user_id={user_id}&amount={amount}"
        logger.info(f"Payment URL: {payment_url}")
        
        keyboard = [[
            InlineKeyboardButton(
                "💳 Pay with Card", 
                url=payment_url
            )
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await target.edit_message_text(
            f"💳 **Deposit ${amount:.2f}**\n\n"
            f"Click the button below to pay securely.\n\n"
            f"⚠️ **Note:** The payment page will open in your browser.\n"
            f"Your balance will update automatically after payment.",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Deposit failed: {str(e)}")
        # Handle error gracefully - send a new message instead of editing
        try:
            if hasattr(message_obj, 'message'):  # CallbackQuery
                await message_obj.message.reply_text(
                    "❌ Payment processing failed. Please try again.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("💳 Try Again", callback_data="deposit")
                    ]])
                )
            elif hasattr(message_obj, 'chat'):  # Message
                await message_obj.reply_text(
                    "❌ Payment processing failed. Please try again.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("💳 Try Again", callback_data="deposit")
                    ]])
                )
        except Exception as inner_e:
            logger.error(f"Error in error handling: {inner_e}")

async def handle_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom deposit amount"""
    try:
        amount_text = update.message.text.strip().replace('$', '').replace(',', '')
        amount = float(amount_text)
        
        if amount < 5:
            await update.message.reply_text("❌ Minimum deposit is $5")
            return AMOUNT
        if amount > 1000:
            await update.message.reply_text("❌ Maximum deposit is $1000")
            return AMOUNT
        
        await process_deposit(update, update.effective_user.id, amount)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return AMOUNT

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start withdrawal process"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        user_data = {'balance': 0}
    
    if user_data['balance'] < 500:  # Minimum $5 withdrawal
        await query.edit_message_text(
            f"❌ Minimum withdrawal is $5.00\n"
            f"Your balance: **${user_data['balance']/100:.2f}**",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="main_menu")
            ]])
        )
        return ConversationHandler.END
    
    await query.edit_message_text(
        f"💸 **Withdrawal**\n\n"
        f"Balance: **${user_data['balance']/100:.2f}**\n\n"
        f"Enter amount to withdraw (minimum $5):",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Cancel", callback_data="main_menu")
        ]])
    )
    return WITHDRAW_AMOUNT

async def withdraw_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal amount"""
    try:
        amount_text = update.message.text.strip().replace('$', '').replace(',', '')
        amount = float(amount_text)
        
        if amount < 5:
            await update.message.reply_text("❌ Minimum withdrawal is $5")
            return WITHDRAW_AMOUNT
        
        amount_cents = int(amount * 100)
        user_data = db.get_user(update.effective_user.id)
        
        if not user_data:
            await update.message.reply_text("❌ User not found")
            return ConversationHandler.END
        
        if amount_cents > user_data['balance']:
            await update.message.reply_text(
                f"❌ Insufficient balance. Your balance: ${user_data['balance']/100:.2f}"
            )
            return WITHDRAW_AMOUNT
        
        context.user_data['withdraw_amount'] = amount_cents
        
        await update.message.reply_text(
            "📱 **Enter Withdrawal Details**\n\n"
            "Please enter your PayPal email or bank account details:"
        )
        return WITHDRAW_ADDRESS
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return WITHDRAW_AMOUNT

async def withdraw_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal address"""
    address = update.message.text
    amount = context.user_data.get('withdraw_amount', 0)
    user = update.effective_user
    
    try:
        # Create pending transaction
        result = db.update_balance(
            user_id=user.id,
            amount=-amount,
            transaction_type='withdrawal',
            description=f'Withdrawal request to {address}',
            status='pending'
        )
        
        if result:
            # Notify user
            await update.message.reply_text(
                f"✅ **Withdrawal Request Submitted**\n\n"
                f"Amount: **${amount/100:.2f}**\n"
                f"Destination: {address}\n\n"
                f"Your request has been sent to admin for approval.\n"
                f"You will be notified once processed.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")
                ]])
            )
            
            # Notify admin
            await notify_admin_withdrawal(user.id, amount, address, context)
            
        else:
            await update.message.reply_text(
                "❌ Withdrawal failed. Please try again.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")
                ]])
            )
        
    except Exception as e:
        logger.error(f"Withdrawal error: {e}")
        await update.message.reply_text(
            "❌ Withdrawal failed. Please try again.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")
            ]])
        )
    
    context.user_data.clear()
    return ConversationHandler.END

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show transaction history"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    transactions = db.get_user_transactions(user.id, limit=10)
    
    if not transactions:
        await query.edit_message_text(
            "📊 No transactions yet.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="main_menu")
            ]])
        )
        return
    
    history_text = "📊 **Recent Transactions**\n\n"
    for t in transactions:
        amount = t['amount'] / 100
        sign = "+" if t['amount'] > 0 else ""
        emoji = "💚" if t['type'] == 'deposit' else "💔" if t['type'] == 'withdrawal' else "🎮"
        date = datetime.fromisoformat(t['created_at']).strftime("%m/%d %H:%M")
        status = "✅" if t['status'] == 'completed' else "⏳"
        history_text += f"{emoji} {date}: {sign}${amount:.2f} {t['type']} {status}\n"
    
    await query.edit_message_text(
        history_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="main_menu")
        ]])
    )

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Launch Bingo game"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        user_data = {'balance': 0, 'games_played': 0, 'games_won': 0}
    
    # Game fee $2
    if user_data['balance'] < 200:
        await query.edit_message_text(
            "❌ **Insufficient Balance**\n\n"
            "You need $2.00 to play Bingo.\n\n"
            f"Your balance: **${user_data['balance']/100:.2f}**",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
                [InlineKeyboardButton("◀️ Back", callback_data="main_menu")]
            ])
        )
        return
    
    # Launch webapp
    webapp_url = f"{WEBAPP_URL}/game?user_id={user.id}&game_id={context.user_data.get('game_id', 1)}"
    logger.info(f"Webapp URL: {webapp_url}")
    
    keyboard = [[
        InlineKeyboardButton(
            "🎮 Open Bingo Game", 
            web_app={'url': webapp_url}
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🎮 **Ready to Play!**\n\n"
        "Click below to open the game.\n\n"
        f"• Game fee: $2.00\n"
        f"• Your balance: ${user_data['balance']/100:.2f}",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    query = update.callback_query
    await query.answer()
    
    help_text = (
        "❓ **Bingo Bot Help**\n\n"
        "**How to Play:**\n"
        "1. Click 'Play Bingo' to start\n"
        "2. Match numbers as they're called\n"
        "3. Click 'BINGO' when you have 5 in a row\n\n"
        "**💰 Balance:**\n"
        "• Deposit funds to play\n"
        "• Withdraw your winnings\n"
        "• Each game costs $2\n\n"
        "**💳 Payments:**\n"
        "• Secure Stripe payments\n"
        "• Instant balance updates\n"
        "• 24h withdrawal processing\n\n"
        "Need help? Contact @admin"
    )
    
    await query.edit_message_text(
        help_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="main_menu")
        ]])
    )

# ==================== ADMIN FUNCTIONS ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    query = update.callback_query
    await query.answer()
    
    # Check if user is admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ You are not authorized to access this panel.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 Pending Withdrawals", callback_data="admin_pending")],
        [InlineKeyboardButton("💰 Total Balance", callback_data="admin_total_balance")],
        [InlineKeyboardButton("👥 User Stats", callback_data="admin_user_stats")],
        [InlineKeyboardButton("📈 System Health", callback_data="admin_health")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👑 **Admin Control Panel**\n\n"
        "Welcome to the admin panel. Select an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def admin_pending_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending withdrawals"""
    query = update.callback_query
    await query.answer()
    
    # Check if admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    # Get pending withdrawals from database
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM transactions 
            WHERE type = 'withdrawal' AND status = 'pending'
            ORDER BY created_at DESC
        ''')
        pending = cursor.fetchall()
    
    if not pending:
        await query.edit_message_text(
            "📊 No pending withdrawals.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back to Admin", callback_data="admin_panel")
            ]])
        )
        return
    
    message = "📊 **Pending Withdrawals**\n\n"
    for p in pending:
        created = datetime.fromisoformat(p['created_at']).strftime("%m/%d %H:%M")
        message += f"• ID: `{p['id']}` | User: {p['user_id']}\n"
        message += f"  Amount: **${p['amount']/100:.2f}** | {created}\n\n"
    
    await query.edit_message_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back to Admin", callback_data="admin_panel")
        ]])
    )

async def admin_total_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show total balance in system"""
    query = update.callback_query
    await query.answer()
    
    # Check if admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    # Calculate totals
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Total user balances
        cursor.execute('SELECT SUM(balance) as total FROM users')
        total_balance = cursor.fetchone()['total'] or 0
        
        # Total deposits
        cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type = "deposit" AND status = "completed"')
        total_deposits = cursor.fetchone()['total'] or 0
        
        # Total withdrawals
        cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type = "withdrawal" AND status = "completed"')
        total_withdrawals = cursor.fetchone()['total'] or 0
        
        # Total users
        cursor.execute('SELECT COUNT(*) as count FROM users')
        total_users = cursor.fetchone()['count']
    
    message = (
        f"💰 **System Financials**\n\n"
        f"**Total Users:** {total_users}\n"
        f"**Total Balance:** `${total_balance/100:.2f}`\n"
        f"**Total Deposits:** `${total_deposits/100:.2f}`\n"
        f"**Total Withdrawals:** `${total_withdrawals/100:.2f}`\n"
        f"**Platform Profit:** `${(total_deposits - total_withdrawals - total_balance)/100:.2f}`"
    )
    
    await query.edit_message_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back to Admin", callback_data="admin_panel")
        ]])
    )

async def admin_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user statistics"""
    query = update.callback_query
    await query.answer()
    
    # Check if admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Top users by balance
        cursor.execute('''
            SELECT user_id, username, first_name, balance 
            FROM users 
            ORDER BY balance DESC 
            LIMIT 5
        ''')
        top_users = cursor.fetchall()
        
        # Users with most games
        cursor.execute('''
            SELECT user_id, username, first_name, games_played 
            FROM users 
            ORDER BY games_played DESC 
            LIMIT 5
        ''')
        top_players = cursor.fetchall()
    
    message = "👥 **User Statistics**\n\n"
    
    message += "**💰 Top Balances:**\n"
    for u in top_users:
        name = u['first_name'] or u['username'] or f"User {u['user_id']}"
        message += f"• {name}: **${u['balance']/100:.2f}**\n"
    
    message += "\n**🎮 Most Games Played:**\n"
    for u in top_players:
        name = u['first_name'] or u['username'] or f"User {u['user_id']}"
        message += f"• {name}: **{u['games_played']}** games\n"
    
    await query.edit_message_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back to Admin", callback_data="admin_panel")
        ]])
    )

async def admin_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system health"""
    query = update.callback_query
    await query.answer()
    
    # Check if admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    import platform
    import psutil
    
    message = (
        f"📈 **System Health**\n\n"
        f"**Bot Status:** ✅ Online\n"
        f"**Database:** ✅ Connected\n"
        f"**Python Version:** {platform.python_version()}\n"
        f"**Server Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**BASE_URL:** `{BASE_URL}`\n"
    )
    
    # Try to add memory info if available
    try:
        memory = psutil.virtual_memory()
        message += f"**Memory Usage:** {memory.percent}%\n"
    except:
        pass
    
    await query.edit_message_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back to Admin", callback_data="admin_panel"),
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_health")
        ]])
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin approval/rejection"""
    query = update.callback_query
    await query.answer()
    
    # Check if user is admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ You are not authorized to perform this action.")
        return
    
    data = query.data
    if data.startswith('approve_'):
        parts = data.split('_')
        user_id = int(parts[1])
        amount = int(parts[2])
        
        try:
            # Process approval
            result = db.update_balance(
                user_id=user_id,
                amount=-amount,
                transaction_type='withdrawal',
                description='Withdrawal approved by admin',
                status='completed'
            )
            
            await query.edit_message_text(
                f"✅ Withdrawal for user {user_id} of ${amount/100:.2f} has been approved.",
                parse_mode='Markdown'
            )
            
            # Notify user
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Your withdrawal of **${amount/100:.2f}** has been approved and processed!",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error approving withdrawal: {e}")
            await query.edit_message_text(
                f"❌ Error approving withdrawal: {str(e)}",
                parse_mode='Markdown'
            )
        
    elif data.startswith('reject_'):
        parts = data.split('_')
        user_id = int(parts[1])
        amount = int(parts[2])
        
        try:
            # Refund the amount back to user
            db.update_balance(
                user_id=user_id,
                amount=amount,
                transaction_type='refund',
                description='Withdrawal rejected, funds returned',
                status='completed'
            )
            
            await query.edit_message_text(
                f"❌ Withdrawal for user {user_id} of ${amount/100:.2f} has been rejected.",
                parse_mode='Markdown'
            )
            
            # Notify user
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Your withdrawal of **${amount/100:.2f}** was rejected. Funds have been returned to your balance.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error rejecting withdrawal: {e}")
            await query.edit_message_text(
                f"❌ Error rejecting withdrawal: {str(e)}",
                parse_mode='Markdown'
            )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    await update.message.reply_text(
        "Cancelled.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    
    # Send message to user
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An error occurred. Please try again."
            )
        
        # Notify admin
        if ADMIN_USER_ID:
            error_msg = f"🔴 **Bot Error**\n\n{context.error}"
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=error_msg,
                parse_mode='Markdown'
            )
    except:
        pass

def setup_handlers(application):
    """Setup all handlers for the bot"""
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Conversation handlers with per_message=False to fix warning
    deposit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(deposit_amount_handler, pattern='^deposit_')],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_amount)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
        name="deposit_conversation"
    )
    
    withdraw_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(withdraw_command, pattern='^withdraw$')],
        states={
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount_handler)],
            WITHDRAW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_address_handler)]
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(show_main_menu, pattern='^main_menu$')],
        per_message=False,
        name="withdraw_conversation"
    )
    
    # Add handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern='^main_menu$'))
    application.add_handler(CallbackQueryHandler(balance_command, pattern='^balance$'))
    application.add_handler(CallbackQueryHandler(deposit_command, pattern='^deposit$'))
    application.add_handler(CallbackQueryHandler(history_command, pattern='^history$'))
    application.add_handler(CallbackQueryHandler(play_command, pattern='^play$'))
    application.add_handler(CallbackQueryHandler(help_command, pattern='^help$'))
    
    # Admin handlers
    application.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_pending_handler, pattern='^admin_pending$'))
    application.add_handler(CallbackQueryHandler(admin_total_balance, pattern='^admin_total_balance$'))
    application.add_handler(CallbackQueryHandler(admin_user_stats, pattern='^admin_user_stats$'))
    application.add_handler(CallbackQueryHandler(admin_health, pattern='^admin_health$'))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern='^(approve|reject)_'))
    
    # Add conversation handlers
    application.add_handler(deposit_conv)
    application.add_handler(withdraw_conv)
    
    return application

def main():
    """Start the bot"""
    try:
        # Get token with validation
        BOT_TOKEN = os.getenv('BOT_TOKEN')
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN not found in environment variables")
            logger.error("Please set BOT_TOKEN in Railway dashboard")
            return
        
        logger.info("🤖 Initializing bot...")
        
        # Create application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Setup all handlers
        application = setup_handlers(application)
        
        logger.info("✅ Bot initialized successfully!")
        logger.info(f"🚀 Starting bot polling with token: {BOT_TOKEN[:10]}...")
        logger.info(f"🌐 Using BASE_URL: {BASE_URL}")
        if ADMIN_USER_ID:
            logger.info(f"👑 Admin ID: {ADMIN_USER_ID}")
        
        # Start bot
        application.run_polling()
        
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        raise

if __name__ == '__main__':
    main()