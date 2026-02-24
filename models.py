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
            self.db_path = 'bingo.db'  # Use SQLite for simplicity
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
            
            # Users table
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Transactions table (this was missing)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    type TEXT CHECK(type IN ('deposit', 'withdrawal', 'game_fee', 'game_win', 'refund', 'withdrawal_refund')) NOT NULL,
                    status TEXT CHECK(status IN ('pending', 'completed', 'failed', 'cancelled')) DEFAULT 'pending',
                    payment_intent_id TEXT UNIQUE,
                    description TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Payment Methods table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_methods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    method_code TEXT UNIQUE NOT NULL,
                    method_name TEXT NOT NULL,
                    type TEXT CHECK(type IN ('mobile_money', 'bank', 'manual', 'card')) NOT NULL,
                    provider TEXT,
                    account_number TEXT,
                    account_name TEXT,
                    min_amount INTEGER DEFAULT 100,
                    max_amount INTEGER DEFAULT 500000,
                    fee_percentage REAL DEFAULT 0,
                    fee_fixed INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    instructions TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Payment Accounts table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    method_id INTEGER NOT NULL,
                    account_number TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    phone_number TEXT,
                    branch TEXT,
                    is_primary BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (method_id) REFERENCES payment_methods (id)
                )
            ''')
            
            # Payment Requests table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    method_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT DEFAULT 'ETB',
                    status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'failed', 'expired', 'rejected')) DEFAULT 'pending',
                    transaction_id TEXT,
                    sender_phone TEXT,
                    sender_name TEXT,
                    transaction_reference TEXT,
                    admin_notes TEXT,
                    expiry_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    FOREIGN KEY (method_id) REFERENCES payment_methods (id)
                )
            ''')
            
            # Payment Proofs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_proofs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL,
                    proof_type TEXT CHECK(proof_type IN ('screenshot', 'reference', 'text')) NOT NULL,
                    proof_data TEXT NOT NULL,
                    file_path TEXT,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    verified BOOLEAN DEFAULT FALSE,
                    verified_by INTEGER,
                    verified_at TIMESTAMP,
                    FOREIGN KEY (request_id) REFERENCES payment_requests (id),
                    FOREIGN KEY (verified_by) REFERENCES users (user_id)
                )
            ''')
            
            # Withdrawal Requests table
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
                    status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'rejected')) DEFAULT 'pending',
                    admin_notes TEXT,
                    processed_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    FOREIGN KEY (method_id) REFERENCES payment_methods (id),
                    FOREIGN KEY (processed_by) REFERENCES users (user_id)
                )
            ''')
            
            # Games table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_code TEXT UNIQUE NOT NULL,
                    status TEXT CHECK(status IN ('waiting', 'active', 'completed', 'cancelled')) DEFAULT 'waiting',
                    prize_pool INTEGER DEFAULT 0,
                    player_count INTEGER DEFAULT 0,
                    called_numbers TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP
                )
            ''')
            
            # Game players table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS game_players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    card_numbers TEXT NOT NULL,
                    marked_numbers TEXT DEFAULT '[]',
                    is_winner BOOLEAN DEFAULT FALSE,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games (id),
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    UNIQUE(game_id, user_id)
                )
            ''')
            
            # Create indexes for better performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_requests_user_id ON payment_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_user_id ON withdrawal_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_methods_is_active ON payment_methods(is_active)')
            
            conn.commit()
            logger.info("✅ Database tables created/verified")
    
    def _insert_default_payment_methods(self):
        """Insert default payment methods (Tebirr and CBE Birr only)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if methods already exist
            cursor.execute('SELECT COUNT(*) as count FROM payment_methods')
            if cursor.fetchone()['count'] > 0:
                return
            
            # Only Tebirr and CBE Birr
            default_methods = [
                # Telbirr
                ('TELBIRR', 'ቴሌቢር (Telbirr)', 'mobile_money', 'Ethio Telecom', None, None, 1000, 500000, 0, 0, 1,
                 '🔵 **ቴሌቢር ክፍያ / Telbirr Payment**\n\n'
                 '1. ወደ ቴሌቢር ሜኑ ለመግባት *127# ይደውሉ\n'
                 '2. "ገንዘብ ላክ" የሚለውን ይምረጡ\n'
                 '3. ቁጥር **0953933030** ያስገቡ\n'
                 '4. መጠኑን ያስገቡ\n'
                 '5. ፒንዎን ያስገቡ\n'
                 '6. ከተጠናቀቀ በኋላ የደረሰኝ ቁጥር ያስቀምጡ'),
                
                # CBE Birr
                ('CBEBIRR', 'ሲቢኢ ቢር (CBE Birr)', 'mobile_money', 'CBE', None, None, 1000, 500000, 0, 0, 1,
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
                    (method_code, method_name, type, provider, account_number, account_name, 
                     min_amount, max_amount, fee_percentage, fee_fixed, is_active, instructions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', method)
            
            # Add primary accounts
            cursor.execute('SELECT id FROM payment_methods WHERE method_code = "TELBIRR"')
            telbirr_id = cursor.fetchone()['id']
            cursor.execute('''
                INSERT INTO payment_accounts (method_id, account_number, account_name, phone_number, is_primary)
                VALUES (?, ?, ?, ?, ?)
            ''', (telbirr_id, '0953933030', 'Bingo Entertainment', '0953933030', 1))
            
            cursor.execute('SELECT id FROM payment_methods WHERE method_code = "CBEBIRR"')
            cbebirr_id = cursor.fetchone()['id']
            cursor.execute('''
                INSERT INTO payment_accounts (method_id, account_number, account_name, phone_number, is_primary)
                VALUES (?, ?, ?, ?, ?)
            ''', (cbebirr_id, '0953933030', 'Bingo Entertainment', '0953933030', 1))
            
            conn.commit()
            logger.info("✅ Default payment methods inserted (Tebirr and CBE Birr only)")
    
    # ==================== USER METHODS ====================
    
    def get_user(self, user_id):
        """Get user by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            return cursor.fetchone()
    
    def create_user(self, user_id, username=None, first_name=None, last_name=None, phone_number=None, country='ET', currency='ETB'):
        """Create or update user"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, phone_number, country, currency)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, username),
                    first_name = COALESCE(excluded.first_name, first_name),
                    last_name = COALESCE(excluded.last_name, last_name),
                    phone_number = COALESCE(excluded.phone_number, phone_number),
                    country = COALESCE(excluded.country, country),
                    currency = COALESCE(excluded.currency, currency),
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, username, first_name, last_name, phone_number, country, currency))
            conn.commit()
            logger.info(f"✅ User {user_id} created/updated")
    
    def update_balance(self, user_id, amount, transaction_type, description=None, status='completed', payment_intent_id=None):
        """Update user balance with transaction"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('BEGIN TRANSACTION')
            
            try:
                # Get current balance
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                user = cursor.fetchone()
                
                if not user:
                    logger.error(f"User {user_id} not found")
                    cursor.execute('ROLLBACK')
                    return None
                
                # Calculate new balance only if status is 'completed'
                new_balance = user['balance']
                if status == 'completed':
                    new_balance = user['balance'] + amount
                    
                    # Update user balance
                    cursor.execute('''
                        UPDATE users 
                        SET balance = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ?
                    ''', (new_balance, user_id))
                    
                    # Update total deposits/withdrawals counters
                    if transaction_type == 'deposit':
                        cursor.execute('''
                            UPDATE users 
                            SET total_deposits = total_deposits + ?
                            WHERE user_id = ?
                        ''', (amount, user_id))
                    elif transaction_type in ['withdrawal', 'withdrawal_refund']:
                        cursor.execute('''
                            UPDATE users 
                            SET total_withdrawals = total_withdrawals + ?
                            WHERE user_id = ?
                        ''', (abs(amount), user_id))
                
                # Create transaction record
                cursor.execute('''
                    INSERT INTO transactions 
                    (user_id, amount, type, status, payment_intent_id, description, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, 
                    amount, 
                    transaction_type, 
                    status, 
                    payment_intent_id, 
                    description,
                    datetime.now() if status == 'completed' else None
                ))
                
                transaction_id = cursor.lastrowid
                conn.commit()
                
                logger.info(f"✅ Transaction {transaction_id}: User {user_id}, {transaction_type}, {amount/100:.2f} ETB, Status: {status}")
                
                return {
                    'new_balance': new_balance, 
                    'transaction_id': transaction_id, 
                    'status': status
                }
                
            except Exception as e:
                cursor.execute('ROLLBACK')
                logger.error(f"Database error in update_balance: {e}")
                raise e
    
    # ==================== TRANSACTION METHODS ====================
    
    def get_user_transactions(self, user_id, limit=10, offset=0, status=None):
        """Get user transaction history"""
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
            
            return cursor.fetchall()
    
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
            return cursor.fetchall()
    
    def get_payment_method(self, method_id):
        """Get payment method by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payment_methods WHERE id = ?', (method_id,))
            return cursor.fetchone()
    
    def get_primary_account(self, method_id):
        """Get primary account for a method"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM payment_accounts 
                WHERE method_id = ? AND is_primary = 1 AND is_active = 1
            ''', (method_id,))
            return cursor.fetchone()
    
    # ==================== PAYMENT REQUESTS ====================
    
    def create_payment_request(self, user_id, method_id, amount, sender_phone=None, sender_name=None):
        """Create a new payment request"""
        request_id = f"PAY-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO payment_requests 
                (request_id, user_id, method_id, amount, sender_phone, sender_name, expiry_time)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now', '+1 hour'))
            ''', (request_id, user_id, method_id, amount, sender_phone, sender_name))
            
            conn.commit()
            return request_id
    
    def get_payment_request(self, request_id):
        """Get payment request by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT pr.*, pm.method_name, pm.type, pm.provider
                FROM payment_requests pr
                JOIN payment_methods pm ON pr.method_id = pm.id
                WHERE pr.request_id = ?
            ''', (request_id,))
            return cursor.fetchone()
    
    def get_user_payment_requests(self, user_id, status=None, limit=10):
        """Get user's payment requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = '''
                SELECT pr.*, pm.method_name, pm.type
                FROM payment_requests pr
                JOIN payment_methods pm ON pr.method_id = pm.id
                WHERE pr.user_id = ?
            '''
            params = [user_id]
            
            if status:
                query += " AND pr.status = ?"
                params.append(status)
            
            query += " ORDER BY pr.created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def get_pending_payment_requests(self, limit=50):
        """Get all pending payment requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Expire old requests
            cursor.execute('''
                UPDATE payment_requests 
                SET status = 'expired' 
                WHERE status = 'pending' AND expiry_time < datetime('now')
            ''')
            
            cursor.execute('''
                SELECT pr.*, u.username, u.first_name, u.last_name, u.phone_number,
                       pm.method_name, pm.type
                FROM payment_requests pr
                JOIN users u ON pr.user_id = u.user_id
                JOIN payment_methods pm ON pr.method_id = pm.id
                WHERE pr.status = 'pending'
                ORDER BY pr.created_at ASC
                LIMIT ?
            ''', (limit,))
            
            return cursor.fetchall()
    
    def update_payment_request_status(self, request_id, status, admin_notes=None):
        """Update payment request status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if status == 'completed':
                cursor.execute('''
                    UPDATE payment_requests 
                    SET status = ?, admin_notes = ?, 
                        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE request_id = ?
                ''', (status, admin_notes, request_id))
            else:
                cursor.execute('''
                    UPDATE payment_requests 
                    SET status = ?, admin_notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE request_id = ?
                ''', (status, admin_notes, request_id))
            
            conn.commit()
            return cursor.rowcount > 0
    
    def add_payment_proof(self, request_id, proof_type, proof_data, file_path=None):
        """Add proof for manual payment"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get request ID
            cursor.execute('SELECT id FROM payment_requests WHERE request_id = ?', (request_id,))
            req = cursor.fetchone()
            
            if not req:
                return None
            
            cursor.execute('''
                INSERT INTO payment_proofs (request_id, proof_type, proof_data, file_path)
                VALUES (?, ?, ?, ?)
            ''', (req['id'], proof_type, proof_data, file_path))
            
            conn.commit()
            return cursor.lastrowid
    
    # ==================== WITHDRAWAL REQUESTS ====================
    
    def create_withdrawal_request(self, user_id, method_id, amount, account_number, account_name, phone_number=None):
        """Create a new withdrawal request"""
        request_id = f"WDR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check user balance
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
                SELECT wr.*, pm.method_name, pm.type, pm.provider,
                       u.username, u.first_name, u.last_name
                FROM withdrawal_requests wr
                JOIN payment_methods pm ON wr.method_id = pm.id
                JOIN users u ON wr.user_id = u.user_id
                WHERE wr.request_id = ?
            ''', (request_id,))
            return cursor.fetchone()
    
    def get_user_withdrawal_requests(self, user_id, status=None, limit=10):
        """Get user's withdrawal requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = '''
                SELECT wr.*, pm.method_name, pm.type
                FROM withdrawal_requests wr
                JOIN payment_methods pm ON wr.method_id = pm.id
                WHERE wr.user_id = ?
            '''
            params = [user_id]
            
            if status:
                query += " AND wr.status = ?"
                params.append(status)
            
            query += " ORDER BY wr.created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def get_pending_withdrawal_requests(self, limit=50):
        """Get all pending withdrawal requests"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT wr.*, u.username, u.first_name, u.last_name, u.phone_number,
                       pm.method_name, pm.type, pm.provider
                FROM withdrawal_requests wr
                JOIN users u ON wr.user_id = u.user_id
                JOIN payment_methods pm ON wr.method_id = pm.id
                WHERE wr.status = 'pending'
                ORDER BY wr.created_at ASC
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()
    
    # ==================== GAME METHODS ====================
    
    def create_game(self, game_code):
        """Create new game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO games (game_code)
                VALUES (?)
            ''', (game_code,))
            conn.commit()
            return cursor.lastrowid
    
    # ==================== STATISTICS METHODS ====================
    
    def get_system_stats(self):
        """Get overall system statistics"""
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
            
            # Today's volume
            cursor.execute('''
                SELECT COALESCE(SUM(amount), 0) as total
                FROM transactions 
                WHERE date(created_at) = date('now') AND status = 'completed'
            ''')
            stats['today_volume'] = cursor.fetchone()['total']
            
            # Game stats
            cursor.execute('SELECT COUNT(*) as count FROM games')
            stats['total_games'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM games WHERE status = "completed"')
            stats['completed_games'] = cursor.fetchone()['count']
            
            return stats
    
    def get_top_users(self, by='balance', limit=10):
        """Get top users by balance, games played, or games won"""
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
            
            return cursor.fetchall()