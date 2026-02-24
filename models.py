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
                    welcome_bonus_amount INTEGER DEFAULT 1000, -- 10 ETB in cents
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
                    card_ids TEXT NOT NULL, -- JSON array of card IDs
                    stake INTEGER NOT NULL, -- Total wagered
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active', -- active, completed, won
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Rest of your tables...
            # (Add other tables as needed)
            
            conn.commit()
            logger.info("✅ Database tables created/verified")
    
    def get_or_create_user(self, user_id, username=None, first_name=None, last_name=None):
        """Get user and give welcome bonus if new"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if user exists
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            
            if user:
                return dict(user)
            
            # Create new user with welcome bonus
            welcome_bonus = 1000  # 10 ETB in cents
            
            cursor.execute('''
                INSERT INTO users 
                (user_id, username, first_name, last_name, balance, welcome_bonus_claimed)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, welcome_bonus, True))
            
            conn.commit()
            
            logger.info(f"✅ New user {user_id} created with {welcome_bonus/100} ETB welcome bonus")
            
            # Return new user data
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            return dict(cursor.fetchone())
    
    def get_user_balance(self, user_id):
        """Get user balance"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            return row['balance'] if row else 0
    
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
                
            except Exception as e:
                cursor.execute('ROLLBACK')
                logger.error(f"Database error: {e}")
                raise e
    
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
                SELECT SUM(stake) as total FROM active_games 
                WHERE user_id = ? AND status = 'active'
            ''', (user_id,))
            row = cursor.fetchone()
            return row['total'] if row and row['total'] else 0