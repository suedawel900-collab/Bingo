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
        """Initialize database tables with all required fields"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Users table
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
                    approved_by INTEGER,
                    approved_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (method_id) REFERENCES payment_methods(id),
                    FOREIGN KEY (approved_by) REFERENCES users(user_id)
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
                    approved_by INTEGER,
                    approved_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (approved_by) REFERENCES users(user_id)
                )
            ''')
            
            # Payment history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    type TEXT,
                    method TEXT,
                    txid TEXT,
                    status TEXT,
                    approved_by INTEGER,
                    approved_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (approved_by) REFERENCES users(user_id)
                )
            ''')
            
            # Game history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS game_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER,
                    room_id INTEGER,
                    user_id INTEGER,
                    card_id INTEGER,
                    won BOOLEAN DEFAULT 0,
                    prize_amount INTEGER DEFAULT 0,
                    pattern TEXT,
                    called_numbers TEXT,
                    marked_numbers TEXT,
                    winning_number INTEGER,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
            
            # User cards table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    game_id INTEGER,
                    card_id INTEGER,
                    card_data TEXT,
                    marked_numbers TEXT DEFAULT '[]',
                    suspended BOOLEAN DEFAULT 0,
                    won BOOLEAN DEFAULT 0,
                    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Games table
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
                    winner_id INTEGER,
                    winning_card_id INTEGER,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (winner_id) REFERENCES users(user_id)
                )
            ''')
            
            # Game players table
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
            
            # Patterns table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS patterns (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
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
            
            # Game settings table
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
            
            # Insert default patterns
            cursor.execute("SELECT COUNT(*) FROM patterns")
            if cursor.fetchone()[0] == 0:
                patterns = [
                    ('any_row', 'Any Row', 'Complete any horizontal line'),
                    ('any_col', 'Any Column', 'Complete any vertical line'),
                    ('diag_tl', 'Diagonal TL→BR', 'Diagonal from top-left'),
                    ('diag_tr', 'Diagonal TR→BL', 'Diagonal from top-right'),
                    ('any_line', 'Any Line', 'Complete any row, column, or diagonal'),
                    ('full_house', 'Full House', 'Mark all numbers'),
                    ('four_corners', 'Four Corners', 'Mark the four corners'),
                    ('x_pattern', 'X Pattern', 'Complete both diagonals'),
                    ('plus_pattern', 'Plus Sign', 'Complete middle row and column'),
                    ('t_pattern', 'T Pattern', 'Complete top row and middle column'),
                    ('l_pattern', 'L Pattern', 'Complete left column and bottom row'),
                    ('u_pattern', 'U Pattern', 'Complete side columns and bottom row'),
                    ('frame', 'Frame', 'Mark all outer border numbers'),
                    ('blackout', 'Blackout', 'Mark every single number'),
                    ('two_rows', 'Two Rows', 'Complete any 2 rows'),
                    ('two_cols', 'Two Columns', 'Complete any 2 columns'),
                    ('top_bottom', 'Top & Bottom', 'Complete top and bottom rows'),
                    ('center_col', 'Center Column', 'Complete the middle column'),
                    ('b_o_cols', 'B & O Columns', 'Complete left and right columns'),
                    ('six_pack', 'Six Pack', 'Complete any 2x3 block')
                ]
                cursor.executemany("INSERT INTO patterns (id, name, description) VALUES (?,?,?)", patterns)
            
            # Insert default game settings
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
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_user ON payment_requests(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_history_user ON payment_history(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_history_status ON payment_history(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_history_user ON game_history(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_history_game ON game_history(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_user ON withdrawal_requests(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_cards_game ON user_cards(game_id, user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_cards_user ON user_cards(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_room ON games(room_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_players_game ON game_players(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_bonuses_status ON referral_bonuses(status)")
            
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
        """Generate a unique referral code"""
        random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        code = f"MK{user_id}{random_str}"
        return code[:12]

    def get_or_create_user(self, user_id: int, username=None, first_name=None, last_name=None, phone_number=None, referred_by=None) -> Dict:
        """Get existing user or create new one"""
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
                referral_code = self.generate_referral_code(user_id)
                
                cursor.execute('''
                    INSERT INTO users (
                        user_id, username, first_name, last_name, 
                        phone_number, balance, referral_code, referred_by, has_deposited
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, username, first_name, last_name, phone_number, 1000, referral_code, referred_by, 0))
                conn.commit()

                cursor.execute('''
                    INSERT INTO transactions (user_id, amount, type, description)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, 1000, 'bonus', 'Welcome bonus'))
                conn.commit()

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

    # ==================== BALANCE METHODS ====================

    def update_balance(self, user_id: int, amount: int, transaction_type: str, description: str = None) -> Optional[Dict]:
        """Update user balance"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute("UPDATE users SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (amount, user_id))
            if cursor.rowcount == 0:
                return None

            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            new_balance_row = cursor.fetchone()
            if not new_balance_row:
                return None
            new_balance = new_balance_row['balance']

            old_balance = new_balance - amount

            cursor.execute('''
                INSERT INTO transactions (user_id, amount, type, description)
                VALUES (?, ?, ?, ?)
            ''', (user_id, amount, transaction_type, description))

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

    # ==================== PAYMENT HISTORY METHODS ====================

    def save_payment_record(self, user_id: int, amount: int, method: str, txid: str, status: str, approved_by: int = None) -> bool:
        """Save payment record to history"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO payment_history (user_id, amount, type, method, txid, status, approved_by, approved_at)
                VALUES (?, ?, 'deposit', ?, ?, ?, ?, 
                        CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE NULL END)
            ''', (user_id, amount, method, txid, status, approved_by, approved_by))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving payment record: {e}")
            return False
        finally:
            conn.close()

    def save_withdrawal_record(self, user_id: int, amount: int, method: str, phone: str, status: str, approved_by: int = None) -> bool:
        """Save withdrawal record to history"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO payment_history (user_id, amount, type, method, txid, status, approved_by, approved_at)
                VALUES (?, ?, 'withdrawal', ?, ?, ?, ?, 
                        CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE NULL END)
            ''', (user_id, amount, method, phone, status, approved_by, approved_by))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving withdrawal record: {e}")
            return False
        finally:
            conn.close()

    def get_payment_history(self, user_id: int = None, limit: int = 50) -> List[Dict]:
        """Get payment history"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute('''
                    SELECT ph.*, u.first_name, u.username 
                    FROM payment_history ph
                    JOIN users u ON ph.user_id = u.user_id
                    WHERE ph.user_id = ?
                    ORDER BY ph.created_at DESC
                    LIMIT ?
                ''', (user_id, limit))
            else:
                cursor.execute('''
                    SELECT ph.*, u.first_name, u.username 
                    FROM payment_history ph
                    JOIN users u ON ph.user_id = u.user_id
                    ORDER BY ph.created_at DESC
                    LIMIT ?
                ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    # ==================== GAME HISTORY METHODS ====================

    def save_game_result(self, user_id: int, game_id: int, card_id: int, won: bool, 
                         prize_amount: int = 0, pattern: str = None, 
                         called_numbers: List[int] = None, marked_numbers: List[int] = None,
                         winning_number: int = None, reason: str = None) -> bool:
        """Save game result to history"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Get room_id from game
            cursor.execute("SELECT room_id FROM games WHERE id = ?", (game_id,))
            game = cursor.fetchone()
            room_id = game['room_id'] if game else 1
            
            cursor.execute('''
                INSERT INTO game_history 
                (game_id, room_id, user_id, card_id, won, prize_amount, pattern, 
                 called_numbers, marked_numbers, winning_number, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                game_id, room_id, user_id, card_id, won, prize_amount, pattern,
                json.dumps(called_numbers) if called_numbers else None,
                json.dumps(marked_numbers) if marked_numbers else None,
                winning_number, reason
            ))
            conn.commit()
            
            # Update user stats
            cursor.execute("UPDATE users SET games_played = games_played + 1 WHERE user_id = ?", (user_id,))
            if won:
                cursor.execute("UPDATE users SET games_won = games_won + 1 WHERE user_id = ?", (user_id,))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving game result: {e}")
            return False
        finally:
            conn.close()

    def get_game_history(self, user_id: int = None, game_id: int = None, limit: int = 50) -> List[Dict]:
        """Get game history"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute('''
                    SELECT gh.*, u.first_name, u.username 
                    FROM game_history gh
                    JOIN users u ON gh.user_id = u.user_id
                    WHERE gh.user_id = ?
                    ORDER BY gh.created_at DESC
                    LIMIT ?
                ''', (user_id, limit))
            elif game_id:
                cursor.execute('''
                    SELECT gh.*, u.first_name, u.username 
                    FROM game_history gh
                    JOIN users u ON gh.user_id = u.user_id
                    WHERE gh.game_id = ?
                    ORDER BY gh.created_at DESC
                    LIMIT ?
                ''', (game_id, limit))
            else:
                cursor.execute('''
                    SELECT gh.*, u.first_name, u.username 
                    FROM game_history gh
                    JOIN users u ON gh.user_id = u.user_id
                    ORDER BY gh.created_at DESC
                    LIMIT ?
                ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_game_stats(self) -> Dict:
        """Get game statistics"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM game_history")
            total_games_played = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM game_history WHERE won = 1")
            total_wins = cursor.fetchone()[0]
            
            cursor.execute("SELECT SUM(prize_amount) FROM game_history WHERE won = 1")
            total_prizes = cursor.fetchone()[0] or 0
            
            cursor.execute("SELECT COUNT(*) FROM game_history WHERE reason = 'false_bingo'")
            total_false_bingos = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM game_history WHERE reason = 'too_late'")
            total_too_late = cursor.fetchone()[0]
            
            return {
                'total_games_played': total_games_played,
                'total_wins': total_wins,
                'total_prizes': total_prizes,
                'total_false_bingos': total_false_bingos,
                'total_too_late': total_too_late
            }
        finally:
            conn.close()

    # ==================== GAME METHODS ====================

    def get_active_games_count(self, user_id: int) -> int:
        """Get count of active games for user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM game_players WHERE user_id = ?", (user_id,))
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def get_total_stake(self, user_id: int) -> int:
        """Get total stake for user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(g.prize_pool) FROM games g JOIN game_players gp ON g.id = gp.game_id WHERE gp.user_id = ? AND g.status = 'active'", (user_id,))
            result = cursor.fetchone()[0]
            return result or 0
        finally:
            conn.close()

    def add_active_game(self, user_id: int, game_id: int, card_ids: List[int], stake: int) -> bool:
        """Add active game for user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO game_players (user_id, game_id, cards)
                VALUES (?, ?, ?)
            ''', (user_id, game_id, json.dumps(card_ids)))
            conn.commit()
            return True
        finally:
            conn.close()

    # ==================== CARD METHODS ====================

    def save_card_status(self, user_id: int, game_id: int, card_id: int, marked_numbers: list, suspended: bool = False, won: bool = False) -> bool:
        """Save card status to database"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT id FROM user_cards WHERE user_id = ? AND game_id = ? AND card_id = ?",
                (user_id, game_id, card_id)
            )
            existing = cursor.fetchone()
            
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
        """Get active cards for user"""
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
            
            for card_id in suspended_cards:
                cursor.execute('''
                    UPDATE user_cards 
                    SET suspended = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND game_id = ? AND card_id = ?
                ''', (user_id, game_id, card_id))
                
                cursor.execute('''
                    INSERT INTO suspension_history (user_id, game_id, card_id, reason)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, game_id, card_id, reason))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating suspension: {e}")
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
            
            # Save to history
            method = self.get_payment_methods()[0]['name'] if method_id == 1 else "CBE Birr"
            self.save_payment_record(user_id, amount, method, request_id, 'pending')
            
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

    def update_payment_request_status(self, request_id: str, status: str, admin_notes: str = None, approved_by: int = None) -> bool:
        """Update payment request status"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE payment_requests
                SET status = ?, admin_notes = ?, updated_at = CURRENT_TIMESTAMP,
                    approved_by = ?, approved_at = CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE request_id = ?
            ''', (status, admin_notes, approved_by, status, request_id))
            conn.commit()
            
            # Update history
            cursor.execute('''
                UPDATE payment_history
                SET status = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP
                WHERE txid = ?
            ''', (status, approved_by, request_id))
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
            
            # Save to history
            self.save_withdrawal_record(user_id, amount, "Telebirr", phone_number, 'pending')
            
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

    def update_withdrawal_request_status(self, request_id: str, status: str, admin_notes: str = None, approved_by: int = None) -> bool:
        """Update withdrawal request status"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE withdrawal_requests
                SET status = ?, admin_notes = ?, updated_at = CURRENT_TIMESTAMP,
                    approved_by = ?, approved_at = CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE request_id = ?
            ''', (status, admin_notes, approved_by, status, request_id))
            conn.commit()
            
            # Update history
            cursor.execute('''
                UPDATE payment_history
                SET status = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP
                WHERE txid = ?
            ''', (status, approved_by, request_id))
            conn.commit()
            
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_withdrawal_history(self, user_id: int = None, limit: int = 50) -> List[Dict]:
        """Get withdrawal history"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            if user_id:
                cursor.execute('''
                    SELECT * FROM payment_history 
                    WHERE user_id = ? AND type = 'withdrawal'
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (user_id, limit))
            else:
                cursor.execute('''
                    SELECT ph.*, u.first_name, u.username 
                    FROM payment_history ph
                    JOIN users u ON ph.user_id = u.user_id
                    WHERE ph.type = 'withdrawal'
                    ORDER BY ph.created_at DESC
                    LIMIT ?
                ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    # ==================== REFERRAL METHODS ====================

    def create_pending_referral_bonus(self, referrer_id: int, referred_id: int):
        """Create a pending referral bonus entry"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT id FROM referral_bonuses WHERE referrer_id = ? AND referred_id = ?",
                (referrer_id, referred_id)
            )
            if cursor.fetchone():
                return False
            
            cursor.execute('''
                INSERT INTO referral_bonuses (referrer_id, referred_id, amount, status)
                VALUES (?, ?, ?, 'pending')
            ''', (referrer_id, referred_id, 500))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error creating referral bonus: {e}")
            return False
        finally:
            conn.close()

    def check_and_pay_referral_bonus(self, user_id: int) -> bool:
        """Check if user was referred and pay bonus"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute("SELECT referred_by, has_deposited FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if not result or not result['referred_by']:
                return False
            
            if result['has_deposited']:
                return False
            
            referrer_id = result['referred_by']
            
            cursor.execute('''
                SELECT id FROM referral_bonuses 
                WHERE referrer_id = ? AND referred_id = ? AND status = 'pending'
            ''', (referrer_id, user_id))
            
            bonus = cursor.fetchone()
            if not bonus:
                return False
            
            cursor.execute("UPDATE users SET has_deposited = 1 WHERE user_id = ?", (user_id,))
            
            cursor.execute('''
                UPDATE referral_bonuses 
                SET status = 'paid', paid_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (bonus['id'],))
            
            cursor.execute("UPDATE users SET balance = balance + 500, referral_earnings = referral_earnings + 500 WHERE user_id = ?", (referrer_id,))
            
            cursor.execute('''
                INSERT INTO transactions (user_id, amount, type, description)
                VALUES (?, ?, ?, ?)
            ''', (referrer_id, 500, 'referral_bonus', f'Referral bonus for user {user_id}'))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error paying referral bonus: {e}")
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
        """Get referral statistics for a user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
            total_referrals = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(*) FROM referral_bonuses 
                WHERE referrer_id = ? AND status = 'pending'
            ''', (user_id,))
            pending_bonuses = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT SUM(amount) FROM referral_bonuses 
                WHERE referrer_id = ? AND status = 'paid'
            ''', (user_id,))
            total_earnings = cursor.fetchone()[0] or 0
            
            return {
                'total_referrals': total_referrals,
                'pending_bonuses': pending_bonuses,
                'total_earnings': total_earnings
            }
        finally:
            conn.close()

    # ==================== PATTERN METHODS ====================

    def get_patterns(self, active_only: bool = True) -> List[Dict]:
        """Get all patterns"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM patterns WHERE is_active = 1 ORDER BY id")
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

    # ==================== UTILITY METHODS ====================

    def get_all_user_ids(self):
        """Return list of all user IDs"""
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

            cursor.execute("SELECT COUNT(*) FROM games WHERE status = 'active'")
            active_games = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'pending'")
            pending_payments = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending'")
            pending_withdrawals = cursor.fetchone()[0]
            
            # Get game stats
            game_stats = self.get_game_stats()

            return {
                'total_users': total_users,
                'total_balance': total_balance,
                'total_deposits': total_deposits,
                'total_withdrawals': total_withdrawals,
                'active_games': active_games,
                'pending_payments': pending_payments,
                'pending_withdrawals': pending_withdrawals,
                'total_games': game_stats['total_games_played'],
                'total_winners': game_stats['total_wins'],
                'total_prizes': game_stats['total_prizes'],
                'total_false_bingos': game_stats['total_false_bingos'],
                'total_too_late': game_stats['total_too_late']
            }
        finally:
            conn.close()