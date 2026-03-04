import sqlite3
import json
import logging
import random
import string
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="bingo.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Initialize database tables with all required fields for the new bingo system"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Users table - with all fields for the new system
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    phone_number TEXT,
                    referral_code TEXT UNIQUE,
                    referred_by INTEGER,
                    has_deposited BOOLEAN DEFAULT 0,
                    balance INTEGER DEFAULT 1000,
                    games_played INTEGER DEFAULT 0,
                    games_won INTEGER DEFAULT 0,
                    total_deposits INTEGER DEFAULT 0,
                    total_withdrawals INTEGER DEFAULT 0,
                    referral_earnings INTEGER DEFAULT 0,
                    suspended_until TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (referred_by) REFERENCES users(user_id)
                )
            ''')
            
            # Transactions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    type TEXT,
                    description TEXT,
                    reference_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Payment methods table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_methods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    type TEXT,
                    account_number TEXT,
                    account_name TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Payment requests table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_requests (
                    request_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    method_id INTEGER,
                    amount INTEGER,
                    sender_phone TEXT,
                    status TEXT DEFAULT 'pending',
                    admin_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (method_id) REFERENCES payment_methods(id)
                )
            ''')
            
            # Payment proofs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_proofs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    proof_type TEXT,
                    proof_data TEXT,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (request_id) REFERENCES payment_requests(request_id)
                )
            ''')
            
            # Withdrawal requests table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS withdrawal_requests (
                    request_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    amount INTEGER,
                    phone_number TEXT,
                    status TEXT DEFAULT 'pending',
                    admin_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Referral bonuses table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS referral_bonuses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_id INTEGER,
                    amount INTEGER DEFAULT 500,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    paid_at TIMESTAMP,
                    FOREIGN KEY (referrer_id) REFERENCES users(user_id),
                    FOREIGN KEY (referred_id) REFERENCES users(user_id)
                )
            ''')
            
            # User cards table - stores all cards for each user per game
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    game_id INTEGER,
                    card_id INTEGER,
                    card_data TEXT,
                    marked_numbers TEXT DEFAULT '[]',
                    suspended BOOLEAN DEFAULT 0,
                    suspended_at TIMESTAMP,
                    won BOOLEAN DEFAULT 0,
                    won_at TIMESTAMP,
                    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Games table - tracks active games per room
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id INTEGER,
                    game_number INTEGER,
                    pattern_id TEXT,
                    status TEXT DEFAULT 'waiting',
                    prize_pool INTEGER DEFAULT 0,
                    total_cards_sold INTEGER DEFAULT 0,
                    called_numbers TEXT DEFAULT '[]',
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    winner_id INTEGER,
                    winning_card_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (winner_id) REFERENCES users(user_id)
                )
            ''')
            
            # Game players table - tracks which users are in which game
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS game_players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER,
                    user_id INTEGER,
                    cards TEXT DEFAULT '[]',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Patterns table - stores all win patterns
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS patterns (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    positions TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            
            # Suspension history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS suspension_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    game_id INTEGER,
                    card_id INTEGER,
                    reason TEXT,
                    suspended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Game settings table for room configuration
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS game_settings (
                    room_id INTEGER PRIMARY KEY,
                    card_price INTEGER DEFAULT 5000,
                    max_cards INTEGER DEFAULT 8,
                    auto_start_delay INTEGER DEFAULT 30,
                    auto_call_interval INTEGER DEFAULT 3,
                    house_percent REAL DEFAULT 0.20,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Insert default patterns if not exists
            cursor.execute("SELECT COUNT(*) FROM patterns")
            if cursor.fetchone()[0] == 0:
                patterns = [
                    ('any_row', 'Any Row', 'Complete any horizontal line of 5 numbers', 'rows'),
                    ('any_col', 'Any Column', 'Complete any vertical line of 5 numbers', 'cols'),
                    ('diag_tl', 'Diagonal TL→BR', 'Diagonal from top-left to bottom-right', 'diag_tl'),
                    ('diag_tr', 'Diagonal TR→BL', 'Diagonal from top-right to bottom-left', 'diag_tr'),
                    ('any_line', 'Any Line', 'Complete any row, column, or diagonal', 'any_line'),
                    ('full_house', 'Full House', 'Mark all 24 numbers plus FREE space', 'full_house'),
                    ('four_corners', 'Four Corners', 'Mark the four corner squares', 'four_corners'),
                    ('x_pattern', 'X Pattern', 'Complete both diagonals', 'x_pattern'),
                    ('plus_pattern', 'Plus Sign', 'Complete middle row and column', 'plus_pattern'),
                    ('t_pattern', 'T Pattern', 'Complete top row and middle column', 't_pattern'),
                    ('l_pattern', 'L Pattern', 'Complete left column and bottom row', 'l_pattern'),
                    ('u_pattern', 'U Pattern', 'Complete side columns and bottom row', 'u_pattern'),
                    ('frame', 'Frame', 'Mark all outer border numbers', 'frame'),
                    ('blackout', 'Blackout', 'Mark every single number', 'blackout'),
                    ('two_rows', 'Two Rows', 'Complete any 2 rows', 'two_rows'),
                    ('two_cols', 'Two Columns', 'Complete any 2 columns', 'two_cols'),
                    ('top_bottom', 'Top & Bottom', 'Complete top and bottom rows', 'top_bottom'),
                    ('center_col', 'Center Column', 'Complete the N column', 'center_col'),
                    ('b_o_cols', 'B & O Columns', 'Complete left and right columns', 'b_o_cols'),
                    ('six_pack', 'Six Pack', 'Complete any 2x3 block', 'six_pack')
                ]
                cursor.executemany("INSERT INTO patterns (id, name, description, positions) VALUES (?,?,?,?)", patterns)
            
            # Insert default game settings for rooms 1,2,3
            for room_id in [1, 2, 3]:
                cursor.execute('''
                    INSERT OR IGNORE INTO game_settings (room_id, card_price, max_cards, auto_start_delay, auto_call_interval, house_percent)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (room_id, 5000 if room_id == 1 else 10000 if room_id == 2 else 20000, 8, 30, 3, 0.20))
            
            # Insert default payment methods
            cursor.execute('''
                INSERT OR IGNORE INTO payment_methods (name, type, account_number, account_name, is_active)
                VALUES
                    ('Telebirr', 'mobile_money', '0982372677', 'MK Bingo', 1),
                    ('CBE Birr', 'mobile_money', '0982372677', 'MK Bingo', 1)
            ''')
            
            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_user ON payment_requests(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_user ON withdrawal_requests(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_cards_game ON user_cards(game_id, user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_cards_user ON user_cards(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_room ON games(room_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_players_game ON game_players(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_bonuses_status ON referral_bonuses(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_suspension_history_user ON suspension_history(user_id, expires_at)")
            
            conn.commit()
            logger.info("✅ Database initialized successfully with all tables for MK BINGO system")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            raise
        finally:
            conn.close()

    # ==================== USER METHODS ====================

    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user by ID"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def generate_referral_code(self, user_id: int) -> str:
        """Generate a unique referral code for a user"""
        random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        code = f"MK{user_id}{random_str}"
        return code[:12]

    def get_or_create_user(self, user_id: int, username=None, first_name=None, last_name=None, phone_number=None, referred_by=None) -> Dict:
        """Get existing user or create new one with welcome bonus"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()

            if row:
                user = dict(row)
                if phone_number and not user.get('phone_number'):
                    cursor.execute("UPDATE users SET phone_number = ? WHERE user_id = ?", (phone_number, user_id))
                    conn.commit()
                    user['phone_number'] = phone_number
                return user
            else:
                # Generate referral code
                referral_code = self.generate_referral_code(user_id)
                
                # Create new user with welcome bonus
                cursor.execute('''
                    INSERT INTO users (
                        user_id, username, first_name, last_name, 
                        phone_number, balance, referral_code, referred_by, has_deposited
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, username, first_name, last_name, phone_number, 1000, referral_code, referred_by, 0))
                conn.commit()

                # Log welcome bonus transaction
                cursor.execute('''
                    INSERT INTO transactions (user_id, amount, type, description)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, 1000, 'bonus', 'Welcome bonus'))
                conn.commit()

                # If this user was referred, create pending referral bonus
                if referred_by:
                    self.create_pending_referral_bonus(referred_by, user_id)

                return self.get_user(user_id)
        finally:
            conn.close()

    def update_user_phone(self, user_id: int, phone_number: str) -> bool:
        """Update user phone number"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET phone_number = ? WHERE user_id = ?", (phone_number, user_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def update_user_suspension(self, user_id: int, suspended_until: datetime = None) -> bool:
        """Update user suspension status"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET suspended_until = ? WHERE user_id = ?", (suspended_until, user_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def is_user_suspended(self, user_id: int) -> bool:
        """Check if user is currently suspended"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT suspended_until FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row and row['suspended_until']:
                suspended_until = datetime.fromisoformat(row['suspended_until'])
                return datetime.now() < suspended_until
            return False
        finally:
            conn.close()

    # ==================== BALANCE METHODS ====================

    def update_balance(self, user_id: int, amount: int, transaction_type: str, description: str = None) -> Optional[Dict]:
        """Atomically update user balance and record transaction."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()

            # Atomically update balance using SQL addition
            cursor.execute("UPDATE users SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (amount, user_id))
            if cursor.rowcount == 0:
                return None

            # Retrieve new balance
            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            new_balance_row = cursor.fetchone()
            if not new_balance_row:
                return None
            new_balance = new_balance_row['balance']

            # Calculate old balance (new - amount)
            old_balance = new_balance - amount

            # Record transaction
            cursor.execute('''
                INSERT INTO transactions (user_id, amount, type, description)
                VALUES (?, ?, ?, ?)
            ''', (user_id, amount, transaction_type, description))

            # Update totals based on transaction type
            if amount > 0 and transaction_type == 'deposit':
                cursor.execute("UPDATE users SET total_deposits = total_deposits + ? WHERE user_id = ?", (amount, user_id))
            elif amount < 0 and transaction_type == 'withdrawal':
                cursor.execute("UPDATE users SET total_withdrawals = total_withdrawals + ? WHERE user_id = ?", (abs(amount), user_id))
            elif amount > 0 and transaction_type == 'game_win':
                cursor.execute("UPDATE users SET games_won = games_won + 1 WHERE user_id = ?", (user_id,))

            conn.commit()

            return {
                'old_balance': old_balance,
                'new_balance': new_balance,
                'amount': amount
            }
        except sqlite3.Error as e:
            logger.error(f"Database error in update_balance: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

    # ==================== CARD METHODS ====================

    def save_card_status(self, user_id: int, game_id: int, card_id: int, marked_numbers: list, suspended: bool = False, won: bool = False) -> bool:
        """Save card status to database"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Check if card exists
            cursor.execute(
                "SELECT id FROM user_cards WHERE user_id = ? AND game_id = ? AND card_id = ?",
                (user_id, game_id, card_id)
            )
            existing = cursor.fetchone()
            
            now = datetime.now()
            
            if existing:
                cursor.execute('''
                    UPDATE user_cards 
                    SET marked_numbers = ?, suspended = ?, won = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND game_id = ? AND card_id = ?
                ''', (json.dumps(marked_numbers), suspended, won, user_id, game_id, card_id))
            else:
                cursor.execute('''
                    INSERT INTO user_cards (user_id, game_id, card_id, marked_numbers, suspended, won)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (user_id, game_id, card_id, json.dumps(marked_numbers), suspended, won))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving card status: {e}")
            return False
        finally:
            conn.close()

    def get_user_cards(self, user_id: int, game_id: int) -> List[Dict]:
        """Get all cards for user in a game"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM user_cards 
                WHERE user_id = ? AND game_id = ?
                ORDER BY purchased_at DESC
            ''', (user_id, game_id))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_active_cards(self, user_id: int, game_id: int) -> List[Dict]:
        """Get active (non-suspended, non-won) cards for user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM user_cards 
                WHERE user_id = ? AND game_id = ? AND suspended = 0 AND won = 0
                ORDER BY purchased_at DESC
            ''', (user_id, game_id))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_suspension_status(self, user_id: int, game_id: int, suspended_cards: List[int], reason: str = "false_bingo") -> bool:
        """Update which cards are suspended for a user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            now = datetime.now()
            expires_at = datetime.now()  # Will be set by game manager
            
            for card_id in suspended_cards:
                cursor.execute('''
                    UPDATE user_cards 
                    SET suspended = 1, suspended_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND game_id = ? AND card_id = ?
                ''', (user_id, game_id, card_id))
                
                # Log suspension
                cursor.execute('''
                    INSERT INTO suspension_history (user_id, game_id, card_id, reason, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, game_id, card_id, reason, expires_at))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating suspension: {e}")
            return False
        finally:
            conn.close()

    def clear_suspensions(self, user_id: int, game_id: int) -> bool:
        """Clear all suspensions for a user in a game (at round end)"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE user_cards 
                SET suspended = 0, suspended_at = NULL
                WHERE user_id = ? AND game_id = ?
            ''', (user_id, game_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error clearing suspensions: {e}")
            return False
        finally:
            conn.close()

    # ==================== GAME METHODS ====================

    def create_game(self, room_id: int, pattern_id: str) -> Optional[int]:
        """Create a new game record"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO games (room_id, game_number, pattern_id, status)
                VALUES (?, (SELECT COALESCE(MAX(game_number), 0) + 1 FROM games WHERE room_id = ?), ?, 'waiting')
            ''', (room_id, room_id, pattern_id))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error creating game: {e}")
            return None
        finally:
            conn.close()

    def get_game(self, game_id: int) -> Optional[Dict]:
        """Get game by ID"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM games WHERE id = ?", (game_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def update_game_status(self, game_id: int, status: str, **kwargs) -> bool:
        """Update game status"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            updates = []
            values = []
            
            if status:
                updates.append("status = ?")
                values.append(status)
            
            if 'prize_pool' in kwargs:
                updates.append("prize_pool = ?")
                values.append(kwargs['prize_pool'])
            
            if 'total_cards_sold' in kwargs:
                updates.append("total_cards_sold = ?")
                values.append(kwargs['total_cards_sold'])
            
            if 'called_numbers' in kwargs:
                updates.append("called_numbers = ?")
                values.append(json.dumps(kwargs['called_numbers']))
            
            if 'started_at' in kwargs and kwargs['started_at']:
                updates.append("started_at = CURRENT_TIMESTAMP")
            
            if 'ended_at' in kwargs and kwargs['ended_at']:
                updates.append("ended_at = CURRENT_TIMESTAMP")
            
            if 'winner_id' in kwargs:
                updates.append("winner_id = ?")
                values.append(kwargs['winner_id'])
            
            if 'winning_card_id' in kwargs:
                updates.append("winning_card_id = ?")
                values.append(kwargs['winning_card_id'])
            
            if not updates:
                return True
            
            values.append(game_id)
            query = f"UPDATE games SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, values)
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating game: {e}")
            return False
        finally:
            conn.close()

    def add_player_to_game(self, game_id: int, user_id: int, card_ids: List[int]) -> bool:
        """Add player to game with their cards"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO game_players (game_id, user_id, cards)
                VALUES (?, ?, ?)
            ''', (game_id, user_id, json.dumps(card_ids)))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding player to game: {e}")
            return False
        finally:
            conn.close()

    def get_game_players(self, game_id: int) -> List[Dict]:
        """Get all players in a game"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT gp.*, u.first_name, u.username
                FROM game_players gp
                JOIN users u ON gp.user_id = u.user_id
                WHERE gp.game_id = ?
            ''', (game_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_active_game_for_user(self, user_id: int, room_id: int) -> Optional[Dict]:
        """Get active game for user in a specific room"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT g.* FROM games g
                JOIN game_players gp ON g.id = gp.game_id
                WHERE gp.user_id = ? AND g.room_id = ? AND g.status IN ('waiting', 'active')
                ORDER BY g.created_at DESC LIMIT 1
            ''', (user_id, room_id))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    # ==================== GAME SETTINGS METHODS ====================

    def get_game_settings(self, room_id: int) -> Dict:
        """Get settings for a specific room"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM game_settings WHERE room_id = ?", (room_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            # Return defaults if not found
            return {
                'room_id': room_id,
                'card_price': 5000 if room_id == 1 else 10000 if room_id == 2 else 20000,
                'max_cards': 8,
                'auto_start_delay': 30,
                'auto_call_interval': 3,
                'house_percent': 0.20
            }
        finally:
            conn.close()

    def update_game_settings(self, room_id: int, **kwargs) -> bool:
        """Update game settings for a room"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            updates = []
            values = []
            
            if 'card_price' in kwargs:
                updates.append("card_price = ?")
                values.append(kwargs['card_price'])
            
            if 'max_cards' in kwargs:
                updates.append("max_cards = ?")
                values.append(kwargs['max_cards'])
            
            if 'auto_start_delay' in kwargs:
                updates.append("auto_start_delay = ?")
                values.append(kwargs['auto_start_delay'])
            
            if 'auto_call_interval' in kwargs:
                updates.append("auto_call_interval = ?")
                values.append(kwargs['auto_call_interval'])
            
            if 'house_percent' in kwargs:
                updates.append("house_percent = ?")
                values.append(kwargs['house_percent'])
            
            if not updates:
                return True
            
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(room_id)
            
            query = f"UPDATE game_settings SET {', '.join(updates)} WHERE room_id = ?"
            cursor.execute(query, values)
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating game settings: {e}")
            return False
        finally:
            conn.close()

    # ==================== PAYMENT METHODS ====================

    def get_payment_methods(self, type: str = None, active_only: bool = True) -> List[Dict]:
        """Get payment methods"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            query = "SELECT * FROM payment_methods WHERE 1=1"
            params = []

            if type:
                query += " AND type = ?"
                params.append(type)

            if active_only:
                query += " AND is_active = 1"

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def create_payment_request(self, user_id: int, method_id: int, amount: int, sender_phone: str) -> str:
        """Create a payment request"""
        import uuid
        request_id = str(uuid.uuid4())[:8].upper()

        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO payment_requests (request_id, user_id, method_id, amount, sender_phone)
                VALUES (?, ?, ?, ?, ?)
            ''', (request_id, user_id, method_id, amount, sender_phone))
            conn.commit()
            return request_id
        finally:
            conn.close()

    def get_payment_request(self, request_id: str) -> Optional[Dict]:
        """Get payment request by ID"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT pr.*, u.first_name, u.username
                FROM payment_requests pr
                JOIN users u ON pr.user_id = u.user_id
                WHERE pr.request_id = ?
            ''', (request_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def get_user_payment_requests(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Get payment requests for a user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM payment_requests
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ''', (user_id, limit))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_pending_payment_requests(self, limit: int = 20) -> List[Dict]:
        """Get all pending payment requests"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT pr.*, u.first_name, u.username, u.phone_number
                FROM payment_requests pr
                JOIN users u ON pr.user_id = u.user_id
                WHERE pr.status = 'pending'
                ORDER BY pr.created_at ASC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_payment_request_status(self, request_id: str, status: str, admin_notes: str = None) -> bool:
        """Update payment request status"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE payment_requests
                SET status = ?, admin_notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
            ''', (status, admin_notes, request_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def add_payment_proof(self, request_id: str, proof_type: str, proof_data: str) -> bool:
        """Add payment proof"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO payment_proofs (request_id, proof_type, proof_data)
                VALUES (?, ?, ?)
            ''', (request_id, proof_type, proof_data))
            conn.commit()
            return True
        finally:
            conn.close()

    # ==================== REFERRAL METHODS ====================

    def create_pending_referral_bonus(self, referrer_id: int, referred_id: int):
        """Create a pending referral bonus entry"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Check if this referral was already recorded
            cursor.execute(
                "SELECT id FROM referral_bonuses WHERE referrer_id = ? AND referred_id = ?",
                (referrer_id, referred_id)
            )
            if cursor.fetchone():
                return False
            
            # Add pending referral bonus
            cursor.execute('''
                INSERT INTO referral_bonuses (referrer_id, referred_id, amount, status)
                VALUES (?, ?, ?, 'pending')
            ''', (referrer_id, referred_id, 500))
            
            conn.commit()
            logger.info(f"Pending referral bonus created: {referrer_id} -> {referred_id}")
            return True
        except Exception as e:
            logger.error(f"Error creating pending referral bonus: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def check_and_pay_referral_bonus(self, user_id: int) -> bool:
        """Check if user was referred and pay bonus to referrer after first deposit"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get user's referrer
            cursor.execute("SELECT referred_by, has_deposited FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if not result or not result['referred_by']:
                return False
            
            # Check if user already has deposited before
            if result['has_deposited']:
                return False
            
            referrer_id = result['referred_by']
            
            # Check if there's a pending bonus for this referral
            cursor.execute('''
                SELECT id FROM referral_bonuses 
                WHERE referrer_id = ? AND referred_id = ? AND status = 'pending'
            ''', (referrer_id, user_id))
            
            bonus = cursor.fetchone()
            if not bonus:
                return False
            
            # Mark user as having deposited
            cursor.execute(
                "UPDATE users SET has_deposited = 1 WHERE user_id = ?",
                (user_id,)
            )
            
            # Update bonus status to paid
            cursor.execute('''
                UPDATE referral_bonuses 
                SET status = 'paid', paid_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (bonus['id'],))
            
            # Add bonus to referrer's balance
            cursor.execute(
                "UPDATE users SET balance = balance + 500, referral_earnings = referral_earnings + 500 WHERE user_id = ?",
                (referrer_id,)
            )
            
            # Log transaction for referrer
            cursor.execute('''
                INSERT INTO transactions (user_id, amount, type, description)
                VALUES (?, ?, ?, ?)
            ''', (referrer_id, 500, 'referral_bonus', f'Referral bonus for user {user_id} (after deposit)'))
            
            conn.commit()
            logger.info(f"Referral bonus paid: {referrer_id} received 5 ETB for {user_id}'s deposit")
            return True
        except Exception as e:
            logger.error(f"Error paying referral bonus: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def get_user_by_referral_code(self, code: str) -> Optional[Dict]:
        """Get user by referral code"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE referral_code = ?", (code,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def get_referral_stats(self, user_id: int) -> Dict:
        """Get detailed referral statistics for a user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Count total referrals
            cursor.execute(
                "SELECT COUNT(*) FROM users WHERE referred_by = ?",
                (user_id,)
            )
            total_referrals = cursor.fetchone()[0]
            
            # Count pending bonuses
            cursor.execute('''
                SELECT COUNT(*) FROM referral_bonuses 
                WHERE referrer_id = ? AND status = 'pending'
            ''', (user_id,))
            pending_bonuses = cursor.fetchone()[0]
            
            # Get total earnings from paid bonuses
            cursor.execute('''
                SELECT SUM(amount) FROM referral_bonuses 
                WHERE referrer_id = ? AND status = 'paid'
            ''', (user_id,))
            total_earnings = cursor.fetchone()[0] or 0
            
            # Get recent referrals with status
            cursor.execute('''
                SELECT 
                    u.user_id, 
                    u.first_name, 
                    u.username, 
                    u.created_at,
                    u.has_deposited,
                    rb.status,
                    rb.paid_at
                FROM users u
                LEFT JOIN referral_bonuses rb ON u.user_id = rb.referred_id AND rb.referrer_id = ?
                WHERE u.referred_by = ?
                ORDER BY u.created_at DESC
                LIMIT 10
            ''', (user_id, user_id))
            
            recent = []
            for row in cursor.fetchall():
                recent.append(dict(row))
            
            return {
                'total_referrals': total_referrals,
                'pending_bonuses': pending_bonuses,
                'total_earnings': total_earnings,
                'recent_referrals': recent
            }
        finally:
            conn.close()

    # ==================== WITHDRAWAL METHODS ====================

    def create_withdrawal_request(self, user_id: int, amount: int, phone_number: str) -> Optional[str]:
        """Create a withdrawal request"""
        import uuid
        request_id = str(uuid.uuid4())[:8].upper()

        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO withdrawal_requests (request_id, user_id, amount, phone_number, status)
                VALUES (?, ?, ?, ?, 'pending')
            ''', (request_id, user_id, amount, phone_number))
            conn.commit()
            return request_id
        except sqlite3.Error as e:
            logger.error(f"Error creating withdrawal request: {e}")
            return None
        finally:
            conn.close()

    def get_withdrawal_request(self, request_id: str) -> Optional[Dict]:
        """Get withdrawal request by ID"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT wr.*, u.first_name, u.username
                FROM withdrawal_requests wr
                JOIN users u ON wr.user_id = u.user_id
                WHERE wr.request_id = ?
            ''', (request_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def get_pending_withdrawal_requests(self, limit: int = 20) -> List[Dict]:
        """Get all pending withdrawal requests"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT wr.*, u.first_name, u.username, u.phone_number
                FROM withdrawal_requests wr
                JOIN users u ON wr.user_id = u.user_id
                WHERE wr.status = 'pending'
                ORDER BY wr.created_at ASC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_withdrawal_request_status(self, request_id: str, status: str, admin_notes: str = None) -> bool:
        """Update withdrawal request status"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE withdrawal_requests
                SET status = ?, admin_notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
            ''', (status, admin_notes, request_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ==================== PATTERN METHODS ====================

    def get_patterns(self, active_only: bool = True) -> List[Dict]:
        """Get all patterns"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            if active_only:
                cursor.execute("SELECT * FROM patterns WHERE is_active = 1 ORDER BY id")
            else:
                cursor.execute("SELECT * FROM patterns ORDER BY id")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_pattern(self, pattern_id: str) -> Optional[Dict]:
        """Get pattern by ID"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    # ==================== BROADCAST / UTILITY METHODS ====================

    def get_all_user_ids(self):
        """Return list of all user IDs."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            rows = cursor.fetchall()
            return [row['user_id'] for row in rows]
        finally:
            conn.close()

    def get_active_users_count(self) -> int:
        """Get count of users active in the last 24 hours"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(DISTINCT user_id) FROM transactions 
                WHERE created_at > datetime('now', '-1 day')
            ''')
            return cursor.fetchone()[0]
        finally:
            conn.close()

    # ==================== SYSTEM STATS ====================

    def get_system_stats(self) -> Dict:
        """Get system statistics"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(balance) FROM users")
            total_balance = cursor.fetchone()[0] or 0

            cursor.execute("SELECT SUM(amount) FROM transactions WHERE amount > 0 AND type = 'deposit'")
            total_deposits = cursor.fetchone()[0] or 0

            cursor.execute("SELECT SUM(amount) FROM transactions WHERE amount < 0 AND type = 'withdrawal'")
            total_withdrawals = abs(cursor.fetchone()[0] or 0)

            cursor.execute("SELECT COUNT(DISTINCT game_id) FROM games WHERE status = 'active'")
            active_games = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM games WHERE status = 'active'")
            active_rooms = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM referral_bonuses WHERE status = 'pending'")
            pending_referrals = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(amount) FROM referral_bonuses WHERE status = 'paid'")
            total_referral_paid = cursor.fetchone()[0] or 0
            
            cursor.execute("SELECT COUNT(*) FROM suspension_history WHERE expires_at > datetime('now')")
            active_suspensions = cursor.fetchone()[0]

            return {
                'total_users': total_users,
                'total_balance': total_balance,
                'total_deposits': total_deposits,
                'total_withdrawals': total_withdrawals,
                'active_games': active_games,
                'active_rooms': active_rooms,
                'pending_referrals': pending_referrals,
                'total_referral_paid': total_referral_paid,
                'active_suspensions': active_suspensions
            }
        finally:
            conn.close()