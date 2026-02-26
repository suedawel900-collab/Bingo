import os
import sqlite3
import json
import threading
from datetime import datetime
from contextlib import contextmanager
import logging
import uuid

logger = logging.getLogger(__name__)

class Database:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize database"""
        self.db_path = os.getenv('DATABASE_URL', 'bingo.db')
        if self.db_path.startswith('postgresql://'):
            self.db_path = 'bingo.db'
        self._create_tables()
        self._insert_default_payment_methods()
        logger.info(f"✅ Database initialized at {self.db_path}")
    
    @contextmanager
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def _create_tables(self):
        """Create all tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Users table with welcome bonus tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    phone_number TEXT,
                    country TEXT DEFAULT 'ET',
                    currency TEXT DEFAULT 'ETB',
                    balance INTEGER DEFAULT 0,
                    total_deposits INTEGER DEFAULT 0,
                    total_withdrawals INTEGER DEFAULT 0,
                    games_played INTEGER DEFAULT 0,
                    games_won INTEGER DEFAULT 0,
                    welcome_bonus_claimed BOOLEAN DEFAULT FALSE,
                    welcome_bonus_amount INTEGER DEFAULT 1000,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Active games tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS active_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game_id INTEGER NOT NULL,
                    card_ids TEXT NOT NULL,
                    stake INTEGER NOT NULL,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active',
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Transactions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'completed',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Payment methods table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_methods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    method_code TEXT UNIQUE NOT NULL,
                    method_name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    provider TEXT,
                    min_amount INTEGER DEFAULT 1000,
                    max_amount INTEGER DEFAULT 500000,
                    is_active BOOLEAN DEFAULT TRUE,
                    instructions TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Payment requests table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    method_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    sender_phone TEXT,
                    transaction_reference TEXT,
                    admin_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    FOREIGN KEY (method_id) REFERENCES payment_methods (id)
                )
            ''')
            
            # Withdrawal requests table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS withdrawal_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    method_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    account_number TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    phone_number TEXT,
                    status TEXT DEFAULT 'pending',
                    admin_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    FOREIGN KEY (method_id) REFERENCES payment_methods (id)
                )
            ''')
            
            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_active_games_user_id ON active_games(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_requests_user_id ON payment_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_user_id ON withdrawal_requests(user_id)')
            
            conn.commit()
            logger.info("✅ Database tables created/verified")
    
    def _insert_default_payment_methods(self):
        """Insert default payment methods"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if methods already exist
            cursor.execute('SELECT COUNT(*) as count FROM payment_methods')
            if cursor.fetchone()['count'] > 0:
                return
            
            # Default payment methods
            default_methods = [
                ('TELBIRR', 'ቴሌቢር (Telbirr)', 'mobile_money', 'Ethio Telecom', 1000, 500000, 1,
                 '🔵 **ቴሌቢር ክፍያ / Telbirr Payment**\n\n'
                 '1. ወደ ቴሌቢር ሜኑ ለመግባት *127# ይደውሉ\n'
                 '2. "ገንዘብ ላክ" የሚለውን ይምረጡ\n'
                 '3. ቁጥር **0953933030** ያስገቡ\n'
                 '4. መጠኑን ያስገቡ\n'
                 '5. ፒንዎን ያስገቡ\n'
                 '6. ከተጠናቀቀ በኋላ የደረሰኝ ቁጥር ያስቀምጡ'),
                
                ('CBEBIRR', 'ሲቢኢ ቢር (CBE Birr)', 'mobile_money', 'CBE', 1000, 500000, 1,
                 '💚 **ሲቢኢ ቢር ክፍያ / CBE Birr Payment**\n\n'
                 '1. ወደ ሲቢኢ ቢር ሜኑ ለመግባት *847# ይደውሉ\n'
                 '2. "ገንዘብ ላክ" የሚለውን ይምረጡ\n'
                 '3. ቁጥር **0953933030** ያስገቡ\n'
                 '4. መጠኑን ያስገቡ\n'
                 '5. ፒንዎን ያስገቡ\n'
                 '6. ከተጠናቀቀ በኋላ የግብይት መለያ ቁጥር ያስቀምጡ')
            ]
            
            for method in default_methods:
                cursor.execute('''
                    INSERT INTO payment_methods 
                    (method_code, method_name, type, provider, min_amount, max_amount, is_active, instructions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', method)
            
            conn.commit()
            logger.info("✅ Default payment methods inserted")
    
    # ==================== USER METHODS ====================
    
    def create_user(self, user_id, username=None, first_name=None, last_name=None, phone_number=None, country='ET', currency='ETB'):
        """Create a new user with welcome bonus"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if user already exists
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            existing = cursor.fetchone()
            
            if existing:
                # Update existing user
                cursor.execute('''
                    UPDATE users 
                    SET username = COALESCE(?, username),
                        first_name = COALESCE(?, first_name),
                        last_name = COALESCE(?, last_name),
                        phone_number = COALESCE(?, phone_number),
                        country = COALESCE(?, country),
                        currency = COALESCE(?, currency),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                ''', (username, first_name, last_name, phone_number, country, currency, user_id))
                conn.commit()
                return self.get_user(user_id)
            
            # Create new user with welcome bonus
            welcome_bonus = 1000  # 10 ETB in cents
            
            cursor.execute('''
                INSERT INTO users 
                (user_id, username, first_name, last_name, phone_number, country, currency, balance, welcome_bonus_claimed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, phone_number, country, currency, welcome_bonus, True))
            
            conn.commit()
            logger.info(f"✅ New user {user_id} created with {welcome_bonus/100} ETB welcome bonus")
            
            # Record welcome bonus transaction
            self.add_transaction(
                user_id=user_id,
                amount=welcome_bonus,
                type='welcome_bonus',
                description='Welcome bonus'
            )
            
            return self.get_user(user_id)
    
    def get_user(self, user_id):
        """Get user by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    def get_or_create_user(self, user_id, username=None, first_name=None, last_name=None, phone_number=None):
        """Get existing user or create new one"""
        user = self.get_user(user_id)
        if user:
            # Update user info if provided
            if username or first_name or last_name or phone_number:
                with self.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE users 
                        SET username = COALESCE(?, username),
                            first_name = COALESCE(?, first_name),
                            last_name = COALESCE(?, last_name),
                            phone_number = COALESCE(?, phone_number),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ?
                    ''', (username, first_name, last_name, phone_number, user_id))
                    conn.commit()
                user = self.get_user(user_id)
            return user
        return self.create_user(user_id, username, first_name, last_name, phone_number)
    
    def update_balance(self, user_id, amount, transaction_type, description=None):
        """Update user balance"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('BEGIN TRANSACTION')
            
            try:
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                user = cursor.fetchone()
                
                if not user:
                    cursor.execute('ROLLBACK')
                    return None
                
                new_balance = user['balance'] + amount
                
                cursor.execute('''
                    UPDATE users 
                    SET balance = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                ''', (new_balance, user_id))
                
                # Update games played counter
                if transaction_type == 'game_fee':
                    cursor.execute('''
                        UPDATE users 
                        SET games_played = games_played + 1
                        WHERE user_id = ?
                    ''', (user_id,))
                
                # Update games won counter
                if transaction_type == 'game_win':
                    cursor.execute('''
                        UPDATE users 
                        SET games_won = games_won + 1
                        WHERE user_id = ?
                    ''', (user_id,))
                
                # Create transaction record
                cursor.execute('''
                    INSERT INTO transactions 
                    (user_id, amount, type, description, completed_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, amount, transaction_type, description, datetime.now()))
                
                transaction_id = cursor.lastrowid
                conn.commit()
                
                return {
                    'new_balance': new_balance,
                    'transaction_id': transaction_id
                }
                
            except Exception as e:
                cursor.execute('ROLLBACK')
                logger.error(f"Database error: {e}")
                raise e
    
    def add_transaction(self, user_id, amount, type, description=None):
        """Add a transaction record"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO transactions 
                (user_id, amount, type, description, completed_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, amount, type, description, datetime.now()))
            conn.commit()
            return cursor.lastrowid
    
    def add_active_game(self, user_id, game_id, card_ids, stake):
        """Track active game for user"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO active_games (user_id, game_id, card_ids, stake)
                VALUES (?, ?, ?, ?)
            ''', (user_id, game_id, json.dumps(card_ids), stake))
            conn.commit()
            return cursor.lastrowid
    
    def get_active_games_count(self, user_id):
        """Get number of active games for user"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) as count FROM active_games 
                WHERE user_id = ? AND status = 'active'
            ''', (user_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0
    
    def get_total_stake(self, user_id):
        """Get total stake for user's active games"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COALESCE(SUM(stake), 0) as total FROM active_games 
                WHERE user_id = ? AND status = 'active'
            ''', (user_id,))
            row = cursor.fetchone()
            return row['total'] if row else 0
    
    def complete_game(self, user_id, game_id):
        """Mark game as completed"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE active_games 
                SET status = 'completed' 
                WHERE user_id = ? AND game_id = ?
            ''', (user_id, game_id))
            conn.commit()
            return cursor.rowcount > 0
    
    # ==================== NEW METHODS FOR WINNER PAYOUT ====================
    
    def get_total_cards_for_game(self, game_id):
        """Sum of card counts for all active games in this game_id"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COALESCE(SUM(json_array_length(card_ids)), 0) as total_cards
                FROM active_games
                WHERE game_id = ? AND status = 'active'
            ''', (game_id,))
            row = cursor.fetchone()
            return row['total_cards'] if row else 0
    
    def complete_all_games_for_round(self, game_id):
        """Mark all active games for this game as completed"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE active_games
                SET status = 'completed'
                WHERE game_id = ? AND status = 'active'
            ''', (game_id,))
            conn.commit()
            return cursor.rowcount
    
    # ==================== PAYMENT METHODS ====================
    
    def get_payment_methods(self, type=None, active_only=True):
        """Get payment methods"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM payment_methods WHERE 1=1"
            params = []
            
            if active_only:
                query += " AND is_active = 1"
            
            if type:
                query += " AND type = ?"
                params.append(type)
            
            query += " ORDER BY method_name"
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_payment_method(self, method_id):
        """Get payment method by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payment_methods WHERE id = ?', (method_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_payment_method_by_code(self, method_code):
        """Get payment method by code"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payment_methods WHERE method_code = ?', (method_code,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_primary_account(self, method_id):
        """Get primary account for payment method (simplified)"""
        # For now, just return None - accounts not implemented
        return None
    
    # ==================== PAYMENT REQUESTS ====================
    
    def create_payment_request(self, user_id, method_id, amount, sender_phone=None):
        """Create payment request"""
        request_id = f"PAY-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO payment_requests 
                (request_id, user_id, method_id, amount, sender_phone)
                VALUES (?, ?, ?, ?, ?)
            ''', (request_id, user_id, method_id, amount, sender_phone))
            conn.commit()
            return request_id
    
    def get_payment_request(self, request_id):
        """Get payment request by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT pr.*, pm.method_name 
                FROM payment_requests pr
                LEFT JOIN payment_methods pm ON pr.method_id = pm.id
                WHERE pr.request_id = ?
            ''', (request_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user_payment_requests(self, user_id, limit=10):
        """Get user payment requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM payment_requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_pending_payment_requests(self, limit=20):
        """Get pending payment requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT pr.*, u.username, u.first_name, u.last_name, pm.method_name
                FROM payment_requests pr
                JOIN users u ON pr.user_id = u.user_id
                LEFT JOIN payment_methods pm ON pr.method_id = pm.id
                WHERE pr.status = 'pending'
                ORDER BY pr.created_at ASC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def update_payment_request_status(self, request_id, status, admin_notes=None):
        """Update payment request status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if status == 'completed':
                cursor.execute('''
                    UPDATE payment_requests 
                    SET status = ?, admin_notes = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE request_id = ?
                ''', (status, admin_notes, request_id))
            else:
                cursor.execute('''
                    UPDATE payment_requests 
                    SET status = ?, admin_notes = ?
                    WHERE request_id = ?
                ''', (status, admin_notes, request_id))
            
            conn.commit()
            return cursor.rowcount > 0
    
    def add_payment_proof(self, request_id, proof_type, proof_data, file_path=None):
        """Add payment proof"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE payment_requests 
                SET transaction_reference = ?
                WHERE request_id = ?
            ''', (proof_data, request_id))
            conn.commit()
            return cursor.rowcount > 0
    
    # ==================== WITHDRAWAL REQUESTS ====================
    
    def create_withdrawal_request(self, user_id, method_id, amount, account_number, account_name, phone_number=None):
        """Create withdrawal request"""
        request_id = f"WDR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check balance
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            
            if not user or user['balance'] < amount:
                return None
            
            cursor.execute('''
                INSERT INTO withdrawal_requests 
                (request_id, user_id, method_id, amount, account_number, account_name, phone_number)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (request_id, user_id, method_id, amount, account_number, account_name, phone_number))
            
            conn.commit()
            return request_id
    
    def get_withdrawal_request(self, request_id):
        """Get withdrawal request by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT wr.*, pm.method_name 
                FROM withdrawal_requests wr
                LEFT JOIN payment_methods pm ON wr.method_id = pm.id
                WHERE wr.request_id = ?
            ''', (request_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user_withdrawal_requests(self, user_id, limit=10):
        """Get user withdrawal requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM withdrawal_requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_pending_withdrawal_requests(self, limit=20):
        """Get pending withdrawal requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT wr.*, u.username, u.first_name, u.last_name, pm.method_name
                FROM withdrawal_requests wr
                JOIN users u ON wr.user_id = u.user_id
                LEFT JOIN payment_methods pm ON wr.method_id = pm.id
                WHERE wr.status = 'pending'
                ORDER BY wr.created_at ASC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def process_withdrawal_request(self, request_id, status, admin_notes=None, processed_by=None):
        """Process withdrawal request"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE withdrawal_requests 
                SET status = ?, admin_notes = ?, processed_by = ?, processed_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
            ''', (status, admin_notes, processed_by, request_id))
            
            conn.commit()
            
            if cursor.rowcount > 0 and status == 'completed':
                # Get request details
                cursor.execute('SELECT user_id, amount FROM withdrawal_requests WHERE request_id = ?', (request_id,))
                req = cursor.fetchone()
                
                if req:
                    # Update user balance
                    self.update_balance(
                        user_id=req['user_id'],
                        amount=-req['amount'],
                        transaction_type='withdrawal',
                        description=f'Withdrawal via {request_id}'
                    )
            
            return cursor.rowcount > 0
    
    # ==================== GAME METHODS ====================
    
    def get_active_game(self):
        """Get active game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM games 
                WHERE status IN ('waiting', 'active')
                ORDER BY created_at DESC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user_transactions(self, user_id, limit=10, offset=0, status=None):
        """Get user transactions"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if status:
                cursor.execute('''
                    SELECT * FROM transactions 
                    WHERE user_id = ? AND status = ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                ''', (user_id, status, limit, offset))
            else:
                cursor.execute('''
                    SELECT * FROM transactions 
                    WHERE user_id = ? 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                ''', (user_id, limit, offset))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ==================== STATISTICS METHODS ====================
    
    def get_system_stats(self):
        """Get system statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # User stats
            cursor.execute('SELECT COUNT(*) as count FROM users')
            stats['total_users'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COALESCE(SUM(balance), 0) as total FROM users')
            stats['total_balance'] = cursor.fetchone()['total']
            
            # Transaction stats
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "deposit" AND status = "completed"')
            stats['total_deposits'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "withdrawal" AND status = "completed"')
            stats['total_withdrawals'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "game_fee" AND status = "completed"')
            stats['total_game_fees'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "game_win" AND status = "completed"')
            stats['total_game_wins'] = cursor.fetchone()['total']
            
            # Pending requests
            cursor.execute('SELECT COUNT(*) as count FROM payment_requests WHERE status = "pending"')
            stats['pending_payment_requests'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM withdrawal_requests WHERE status = "pending"')
            stats['pending_withdrawal_requests'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM active_games WHERE status = "active"')
            stats['active_games'] = cursor.fetchone()['count']
            
            # Today's volume
            cursor.execute('''
                SELECT COALESCE(SUM(amount), 0) as total
                FROM transactions 
                WHERE date(created_at) = date('now') AND status = 'completed'
            ''')
            stats['today_volume'] = cursor.fetchone()['total']
            
            return stats
    
    def get_top_users(self, by='balance', limit=5):
        """Get top users"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if by == 'balance':
                cursor.execute('''
                    SELECT user_id, username, first_name, balance 
                    FROM users 
                    ORDER BY balance DESC 
                    LIMIT ?
                ''', (limit,))
            elif by == 'games_played':
                cursor.execute('''
                    SELECT user_id, username, first_name, games_played 
                    FROM users 
                    ORDER BY games_played DESC 
                    LIMIT ?
                ''', (limit,))
            elif by == 'games_won':
                cursor.execute('''
                    SELECT user_id, username, first_name, games_won 
                    FROM users 
                    ORDER BY games_won DESC 
                    LIMIT ?
                ''', (limit,))
            else:
                return []
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]