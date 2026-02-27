# models.py
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
                    balance INTEGER DEFAULT 1000,
                    total_deposits INTEGER DEFAULT 0,
                    total_withdrawals INTEGER DEFAULT 0,
                    games_played INTEGER DEFAULT 0,
                    games_won INTEGER DEFAULT 0,
                    welcome_bonus_claimed BOOLEAN DEFAULT TRUE,
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
            
            cursor.execute('SELECT COUNT(*) as count FROM payment_methods')
            if cursor.fetchone()['count'] > 0:
                return
            
            default_methods = [
                ('TELBIRR', 'ቴሌቢር (Telbirr)', 'mobile_money', 'Ethio Telecom', 1000, 500000, 1,
                 '🔵 **ቴሌቢር ክፍያ / Telbirr Payment**\n\n'
                 '1. Dial *127# to access Telbirr menu\n'
                 '2. Select "Send Money"\n'
                 '3. Enter number **0953933030**\n'
                 '4. Enter amount\n'
                 '5. Enter your PIN\n'
                 '6. Save the reference number'),
                
                ('CBEBIRR', 'ሲቢኢ ቢር (CBE Birr)', 'mobile_money', 'CBE', 1000, 500000, 1,
                 '💚 **ሲቢኢ ቢር ክፍያ / CBE Birr Payment**\n\n'
                 '1. Dial *847# to access CBE Birr menu\n'
                 '2. Select "Send Money"\n'
                 '3. Enter number **0953933030**\n'
                 '4. Enter amount\n'
                 '5. Enter your PIN\n'
                 '6. Save the transaction ID')
            ]
            
            for method in default_methods:
                cursor.execute('''
                    INSERT INTO payment_methods 
                    (method_code, method_name, type, provider, min_amount, max_amount, is_active, instructions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', method)
            
            conn.commit()
            logger.info("✅ Default payment methods inserted")
    
    # User methods
    def get_or_create_user(self, user_id, username=None, first_name=None, last_name=None, phone_number=None):
        """Get existing user or create new one"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            
            if user:
                # Update user info if provided
                if username or first_name or last_name or phone_number:
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
                    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                    user = cursor.fetchone()
                return dict(user) if user else None
            
            # Create new user with welcome bonus
            cursor.execute('''
                INSERT INTO users 
                (user_id, username, first_name, last_name, phone_number, balance, welcome_bonus_claimed)
                VALUES (?, ?, ?, ?, ?, 1000, 1)
            ''', (user_id, username, first_name, last_name, phone_number))
            
            conn.commit()
            
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            return dict(user) if user else None
    
    def get_user(self, user_id):
        """Get user by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            return dict(user) if user else None
    
    def update_balance(self, user_id, amount, transaction_type, description=None):
        """Update user balance"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            
            if not user:
                return None
            
            new_balance = user['balance'] + amount
            
            cursor.execute('''
                UPDATE users 
                SET balance = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (new_balance, user_id))
            
            if transaction_type == 'game_fee':
                cursor.execute('''
                    UPDATE users 
                    SET games_played = games_played + 1
                    WHERE user_id = ?
                ''', (user_id,))
            
            if transaction_type == 'game_win':
                cursor.execute('''
                    UPDATE users 
                    SET games_won = games_won + 1
                    WHERE user_id = ?
                ''', (user_id,))
            
            cursor.execute('''
                INSERT INTO transactions 
                (user_id, amount, type, description, completed_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, amount, transaction_type, description, datetime.now()))
            
            conn.commit()
            
            return {
                'new_balance': new_balance,
                'transaction_id': cursor.lastrowid
            }
    
    def add_active_game(self, user_id, game_id, card_ids, stake):
        """Track active game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO active_games (user_id, game_id, card_ids, stake)
                VALUES (?, ?, ?, ?)
            ''', (user_id, game_id, json.dumps(card_ids), stake))
            conn.commit()
            return cursor.lastrowid
    
    def get_active_games_count(self, user_id):
        """Get number of active games"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) as count FROM active_games 
                WHERE user_id = ? AND status = 'active'
            ''', (user_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0
    
    def get_total_stake(self, user_id):
        """Get total stake"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COALESCE(SUM(stake), 0) as total FROM active_games 
                WHERE user_id = ? AND status = 'active'
            ''', (user_id,))
            row = cursor.fetchone()
            return row['total'] if row else 0
    
    def get_user_transactions(self, user_id, limit=10):
        """Get user transactions"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM transactions 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_system_stats(self):
        """Get system statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            cursor.execute('SELECT COUNT(*) as count FROM users')
            stats['total_users'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COALESCE(SUM(balance), 0) as total FROM users')
            stats['total_balance'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "deposit"')
            stats['total_deposits'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "withdrawal"')
            stats['total_withdrawals'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "game_fee"')
            stats['total_game_fees'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE type = "game_win"')
            stats['total_game_wins'] = cursor.fetchone()['total']
            
            cursor.execute('SELECT COUNT(*) as count FROM active_games WHERE status = "active"')
            stats['active_games'] = cursor.fetchone()['count']
            
            return stats