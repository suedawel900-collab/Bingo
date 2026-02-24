import os
import sqlite3
import json
import threading
from datetime import datetime
from contextlib import contextmanager
import logging

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
            
            # Payment intents table (for Stripe)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_intent_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT DEFAULT 'usd',
                    status TEXT DEFAULT 'pending',
                    client_secret TEXT,
                    checkout_session_id TEXT,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
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
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_intents_user_id ON payment_intents(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_intents_status ON payment_intents(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_game_players_game_id ON game_players(game_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_game_players_user_id ON game_players(user_id)')
            
            conn.commit()
            logger.info("✅ Database tables created/verified")
    
    # ==================== USER METHODS ====================
    
    def get_user(self, user_id):
        """Get user by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            return cursor.fetchone()
    
    def get_all_users(self, limit=100, offset=0):
        """Get all users with pagination"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM users 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            ''', (limit, offset))
            return cursor.fetchall()
    
    def get_user_count(self):
        """Get total number of users"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM users')
            return cursor.fetchone()['count']
    
    def create_user(self, user_id, username=None, first_name=None, last_name=None, phone_number=None):
        """Create or update user"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, phone_number)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, username),
                    first_name = COALESCE(excluded.first_name, first_name),
                    last_name = COALESCE(excluded.last_name, last_name),
                    phone_number = COALESCE(excluded.phone_number, phone_number),
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, username, first_name, last_name, phone_number))
            conn.commit()
            logger.info(f"✅ User {user_id} created/updated")
    
    def update_balance(self, user_id, amount, transaction_type, description=None, status='completed', payment_intent_id=None):
        """Update user balance with transaction
        
        Args:
            user_id: Telegram user ID
            amount: Amount in cents (positive for deposit/win, negative for withdrawal/fee)
            transaction_type: 'deposit', 'withdrawal', 'game_fee', 'game_win', 'refund', 'withdrawal_refund'
            description: Optional description
            status: 'pending', 'completed', 'failed', 'cancelled'
            payment_intent_id: Optional Stripe payment intent ID
        
        Returns:
            Dict with new_balance, transaction_id, status or None if failed
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('BEGIN TRANSACTION')
            
            try:
                # Get current balance with lock
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
                
                logger.info(f"✅ Transaction {transaction_id}: User {user_id}, {transaction_type}, ${amount/100:.2f}, Status: {status}")
                
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
    
    def get_transaction(self, transaction_id):
        """Get transaction by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM transactions WHERE id = ?', (transaction_id,))
            return cursor.fetchone()
    
    def get_transaction_by_payment_intent(self, payment_intent_id):
        """Get transaction by payment intent ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM transactions WHERE payment_intent_id = ?', (payment_intent_id,))
            return cursor.fetchone()
    
    def get_pending_withdrawals(self, limit=50):
        """Get all pending withdrawals"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.*, u.username, u.first_name, u.last_name 
                FROM transactions t
                JOIN users u ON t.user_id = u.user_id
                WHERE t.type = 'withdrawal' AND t.status = 'pending'
                ORDER BY t.created_at ASC
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()
    
    def get_all_transactions(self, limit=100, offset=0, status=None, type=None):
        """Get all transactions with filters"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM transactions WHERE 1=1"
            params = []
            
            if status:
                query += " AND status = ?"
                params.append(status)
            
            if type:
                query += " AND type = ?"
                params.append(type)
            
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def get_transaction_stats(self):
        """Get transaction statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Total deposits
            cursor.execute('''
                SELECT COALESCE(SUM(amount), 0) as total 
                FROM transactions 
                WHERE type = 'deposit' AND status = 'completed'
            ''')
            stats['total_deposits'] = cursor.fetchone()['total']
            
            # Total withdrawals
            cursor.execute('''
                SELECT COALESCE(SUM(amount), 0) as total 
                FROM transactions 
                WHERE type = 'withdrawal' AND status = 'completed'
            ''')
            stats['total_withdrawals'] = cursor.fetchone()['total']
            
            # Total game fees
            cursor.execute('''
                SELECT COALESCE(SUM(amount), 0) as total 
                FROM transactions 
                WHERE type = 'game_fee' AND status = 'completed'
            ''')
            stats['total_game_fees'] = cursor.fetchone()['total']
            
            # Total game wins
            cursor.execute('''
                SELECT COALESCE(SUM(amount), 0) as total 
                FROM transactions 
                WHERE type = 'game_win' AND status = 'completed'
            ''')
            stats['total_game_wins'] = cursor.fetchone()['total']
            
            # Pending withdrawals count
            cursor.execute('''
                SELECT COUNT(*) as count 
                FROM transactions 
                WHERE type = 'withdrawal' AND status = 'pending'
            ''')
            stats['pending_withdrawals'] = cursor.fetchone()['count']
            
            # Today's transactions
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total
                FROM transactions 
                WHERE date(created_at) = date('now')
            ''')
            row = cursor.fetchone()
            stats['today_count'] = row['count']
            stats['today_volume'] = row['total']
            
            return stats
    
    def update_transaction_status(self, transaction_id, status, completed_at=None):
        """Update transaction status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if completed_at:
                cursor.execute('''
                    UPDATE transactions 
                    SET status = ?, completed_at = ?
                    WHERE id = ?
                ''', (status, completed_at, transaction_id))
            else:
                cursor.execute('''
                    UPDATE transactions 
                    SET status = ?
                    WHERE id = ?
                ''', (status, transaction_id))
            
            conn.commit()
            return cursor.rowcount > 0
    
    # ==================== PAYMENT METHODS ====================
    
    def create_payment_intent(self, payment_intent_id, user_id, amount, client_secret=None, checkout_session_id=None):
        """Create payment intent record"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO payment_intents (payment_intent_id, user_id, amount, client_secret, checkout_session_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (payment_intent_id, user_id, amount, client_secret, checkout_session_id))
            conn.commit()
            return cursor.lastrowid
    
    def update_payment_intent_status(self, payment_intent_id, status):
        """Update payment intent status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE payment_intents
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE payment_intent_id = ?
            ''', (status, payment_intent_id))
            conn.commit()
            return cursor.rowcount > 0
    
    def get_payment_intent(self, payment_intent_id):
        """Get payment intent by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payment_intents WHERE payment_intent_id = ?', (payment_intent_id,))
            return cursor.fetchone()
    
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
            game_id = cursor.lastrowid
            logger.info(f"✅ Game {game_id} created with code {game_code}")
            return game_id
    
    def get_game(self, game_id):
        """Get game by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM games WHERE id = ?', (game_id,))
            return cursor.fetchone()
    
    def get_game_by_code(self, game_code):
        """Get game by code"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM games WHERE game_code = ?', (game_code,))
            return cursor.fetchone()
    
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
    
    def get_recent_games(self, limit=10):
        """Get recent games"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM games 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()
    
    def update_game_status(self, game_id, status):
        """Update game status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if status == 'active':
                cursor.execute('''
                    UPDATE games 
                    SET status = ?, started_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, game_id))
            elif status == 'completed':
                cursor.execute('''
                    UPDATE games 
                    SET status = ?, ended_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, game_id))
            else:
                cursor.execute('''
                    UPDATE games 
                    SET status = ?
                    WHERE id = ?
                ''', (status, game_id))
            
            conn.commit()
            return cursor.rowcount > 0
    
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
                logger.warning(f"User {user_id} already in game {game_id}")
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
            logger.info(f"✅ User {user_id} joined game {game_id}")
            return cursor.lastrowid
    
    def leave_game(self, game_id, user_id):
        """Remove player from game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM game_players 
                WHERE game_id = ? AND user_id = ?
            ''', (game_id, user_id))
            
            if cursor.rowcount > 0:
                cursor.execute('''
                    UPDATE games 
                    SET player_count = player_count - 1
                    WHERE id = ?
                ''', (game_id,))
                conn.commit()
                logger.info(f"User {user_id} left game {game_id}")
                return True
            
            return False
    
    def get_game_players(self, game_id):
        """Get all players in a game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT gp.*, u.username, u.first_name, u.last_name, u.balance
                FROM game_players gp
                JOIN users u ON gp.user_id = u.user_id
                WHERE gp.game_id = ?
            ''', (game_id,))
            return cursor.fetchall()
    
    def get_player_game(self, user_id, game_id):
        """Get player's game data"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM game_players 
                WHERE game_id = ? AND user_id = ?
            ''', (game_id, user_id))
            return cursor.fetchone()
    
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
    
    def update_game_called_numbers(self, game_id, called_numbers):
        """Update called numbers for game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE games 
                SET called_numbers = ?
                WHERE id = ?
            ''', (json.dumps(called_numbers), game_id))
            conn.commit()
    
    def declare_winner(self, game_id, user_id, prize_amount):
        """Declare winner for game"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Update player as winner
            cursor.execute('''
                UPDATE game_players 
                SET is_winner = TRUE
                WHERE game_id = ? AND user_id = ?
            ''', (game_id, user_id))
            
            # Update game
            cursor.execute('''
                UPDATE games 
                SET status = 'completed', 
                    prize_pool = ?,
                    ended_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (prize_amount, game_id))
            
            conn.commit()
            logger.info(f"✅ Game {game_id} winner: User {user_id}, Prize: ${prize_amount/100:.2f}")
            return True
    
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
            
            # Game stats
            cursor.execute('SELECT COUNT(*) as count FROM games')
            stats['total_games'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM games WHERE status = "completed"')
            stats['completed_games'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM game_players')
            stats['total_game_plays'] = cursor.fetchone()['count']
            
            # Transaction stats
            stats.update(self.get_transaction_stats())
            
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
    
    # ==================== MAINTENANCE METHODS ====================
    
    def vacuum(self):
        """Vacuum database to reclaim space"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('VACUUM')
            logger.info("✅ Database vacuum completed")
    
    def backup(self, backup_path):
        """Create database backup"""
        import shutil
        try:
            shutil.copy2(self.db_path, backup_path)
            logger.info(f"✅ Database backed up to {backup_path}")
            return True
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return False
    
    def execute_raw_query(self, query, params=None):
        """Execute raw SQL query (for admin use only)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            if query.strip().upper().startswith('SELECT'):
                return cursor.fetchall()
            else:
                conn.commit()
                return cursor.rowcount