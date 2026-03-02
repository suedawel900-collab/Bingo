import sqlite3
import json
import logging
import random
import string
from datetime import datetime
from typing import Optional, Dict, List, Any

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
        """Initialize database tables with referral system"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Users table - with referral fields
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
            
            # Referral bonuses table - tracks pending vs paid bonuses
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS referral_bonuses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_id INTEGER,
                    amount INTEGER DEFAULT 500,  # 5 ETB in cents
                    status TEXT DEFAULT 'pending',  # pending, paid, expired
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
                    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Active games table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS active_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    game_id INTEGER,
                    card_ids TEXT,
                    stake INTEGER,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Games table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY,
                    pattern_id INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'waiting',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Patterns table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    positions TEXT
                )
            ''')
            
            # Insert default patterns if not exists
            cursor.execute("SELECT COUNT(*) FROM patterns")
            if cursor.fetchone()[0] == 0:
                patterns = [
                    (1, 'Full House', 'All numbers on card', 'all'),
                    (2, 'Four Corners', 'All four corners', '[[0,0],[0,4],[4,0],[4,4]]'),
                    (3, 'X Pattern', 'Both diagonals', 'diagonals'),
                    (4, 'Blackout', 'Entire card filled', 'all')
                ]
                cursor.executemany("INSERT INTO patterns VALUES (?,?,?,?)", patterns)
            
            # Insert default game
            cursor.execute("INSERT OR IGNORE INTO games (id, pattern_id, status) VALUES (1, 1, 'waiting')")
            
            # Insert default payment methods
            cursor.execute('''
                INSERT OR IGNORE INTO payment_methods (name, type, account_number, account_name, is_active)
                VALUES
                    ('Telebirr', 'mobile_money', '0953933030', 'Bingo Bot', 1),
                    ('CBE Birr', 'mobile_money', '0953933030', 'Bingo Bot', 1)
            ''')
            
            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_user ON payment_requests(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_user ON withdrawal_requests(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_cards_game ON user_cards(game_id, user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_active_games_user ON active_games(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_bonuses_status ON referral_bonuses(status)")
            
            conn.commit()
            logger.info("✅ Database initialized successfully with conditional referral system")
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

    # ==================== GAME METHODS ====================

    def add_active_game(self, user_id: int, game_id: int, card_ids: List[int], stake: int) -> bool:
        """Add active game for user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO active_games (user_id, game_id, card_ids, stake)
                VALUES (?, ?, ?, ?)
            ''', (user_id, game_id, json.dumps(card_ids), stake))
            conn.commit()
            return True
        finally:
            conn.close()

    def get_active_games_count(self, user_id: int) -> int:
        """Get count of active games for user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM active_games WHERE user_id = ?", (user_id,))
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def get_total_stake(self, user_id: int) -> int:
        """Get total stake for user"""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(stake) FROM active_games WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()[0]
            return result or 0
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
            logger.info(f"✅ Pending referral bonus created: {referrer_id} -> {referred_id}")
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
            logger.info(f"✅ Referral bonus paid: {referrer_id} received 5 ETB for {user_id}'s deposit")
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
            
            # Count pending bonuses (referred but not yet deposited)
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

            cursor.execute("SELECT COUNT(DISTINCT game_id) FROM active_games")
            active_games = cursor.fetchone()[0]

            # Referral stats
            cursor.execute("SELECT COUNT(*) FROM referral_bonuses WHERE status = 'pending'")
            pending_referrals = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(amount) FROM referral_bonuses WHERE status = 'paid'")
            total_referral_paid = cursor.fetchone()[0] or 0

            return {
                'total_users': total_users,
                'total_balance': total_balance,
                'total_deposits': total_deposits,
                'total_withdrawals': total_withdrawals,
                'active_games': active_games,
                'pending_referrals': pending_referrals,
                'total_referral_paid': total_referral_paid
            }
        finally:
            conn.close()