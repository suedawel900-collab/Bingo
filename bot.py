import os
import logging
import asyncio
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
WITHDRAW_NAME = 4
PAYMENT_PHONE = 5
PAYMENT_REFERENCE = 6

# Bot token and URLs
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_USERNAME = os.getenv('BOT_USERNAME', 'your_bot')
BASE_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'bingo-production-a078.up.railway.app')
WEBAPP_URL = os.getenv('WEBAPP_URL', f"https://{BASE_URL}")
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')

# Global shutdown flag for async_main
shutdown_flag = False

# Check for required environment variables
if not BOT_TOKEN:
    logger.error("❌ CRITICAL: BOT_TOKEN environment variable is not set!")
    logger.error("Please add BOT_TOKEN to your Railway environment variables")

if not ADMIN_USER_ID:
    logger.warning("⚠️ ADMIN_USER_ID not set. Admin features will be disabled.")

logger.info(f"✅ Using BASE_URL: {BASE_URL}")
logger.info(f"✅ Admin ID: {ADMIN_USER_ID if ADMIN_USER_ID else 'Not set'}")

def get_method_emoji(method_type):
    """Get emoji for payment method type"""
    emojis = {
        'mobile_money': '📱',
        'bank': '🏦',
        'manual': '💵',
        'card': '💳'
    }
    return emojis.get(method_type, '💰')

def set_shutdown_flag():
    """Set shutdown flag for async_main"""
    global shutdown_flag
    shutdown_flag = True

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

async def notify_admin_payment_request(request_id, user_id, amount, method_name, context):
    """Notify admin about new payment request"""
    if not ADMIN_USER_ID:
        return
    
    try:
        message = (
            f"🆕 **New Payment Request**\n\n"
            f"**Request ID:** `{request_id}`\n"
            f"**User ID:** `{user_id}`\n"
            f"**Amount:** {amount:.2f} ETB\n"
            f"**Method:** {method_name}\n"
            f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=message,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

async def notify_admin_withdrawal_request(request_id, user_id, amount, method, account, account_name, context):
    """Notify admin about withdrawal request"""
    if not ADMIN_USER_ID:
        return
    
    try:
        message = (
            f"🆕 **New Withdrawal Request**\n\n"
            f"**Request ID:** `{request_id}`\n"
            f"**User ID:** `{user_id}`\n"
            f"**Amount:** {amount:.2f} ETB\n"
            f"**Method:** {method}\n"
            f"**Account:** {account}\n"
            f"**Account Name:** {account_name}\n"
            f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=message,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    
    # Create or update user
    db.create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        country='ET',
        currency='ETB'
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
    
    # Store phone in context for later use
    context.user_data['phone_number'] = contact.phone_number
    
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
        user_data = {'balance': 0, 'games_played': 0, 'games_won': 0, 'currency': 'ETB'}
    
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
    currency = user_data['currency'] if 'currency' in user_data.keys() else 'ETB'
    games_played = user_data['games_played'] if 'games_played' in user_data.keys() else 0
    games_won = user_data['games_won'] if 'games_won' in user_data.keys() else 0
    
    message = (
        f"🎯 Welcome, {user.first_name}!\n\n"
        f"💰 Balance: **{balance:.2f} {currency}**\n"
        f"🎮 Games: {games_played} | 🏆 Wins: {games_won}\n\n"
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
        user_data = {'balance': 0, 'total_deposits': 0, 'total_withdrawals': 0, 'currency': 'ETB'}
    
    balance = user_data['balance'] / 100
    currency = user_data['currency'] if 'currency' in user_data.keys() else 'ETB'
    total_deposits = user_data['total_deposits'] if 'total_deposits' in user_data.keys() else 0
    total_withdrawals = user_data['total_withdrawals'] if 'total_withdrawals' in user_data.keys() else 0
    
    await query.edit_message_text(
        f"💰 **Your Balance**\n\n"
        f"Current: **{balance:.2f} {currency}**\n"
        f"Total Deposits: **{total_deposits/100:.2f} {currency}**\n"
        f"Total Withdrawals: **{total_withdrawals/100:.2f} {currency}**",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Back", callback_data="main_menu")
        ]])
    )

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show deposit options with only Telbirr and CBE Birr"""
    query = update.callback_query
    await query.answer()
    
    # Get only mobile money payment methods (Telbirr and CBE Birr)
    methods = db.get_payment_methods(type='mobile_money', active_only=True)
    
    keyboard = []
    
    # Add mobile money section
    if methods and len(methods) > 0:
        for m in methods:
            min_amt = m['min_amount'] / 100
            max_amt = m['max_amount'] / 100
            method_name = m['method_name'] if 'method_name' in m.keys() else 'Mobile Money'
            
            # Use specific emoji for each
            if 'CBE' in method_name or 'ሲቢኢ' in method_name:
                emoji = "💚"
            else:
                emoji = "🔵"
                
            keyboard.append([InlineKeyboardButton(
                f"{emoji} {method_name} ({min_amt:.0f}-{max_amt:.0f} ETB)", 
                callback_data=f"deposit_method_{m['id']}"
            )])
    
    keyboard.append([InlineKeyboardButton("◀️ ተመለስ (Back)", callback_data="main_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💳 **የክፍያ ዘዴ ይምረጡ / Choose Payment Method**\n\n"
        "እባክዎ የሚፈልጉትን የክፍያ ዘዴ ይምረጡ:\n"
        "Please select your preferred payment method:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def deposit_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment method selection"""
    query = update.callback_query
    await query.answer()
    
    method_id = int(query.data.split('_')[2])
    context.user_data['payment_method_id'] = method_id
    
    # Get method details
    method = db.get_payment_method(method_id)
    
    if not method:
        await query.edit_message_text("❌ Invalid payment method")
        return ConversationHandler.END
    
    # Show amount input for mobile money
    min_amt = method['min_amount'] / 100
    max_amt = method['max_amount'] / 100
    method_name = method['method_name'] if 'method_name' in method.keys() else 'Payment'
    
    # Set appropriate emoji
    if 'CBE' in method_name or 'ሲቢኢ' in method_name:
        emoji = "💚"
    else:
        emoji = "🔵"
    
    await query.edit_message_text(
        f"{emoji} **{method_name}**\n\n"
        f"ዝቅተኛ: {min_amt:.0f} ETB\n"
        f"ከፍተኛ: {max_amt:.0f} ETB\n\n"
        f"💰 እባክዎ መጠኑን ያስገቡ (ETB):\n"
        f"Please enter amount in ETB:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ተመለስ (Back)", callback_data="deposit")
        ]])
    )
    return AMOUNT

async def handle_ethiopian_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deposit amount for Ethiopian payment methods"""
    try:
        amount_text = update.message.text.strip().replace('$', '').replace(',', '')
        amount = float(amount_text)
        
        method_id = context.user_data.get('payment_method_id')
        method = db.get_payment_method(method_id)
        
        if not method:
            await update.message.reply_text("❌ Invalid payment method")
            return ConversationHandler.END
        
        # Convert to cents
        amount_cents = int(amount * 100)
        
        # Validate amount
        if amount_cents < method['min_amount']:
            await update.message.reply_text(f"❌ Minimum amount is {method['min_amount']/100:.0f} ETB")
            return AMOUNT
        if amount_cents > method['max_amount']:
            await update.message.reply_text(f"❌ Maximum amount is {method['max_amount']/100:.0f} ETB")
            return AMOUNT
        
        # Get user's phone number
        user_data = db.get_user(update.effective_user.id)
        phone = user_data['phone_number'] if user_data and 'phone_number' in user_data.keys() and user_data['phone_number'] else context.user_data.get('phone_number')
        
        if not phone:
            # Ask for phone number for mobile money
            context.user_data['pending_amount'] = amount_cents
            context.user_data['pending_method_id'] = method_id
            await update.message.reply_text(
                "📱 **እባክዎ ስልክ ቁጥርዎን ያስገቡ**\n\n"
                "Please enter your phone number (09xxxxxxxx):",
                parse_mode='Markdown'
            )
            return PAYMENT_PHONE
        
        # Create payment request
        await create_payment_request(update, context, method_id, amount_cents, phone)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return AMOUNT

async def handle_payment_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number input for payment"""
    phone = update.message.text.strip()
    
    # Basic phone validation for Ethiopia
    if not phone.startswith('09') or len(phone) != 10:
        await update.message.reply_text(
            "❌ እባክዎ ትክክለኛ የስልክ ቁጥር ያስገቡ (09xxxxxxxx)\n"
            "Please enter a valid phone number (09xxxxxxxx)"
        )
        return PAYMENT_PHONE
    
    amount_cents = context.user_data.get('pending_amount')
    method_id = context.user_data.get('pending_method_id')
    
    if not amount_cents or not method_id:
        await update.message.reply_text("❌ Session expired. Please start over.")
        return ConversationHandler.END
    
    await create_payment_request(update, context, method_id, amount_cents, phone)
    return ConversationHandler.END

async def create_payment_request(update_obj, context, method_id, amount_cents, phone=None):
    """Create payment request and show instructions"""
    method = db.get_payment_method(method_id)
    user = update_obj.effective_user
    
    if not method:
        await update_obj.message.reply_text("❌ Payment method not found")
        return
    
    # Create payment request
    request_id = db.create_payment_request(
        user_id=user.id,
        method_id=method_id,
        amount=amount_cents,
        sender_phone=phone
    )
    
    if not request_id:
        await update_obj.message.reply_text("❌ Failed to create payment request")
        return
    
    # Get primary account
    account = db.get_primary_account(method_id)
    
    # Get method name safely
    method_name = method['method_name'] if 'method_name' in method.keys() else "Payment"
    
    # Set appropriate emoji
    if 'CBE' in method_name or 'ሲቢኢ' in method_name:
        emoji = "💚"
    else:
        emoji = "🔵"
    
    # Get account number
    account_number = "0953933030"  # Default
    if account and 'account_number' in account.keys():
        account_number = account['account_number']
    
    # Customize instructions based on method
    if 'CBE' in method_name or 'ሲቢኢ' in method_name:
        custom_instructions = (
            f"**የ CBE ቢር ክፍያ መመሪያ / CBE Birr Payment Instructions**\n\n"
            f"1. ወደ CBE ቢር ሜኑ ለመግባት *847# ይደውሉ\n"
            f"2. \"ገንዘብ ላክ\" የሚለውን ይምረጡ\n"
            f"3. ቁጥር **{account_number}** ያስገቡ\n"
            f"4. {amount_cents/100:.0f} ETB ያስገቡ\n"
            f"5. ፒንዎን ያስገቡ\n"
            f"6. ከተጠናቀቀ በኋላ የግብይት መለያ ቁጥር (transaction ID) ያስቀምጡ\n\n"
            f"📱 **CBE Birr Instructions**\n"
            f"1. Dial *847# to access CBE Birr menu\n"
            f"2. Select \"Send Money\"\n"
            f"3. Enter number **{account_number}**\n"
            f"4. Enter {amount_cents/100:.0f} ETB\n"
            f"5. Enter your PIN\n"
            f"6. Save the transaction ID"
        )
    else:
        custom_instructions = (
            f"**የቴሌቢር ክፍያ መመሪያ / Telbirr Payment Instructions**\n\n"
            f"1. ወደ ቴሌቢር ሜኑ ለመግባት *127# ይደውሉ\n"
            f"2. \"ገንዘብ ላክ\" የሚለውን ይምረጡ\n"
            f"3. ቁጥር **{account_number}** ያስገቡ\n"
            f"4. {amount_cents/100:.0f} ETB ያስገቡ\n"
            f"5. ፒንዎን ያስገቡ\n"
            f"6. ከተጠናቀቀ በኋላ የደረሰኝ ቁጥር (reference number) ያስቀምጡ\n\n"
            f"📱 **Telbirr Instructions**\n"
            f"1. Dial *127# to access Telbirr menu\n"
            f"2. Select \"Send Money\"\n"
            f"3. Enter number **{account_number}**\n"
            f"4. Enter {amount_cents/100:.0f} ETB\n"
            f"5. Enter your PIN\n"
            f"6. Save the reference number"
        )
    
    message = (
        f"{emoji} **{method_name}**\n\n"
        f"💰 **መጠን / Amount:** {amount_cents/100:.0f} ETB\n"
        f"🆔 **የክፍያ መለያ / Request ID:** `{request_id}`\n\n"
        f"**📋 መመሪያ / Instructions:**\n{custom_instructions}\n\n"
        f"📱 **ከተከፈለ በኋላ የክፍያ ማረጋገጫ ቁጥር ይላኩ / After payment, send the transaction reference:**"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ ከፍያዬን አረጋገጥኩ / I've Made Payment", callback_data=f"payment_done_{request_id}")],
        [InlineKeyboardButton("◀️ ተመለስ / Cancel", callback_data="deposit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_obj.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    # Clear pending data
    context.user_data.pop('pending_amount', None)
    context.user_data.pop('pending_method_id', None)
    
    # Notify admin
    await notify_admin_payment_request(request_id, user.id, amount_cents/100, method_name, context)

async def payment_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment completion callback"""
    query = update.callback_query
    await query.answer()
    
    request_id = query.data.split('_')[2]
    
    # Get payment request
    request = db.get_payment_request(request_id)
    
    if not request:
        await query.edit_message_text("❌ Payment request not found")
        return
    
    # Ask for transaction reference
    context.user_data['pending_request_id'] = request_id
    
    await query.edit_message_text(
        f"✅ **ክፍያ ሪፖርት ተደርጓል / Payment Report Received**\n\n"
        f"እባክዎ የክፍያ ማረጋገጫ ቁጥርዎን ያስገቡ።\n"
        f"Please enter your transaction reference number:\n\n"
        f"ለምሳሌ / Example: `TRX123456`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ተመለስ / Cancel", callback_data="deposit")
        ]])
    )
    return PAYMENT_REFERENCE

async def handle_payment_reference(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment reference input"""
    reference = update.message.text.strip()
    request_id = context.user_data.get('pending_request_id')
    
    if not request_id:
        await update.message.reply_text("❌ Session expired. Please start over.")
        return ConversationHandler.END
    
    # Add payment proof
    db.add_payment_proof(
        request_id=request_id,
        proof_type='text',
        proof_data=reference
    )
    
    await update.message.reply_text(
        f"✅ **ክፍያ ሪፖርት ተልኳል / Payment Reported Successfully!**\n\n"
        f"የክፍያ ማረጋገጫ ቁጥርዎ: `{reference}`\n"
        f"Your reference: `{reference}`\n\n"
        f"⏳ አስተዳዳሪ ክፍያዎን በቅርቡ ያረጋግጣል።\n"
        f"Admin will verify your payment shortly.\n"
        f"ቀሪ ሂሳብዎ ሲሞላ ይነገርዎታል።\n"
        f"You'll be notified once your balance is updated.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ ዋና መደብ / Main Menu", callback_data="main_menu")
        ]])
    )
    
    # Clear pending data
    context.user_data.pop('pending_request_id', None)
    
    # Notify admin
    if ADMIN_USER_ID:
        user = update.effective_user
        message = (
            f"💰 **Payment Reported**\n\n"
            f"**Request ID:** `{request_id}`\n"
            f"**User:** {user.first_name} (ID: `{user.id}`)\n"
            f"**Reference:** `{reference}`\n\n"
            f"Use the admin panel to approve/reject."
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=message,
            parse_mode='Markdown'
        )
    
    return ConversationHandler.END

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start withdrawal process - simplified for mobile money only"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        user_data = {'balance': 0, 'currency': 'ETB'}
    
    currency = user_data['currency'] if 'currency' in user_data.keys() else 'ETB'
    
    if user_data['balance'] < 500:  # Minimum 5 ETB withdrawal
        await query.edit_message_text(
            f"❌ Minimum withdrawal is 5.00 {currency}\n"
            f"Your balance: **{user_data['balance']/100:.2f} {currency}**",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="main_menu")
            ]])
        )
        return ConversationHandler.END
    
    # Get only mobile money withdrawal methods
    methods = db.get_payment_methods(type='mobile_money', active_only=True)
    
    keyboard = []
    for method in methods:
        method_name = method['method_name'] if 'method_name' in method.keys() else 'Mobile Money'
        
        # Use specific emoji for each
        if 'CBE' in method_name or 'ሲቢኢ' in method_name:
            emoji = "💚"
        else:
            emoji = "🔵"
            
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {method_name}", 
            callback_data=f"withdraw_method_{method['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("◀️ Cancel", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"💸 **Withdrawal**\n\n"
        f"Balance: **{user_data['balance']/100:.2f} {currency}**\n\n"
        f"Select withdrawal method:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    return WITHDRAW_AMOUNT

async def withdraw_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal method selection"""
    query = update.callback_query
    await query.answer()
    
    method_id = int(query.data.split('_')[2])
    context.user_data['withdraw_method_id'] = method_id
    
    method = db.get_payment_method(method_id)
    
    if not method:
        await query.edit_message_text("❌ Invalid withdrawal method")
        return ConversationHandler.END
    
    min_amt = method['min_amount'] / 100 if method['min_amount'] else 5
    method_name = method['method_name'] if 'method_name' in method.keys() else 'Withdrawal'
    
    await query.edit_message_text(
        f"💸 **{method_name} Withdrawal**\n\n"
        f"Minimum: {min_amt:.0f} ETB\n\n"
        f"Enter amount to withdraw:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Cancel", callback_data="withdraw")
        ]])
    )
    return WITHDRAW_AMOUNT

async def withdraw_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal amount"""
    try:
        amount_text = update.message.text.strip().replace('$', '').replace(',', '')
        amount = float(amount_text)
        
        method_id = context.user_data.get('withdraw_method_id')
        method = db.get_payment_method(method_id)
        
        if not method:
            await update.message.reply_text("❌ Invalid withdrawal method")
            return ConversationHandler.END
        
        min_amount = method['min_amount'] / 100 if method['min_amount'] else 5
        if amount < min_amount:
            await update.message.reply_text(f"❌ Minimum withdrawal is {min_amount:.0f} ETB")
            return WITHDRAW_AMOUNT
        
        amount_cents = int(amount * 100)
        user_data = db.get_user(update.effective_user.id)
        
        if not user_data:
            await update.message.reply_text("❌ User not found")
            return ConversationHandler.END
        
        currency = user_data['currency'] if 'currency' in user_data.keys() else 'ETB'
        
        if amount_cents > user_data['balance']:
            await update.message.reply_text(
                f"❌ Insufficient balance. Your balance: {user_data['balance']/100:.2f} {currency}"
            )
            return WITHDRAW_AMOUNT
        
        context.user_data['withdraw_amount'] = amount_cents
        
        # Ask for phone number
        await update.message.reply_text(
            "📱 **Enter Withdrawal Details**\n\n"
            "Please enter your mobile money phone number (09xxxxxxxx):"
        )
        return WITHDRAW_ADDRESS
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return WITHDRAW_AMOUNT

async def withdraw_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal address (phone number)"""
    phone = update.message.text.strip()
    
    # Validate phone number
    if not phone.startswith('09') or len(phone) != 10:
        await update.message.reply_text(
            "❌ Please enter a valid phone number (09xxxxxxxx)"
        )
        return WITHDRAW_ADDRESS
    
    context.user_data['withdraw_phone'] = phone
    
    # Ask for account name
    await update.message.reply_text(
        "📝 **Enter Account Name**\n\n"
        "Please enter the account holder name:"
    )
    return WITHDRAW_NAME

async def withdraw_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle account name for withdrawal"""
    account_name = update.message.text.strip()
    amount = context.user_data.get('withdraw_amount', 0)
    method_id = context.user_data.get('withdraw_method_id')
    phone = context.user_data.get('withdraw_phone')
    user = update.effective_user
    
    method = db.get_payment_method(method_id)
    
    if not method:
        await update.message.reply_text("❌ Invalid withdrawal method")
        return ConversationHandler.END
    
    try:
        # Create withdrawal request
        request_id = db.create_withdrawal_request(
            user_id=user.id,
            method_id=method_id,
            amount=amount,
            account_number=phone,
            account_name=account_name,
            phone_number=phone
        )
        
        if request_id:
            method_name = method['method_name'] if 'method_name' in method.keys() else 'Withdrawal'
            await update.message.reply_text(
                f"✅ **Withdrawal Request Submitted**\n\n"
                f"Amount: **{amount/100:.2f} ETB**\n"
                f"Phone: {phone}\n"
                f"Name: {account_name}\n"
                f"Method: {method_name}\n"
                f"Request ID: `{request_id}`\n\n"
                f"Your request has been sent to admin for approval.\n"
                f"You will be notified once processed.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")
                ]])
            )
            
            # Notify admin
            await notify_admin_withdrawal_request(
                request_id, user.id, amount/100, 
                method_name, phone, account_name, context
            )
            
        else:
            await update.message.reply_text("❌ Withdrawal failed. Please try again.")
        
    except Exception as e:
        logger.error(f"Withdrawal error: {e}")
        await update.message.reply_text("❌ Withdrawal failed. Please try again.")
    
    context.user_data.clear()
    return ConversationHandler.END

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show transaction history"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_data = db.get_user(user.id)
    currency = user_data['currency'] if user_data and 'currency' in user_data.keys() else 'ETB'
    
    # Get regular transactions
    transactions = db.get_user_transactions(user.id, limit=5)
    
    # Get payment requests
    payment_requests = db.get_user_payment_requests(user.id, limit=5)
    
    # Get withdrawal requests
    withdrawal_requests = db.get_user_withdrawal_requests(user.id, limit=5)
    
    if (not transactions or len(transactions) == 0) and (not payment_requests or len(payment_requests) == 0) and (not withdrawal_requests or len(withdrawal_requests) == 0):
        await query.edit_message_text(
            "📊 No transactions yet.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="main_menu")
            ]])
        )
        return
    
    history_text = "📊 **Recent Activity**\n\n"
    
    # Add regular transactions
    if transactions and len(transactions) > 0:
        history_text += "**💰 Transactions:**\n"
        for t in transactions:
            amount = t['amount'] / 100
            sign = "+" if t['amount'] > 0 else ""
            emoji = "💚" if t['type'] == 'deposit' else "💔" if t['type'] == 'withdrawal' else "🎮"
            date = datetime.fromisoformat(t['created_at']).strftime("%m/%d %H:%M")
            status = "✅" if t['status'] == 'completed' else "⏳"
            history_text += f"{emoji} {date}: {sign}{amount:.2f} {currency} {t['type']} {status}\n"
        history_text += "\n"
    
    # Add payment requests
    if payment_requests and len(payment_requests) > 0:
        history_text += "**📱 Payment Requests:**\n"
        for pr in payment_requests:
            amount = pr['amount'] / 100
            date = datetime.fromisoformat(pr['created_at']).strftime("%m/%d %H:%M")
            status_emoji = "✅" if pr['status'] == 'completed' else "⏳" if pr['status'] == 'pending' else "❌"
            method_name = pr['method_name'] if 'method_name' in pr.keys() else 'Payment'
            history_text += f"📤 {date}: {amount:.2f} {currency} {method_name} {status_emoji}\n"
        history_text += "\n"
    
    # Add withdrawal requests
    if withdrawal_requests and len(withdrawal_requests) > 0:
        history_text += "**💸 Withdrawal Requests:**\n"
        for wr in withdrawal_requests:
            amount = wr['amount'] / 100
            date = datetime.fromisoformat(wr['created_at']).strftime("%m/%d %H:%M")
            status_emoji = "✅" if wr['status'] == 'completed' else "⏳" if wr['status'] == 'pending' else "❌"
            method_name = wr['method_name'] if 'method_name' in wr.keys() else 'Withdrawal'
            history_text += f"📥 {date}: {amount:.2f} {currency} {method_name} {status_emoji}\n"
    
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
        user_data = {'balance': 0, 'games_played': 0, 'games_won': 0, 'currency': 'ETB'}
    
    currency = user_data['currency'] if 'currency' in user_data.keys() else 'ETB'
    games_played = user_data['games_played'] if 'games_played' in user_data.keys() else 0
    games_won = user_data['games_won'] if 'games_won' in user_data.keys() else 0
    
    # Game fee 20 ETB (200 cents)
    if user_data['balance'] < 200:
        await query.edit_message_text(
            f"❌ **Insufficient Balance**\n\n"
            f"You need 20.00 {currency} to play Bingo.\n\n"
            f"Your balance: **{user_data['balance']/100:.2f} {currency}**",
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
        f"• Game fee: 20.00 {currency}\n"
        f"• Your balance: {user_data['balance']/100:.2f} {currency}\n"
        f"• Games played: {games_played} | Wins: {games_won}",
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
        "• Deposit using Telbirr or CBE Birr\n"
        "• Withdraw your winnings to mobile money\n"
        "• Each game costs 20 ETB\n\n"
        "**📱 Payment Methods:**\n"
        "• 🔵 Telbirr - Dial *127#\n"
        "• 💚 CBE Birr - Dial *847#\n\n"
        "**📞 Contact:**\n"
        "• For support, contact @admin\n"
        "• Payment issues: Send payment reference to admin"
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
        [InlineKeyboardButton("💰 Pending Payments", callback_data="admin_pending_payments")],
        [InlineKeyboardButton("📊 Pending Withdrawals", callback_data="admin_pending_withdrawals")],
        [InlineKeyboardButton("📈 System Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 User Stats", callback_data="admin_user_stats")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👑 **Admin Control Panel**\n\n"
        "Welcome to the admin panel. Select an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def admin_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending payment requests with approval buttons"""
    query = update.callback_query
    await query.answer()
    
    # Check if user is admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    pending = db.get_pending_payment_requests(limit=20)
    
    if not pending or len(pending) == 0:
        await query.edit_message_text(
            "📊 No pending payment requests.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back to Admin", callback_data="admin_panel")
            ]])
        )
        return
    
    # Send each pending payment with approve/reject buttons
    for p in pending:
        created = datetime.fromisoformat(p['created_at']).strftime("%m/%d %H:%M")
        sender_phone = p['sender_phone'] if 'sender_phone' in p.keys() else 'N/A'
        method_name = p['method_name'] if 'method_name' in p.keys() else 'Unknown'
        first_name = p['first_name'] if 'first_name' in p.keys() else 'User'
        
        # Set emoji based on method
        if 'CBE' in method_name or 'ሲቢኢ' in method_name:
            emoji = "💚"
        else:
            emoji = "🔵"
        
        # Create approve/reject buttons for this payment
        keyboard = [
            [
                InlineKeyboardButton(f"{emoji} ✅ Approve", callback_data=f"approve_payment_{p['request_id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_payment_{p['request_id']}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"{emoji} **Pending Payment Request**\n\n"
            f"**Request ID:** `{p['request_id']}`\n"
            f"**User:** {first_name} (ID: `{p['user_id']}`)\n"
            f"**Amount:** {p['amount']/100:.0f} ETB\n"
            f"**Method:** {method_name}\n"
            f"**Phone:** {sender_phone}\n"
            f"**Time:** {created}\n\n"
            f"Select action:"
        )
        
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    # Send summary
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=f"📊 Showing {len(pending)} pending payment requests.\n"
             f"Use the buttons above to approve or reject each payment.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_pending_payments"),
            InlineKeyboardButton("◀️ Admin Panel", callback_data="admin_panel")
        ]])
    )

async def approve_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment approval"""
    query = update.callback_query
    await query.answer()
    
    # Check if user is admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    request_id = query.data.split('_')[2]  # approve_payment_REQUESTID
    
    # Get payment request details
    request = db.get_payment_request(request_id)
    
    if not request:
        await query.edit_message_text("❌ Payment request not found")
        return
    
    try:
        # Update payment request status
        db.update_payment_request_status(
            request_id=request_id,
            status='completed',
            admin_notes=f"Approved by admin {update.effective_user.id}"
        )
        
        # Add funds to user balance
        db.update_balance(
            user_id=request['user_id'],
            amount=request['amount'],
            transaction_type='deposit',
            description=f'Payment via {request_id}',
            status='completed'
        )
        
        # Get method name for emoji
        method_name = request['method_name'] if 'method_name' in request.keys() else 'Payment'
        if 'CBE' in method_name or 'ሲቢኢ' in method_name:
            emoji = "💚"
        else:
            emoji = "🔵"
        
        # Notify user
        await context.bot.send_message(
            chat_id=request['user_id'],
            text=f"{emoji} **Payment Approved!**\n\n"
                 f"✅ Your payment of **{request['amount']/100:.0f} ETB** has been approved.\n"
                 f"Funds have been added to your balance.\n\n"
                 f"Request ID: `{request_id}`\n"
                 f"Thank you for using Bingo Bot!",
            parse_mode='Markdown'
        )
        
        # Confirm to admin
        await query.edit_message_text(
            f"✅ **Payment Approved Successfully!**\n\n"
            f"**Request ID:** `{request_id}`\n"
            f"**User ID:** {request['user_id']}\n"
            f"**Amount:** {request['amount']/100:.0f} ETB\n\n"
            f"The user has been notified.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back to Pending", callback_data="admin_pending_payments")
            ]])
        )
        
    except Exception as e:
        logger.error(f"Error approving payment: {e}")
        await query.edit_message_text(
            f"❌ Error approving payment: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="admin_pending_payments")
            ]])
        )

async def reject_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment rejection"""
    query = update.callback_query
    await query.answer()
    
    # Check if user is admin
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    request_id = query.data.split('_')[2]  # reject_payment_REQUESTID
    
    # Get payment request details
    request = db.get_payment_request(request_id)
    
    if not request:
        await query.edit_message_text("❌ Payment request not found")
        return
    
    try:
        # Update payment request status
        db.update_payment_request_status(
            request_id=request_id,
            status='rejected',
            admin_notes=f"Rejected by admin {update.effective_user.id}"
        )
        
        # Get method name for emoji
        method_name = request['method_name'] if 'method_name' in request.keys() else 'Payment'
        if 'CBE' in method_name or 'ሲቢኢ' in method_name:
            emoji = "💚"
        else:
            emoji = "🔵"
        
        # Notify user
        await context.bot.send_message(
            chat_id=request['user_id'],
            text=f"{emoji} **Payment Rejected**\n\n"
                 f"❌ Your payment of **{request['amount']/100:.0f} ETB** has been rejected.\n\n"
                 f"**Request ID:** `{request_id}`\n\n"
                 f"Please contact support if you believe this is an error.\n"
                 f"Make sure you sent the payment to the correct number and provided the right reference.",
            parse_mode='Markdown'
        )
        
        # Confirm to admin
        await query.edit_message_text(
            f"❌ **Payment Rejected**\n\n"
            f"**Request ID:** `{request_id}`\n"
            f"**User ID:** {request['user_id']}\n"
            f"**Amount:** {request['amount']/100:.0f} ETB\n\n"
            f"The user has been notified.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back to Pending", callback_data="admin_pending_payments")
            ]])
        )
        
    except Exception as e:
        logger.error(f"Error rejecting payment: {e}")
        await query.edit_message_text(
            f"❌ Error rejecting payment: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back", callback_data="admin_pending_payments")
            ]])
        )

async def admin_pending_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending withdrawal requests"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    pending = db.get_pending_withdrawal_requests(limit=20)
    
    if not pending or len(pending) == 0:
        await query.edit_message_text(
            "📊 No pending withdrawal requests.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Back to Admin", callback_data="admin_panel")
            ]])
        )
        return
    
    # Send each pending withdrawal with approve/reject buttons
    for w in pending:
        created = datetime.fromisoformat(w['created_at']).strftime("%m/%d %H:%M")
        first_name = w['first_name'] if 'first_name' in w.keys() else 'User'
        method_name = w['method_name'] if 'method_name' in w.keys() else 'Unknown'
        account_number = w['account_number'] if 'account_number' in w.keys() else 'N/A'
        account_name = w['account_name'] if 'account_name' in w.keys() else 'N/A'
        
        # Set emoji based on method
        if 'CBE' in method_name or 'ሲቢኢ' in method_name:
            emoji = "💚"
        else:
            emoji = "🔵"
        
        # Create approve/reject buttons
        keyboard = [
            [
                InlineKeyboardButton(f"{emoji} ✅ Approve", callback_data=f"approve_withdrawal_{w['request_id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_withdrawal_{w['request_id']}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"{emoji} **Pending Withdrawal Request**\n\n"
            f"**Request ID:** `{w['request_id']}`\n"
            f"**User:** {first_name} (ID: {w['user_id']})\n"
            f"**Amount:** {w['amount']/100:.0f} ETB\n"
            f"**Method:** {method_name}\n"
            f"**Account:** {account_number}\n"
            f"**Name:** {account_name}\n"
            f"**Time:** {created}\n\n"
            f"Select action:"
        )
        
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    # Send summary
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=f"📊 Showing {len(pending)} pending withdrawal requests.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_pending_withdrawals"),
            InlineKeyboardButton("◀️ Admin Panel", callback_data="admin_panel")
        ]])
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system statistics"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    stats = db.get_system_stats()
    
    message = (
        f"📊 **System Statistics**\n\n"
        f"**Users:** {stats.get('total_users', 0)}\n"
        f"**Total Balance:** {stats.get('total_balance', 0)/100:.2f} ETB\n"
        f"**Total Deposits:** {stats.get('total_deposits', 0)/100:.2f} ETB\n"
        f"**Total Withdrawals:** {stats.get('total_withdrawals', 0)/100:.2f} ETB\n"
        f"**Game Fees:** {stats.get('total_game_fees', 0)/100:.2f} ETB\n"
        f"**Game Wins:** {stats.get('total_game_wins', 0)/100:.2f} ETB\n\n"
        f"**Pending Payments:** {stats.get('pending_payment_requests', 0)}\n"
        f"**Pending Withdrawals:** {stats.get('pending_withdrawal_requests', 0)}\n"
        f"**Today's Volume:** {stats.get('today_volume', 0)/100:.2f} ETB\n"
        f"**Total Games:** {stats.get('total_games', 0)}\n"
        f"**Completed Games:** {stats.get('completed_games', 0)}"
    )
    
    await query.edit_message_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats"),
            InlineKeyboardButton("◀️ Back", callback_data="admin_panel")
        ]])
    )

async def admin_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user statistics"""
    query = update.callback_query
    await query.answer()
    
    if str(update.effective_user.id) != ADMIN_USER_ID:
        await query.edit_message_text("❌ Unauthorized")
        return
    
    # Top users by balance
    top_balance = db.get_top_users(by='balance', limit=5)
    
    # Top players by games played
    top_players = db.get_top_users(by='games_played', limit=5)
    
    # Top winners
    top_winners = db.get_top_users(by='games_won', limit=5)
    
    message = "👥 **User Leaderboards**\n\n"
    
    message += "**💰 Top Balances:**\n"
    if top_balance and len(top_balance) > 0:
        for i, u in enumerate(top_balance, 1):
            name = u['first_name'] if 'first_name' in u.keys() else u['username'] if 'username' in u.keys() else f"User {u['user_id']}"
            balance = u['balance'] if 'balance' in u.keys() else 0
            message += f"{i}. {name}: **{balance/100:.2f} ETB**\n"
    else:
        message += "No data\n"
    
    message += "\n**🎮 Most Games Played:**\n"
    if top_players and len(top_players) > 0:
        for i, u in enumerate(top_players, 1):
            name = u['first_name'] if 'first_name' in u.keys() else u['username'] if 'username' in u.keys() else f"User {u['user_id']}"
            games = u['games_played'] if 'games_played' in u.keys() else 0
            message += f"{i}. {name}: **{games}** games\n"
    else:
        message += "No data\n"
    
    message += "\n**🏆 Most Wins:**\n"
    if top_winners and len(top_winners) > 0:
        for i, u in enumerate(top_winners, 1):
            name = u['first_name'] if 'first_name' in u.keys() else u['username'] if 'username' in u.keys() else f"User {u['user_id']}"
            wins = u['games_won'] if 'games_won' in u.keys() else 0
            message += f"{i}. {name}: **{wins}** wins\n"
    else:
        message += "No data\n"
    
    await query.edit_message_text(
        message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_user_stats"),
            InlineKeyboardButton("◀️ Back", callback_data="admin_panel")
        ]])
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
    
    # Conversation handlers
    deposit_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(deposit_method_selected, pattern='^deposit_method_')
        ],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ethiopian_deposit)],
            PAYMENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_payment_phone)],
            PAYMENT_REFERENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_payment_reference)]
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(show_main_menu, pattern='^main_menu$')],
        per_message=False,
        name="deposit_conversation"
    )
    
    withdraw_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(withdraw_command, pattern='^withdraw$'),
            CallbackQueryHandler(withdraw_method_selected, pattern='^withdraw_method_')
        ],
        states={
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount_handler)],
            WITHDRAW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_address_handler)],
            WITHDRAW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_name_handler)]
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
    application.add_handler(CallbackQueryHandler(payment_done_callback, pattern='^payment_done_'))
    
    # Admin handlers
    application.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_pending_payments, pattern='^admin_pending_payments$'))
    application.add_handler(CallbackQueryHandler(admin_pending_withdrawals, pattern='^admin_pending_withdrawals$'))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern='^admin_stats$'))
    application.add_handler(CallbackQueryHandler(admin_user_stats, pattern='^admin_user_stats$'))
    
    # Payment approval/rejection handlers
    application.add_handler(CallbackQueryHandler(approve_payment_callback, pattern='^approve_payment_'))
    application.add_handler(CallbackQueryHandler(reject_payment_callback, pattern='^reject_payment_'))
    
    # Add conversation handlers
    application.add_handler(deposit_conv)
    application.add_handler(withdraw_conv)
    
    return application

async def async_main():
    """Async version of main for use with asyncio"""
    global shutdown_flag
    application = None
    
    try:
        # Get token with validation
        BOT_TOKEN = os.getenv('BOT_TOKEN')
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN not found in environment variables")
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
        
        # Initialize application
        await application.initialize()
        await application.start()
        
        # Start polling
        await application.updater.start_polling()
        
        # Keep running until stopped
        while not shutdown_flag:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        raise
    finally:
        # Clean shutdown
        if application:
            try:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
                logger.info("✅ Bot shutdown complete")
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")

def main():
    """Synchronous main for backwards compatibility"""
    # Create new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Run the async main
        loop.run_until_complete(async_main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
    finally:
        # Clean up
        loop.close()

if __name__ == "__main__":
    main()