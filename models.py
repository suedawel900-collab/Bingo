import os
import sqlite3
import json
import threading
from datetime import datetime
from contextlib import contextmanager

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
                    balance INTEGER DEFAULT 0,
                    total_deposits INTEGER DEFAULT 0,
                    total_withdrawals INTEGER DEFAULT 0,
                    games_played INTEGER DEFAULT 0,
                    games_won INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Transactions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    type TEXT CHECK(type IN ('deposit', 'withdrawal', 'game_fee', 'game_win')) NOT NULL,
                    status TEXT CHECK(status IN ('pending', 'completed', 'failed')) DEFAULT 'pending',
                    payment_intent_id TEXT UNIQUE,
                    description TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Games table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_code TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'waiting',
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
            
            conn.commit()
    
    # User methods
    def get_user(self, user_id):
        """Get user by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            return cursor.fetchone()
    
    def create_user(self, user_id, username=None, first_name=None, last_name=None, phone_number=None):
        """Create or update user"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, phone_number)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    phone_number = COALESCE(excluded.phone_number, phone_number),
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, username, first_name, last_name, phone_number))
            conn.commit()
    
    def update_balance(self, user_id, amount, transaction_type, payment_intent_id=None, description=None):
        """Update user balance with transaction"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('BEGIN TRANSACTION')
            
            try:
                # Get current balance
                cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                user = cursor.fetchone()
                
                if not user:
                    cursor.execute('ROLLBACK')
                    return None
                
                new_balance = user['balance'] + amount
                
                # Update user balance
                cursor.execute('''
                    UPDATE users 
                    SET balance = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                ''', (new_balance, user_id))
                
                # Create transaction record
                cursor.execute('''
                    INSERT INTO transactions 
                    (user_id, amount, type, status, payment_intent_id, description, completed_at)
                    VALUES (?, ?, ?, 'completed', ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, amount, transaction_type, payment_intent_id, description))
                
                transaction_id = cursor.lastrowid
                conn.commit()
                
                return {'new_balance': new_balance, 'transaction_id': transaction_id}
                
            except Exception as e:
                cursor.execute('ROLLBACK')
                raise e
    
    def get_user_transactions(self, user_id, limit=10):
        """Get user transaction history"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM transactions 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            return cursor.fetchall()
    
    def create_transaction(self, user_id, amount, type, payment_intent_id=None):
        """Create pending transaction"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO transactions (user_id, amount, type, status, payment_intent_id)
                VALUES (?, ?, ?, 'pending', ?)
            ''', (user_id, amount, type, payment_intent_id))
            conn.commit()
            return cursor.lastrowid
    
    def complete_transaction(self, payment_intent_id):
        """Complete transaction after successful payment"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE transactions 
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE payment_intent_id = ?
            ''', (payment_intent_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    # Game methods
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
            return cursor.fetchone()
    
    def join_game(self, game_id, user_id, card_numbers):
        """Add player to game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if already joined
            cursor.execute('''
                SELECT * FROM game_players 
                WHERE game_id = ? AND user_id = ?
            ''', (game_id, user_id))
            
            if cursor.fetchone():
                return None
            
            # Join game
            cursor.execute('''
                INSERT INTO game_players (game_id, user_id, card_numbers)
                VALUES (?, ?, ?)
            ''', (game_id, user_id, json.dumps(card_numbers)))
            
            # Update player count
            cursor.execute('''
                UPDATE games 
                SET player_count = player_count + 1
                WHERE id = ?
            ''', (game_id,))
            
            conn.commit()
            return cursor.lastrowid
    
    def mark_number(self, game_id, user_id, number):
        """Mark number on player's card"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get current marked numbers
            cursor.execute('''
                SELECT marked_numbers FROM game_players 
                WHERE game_id = ? AND user_id = ?
            ''', (game_id, user_id))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            marked = json.loads(result['marked_numbers'])
            if number not in marked:
                marked.append(number)
            
            # Update marked numbers
            cursor.execute('''
                UPDATE game_players 
                SET marked_numbers = ?
                WHERE game_id = ? AND user_id = ?
            ''', (json.dumps(marked), game_id, user_id))
            
            conn.commit()
            return marked
