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
            
            # Games table (new)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT DEFAULT 'active',
                    winner TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_active_games_user_id ON active_games(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_requests_user_id ON payment_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_user_id ON withdrawal_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_games_status ON games(status)')
            
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
    
    # ==================== GAME METHODS (NEW) ====================
    
    def create_game(self):
        """Create a new game and return its ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO games (status) VALUES ('active')")
            conn.commit()
            return cursor.lastrowid
    
    def end_game(self, game_id, winner=None):
        """Mark a game as ended, optionally record winner"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE games SET status='ended', winner=? WHERE id=?",
                (winner, game_id)
            )
            conn.commit()
    
    def get_active_game(self):
        """Return the most recent active game, or None"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM games WHERE status='active' ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_game(self, game_id):
        """Get game by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM games WHERE id=?", (game_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_last_game_id(self):
        """Get the highest game ID (useful for initializing next_game_id)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(id) as max_id FROM games")
            row = cursor.fetchone()
            return row['max_id'] if row and row['max_id'] else 0
    
    # ==================== USER METHODS ====================
    # ... (all existing user methods remain unchanged)
    # ... (only the new game methods added above)