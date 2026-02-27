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
        self._insert_bingo_patterns()
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
                    max_amount INTEGER DEFAULT 50000,
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
            
            # Bingo patterns table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    positions TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        """Insert Ethiopian payment methods"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) as count FROM payment_methods')
            if cursor.fetchone()['count'] > 0:
                return
            
            # Ethiopian Mobile Money Payment Methods
            default_methods = [
                ('TELBIRR', 'ቴሌቢር (Telbirr)', 'mobile_money', 'Ethio Telecom', 1000, 50000, 1,
                 '''🔵 **ቴሌቢር ክፍያ መመሪያ / Telbirr Payment Instructions**

1. ወደ ቴሌቢር ሜኑ ለመግባት *127# ይደውሉ
2. "ገንዘብ ላክ" የሚለውን ይምረጡ
3. ቁጥር **0953933030** ያስገቡ
4. መጠኑን ያስገቡ (ከ10 እስከ 500 ብር)
5. ፒንዎን ያስገቡ
6. ከተጠናቀቀ በኋላ የደረሰኝ ቁጥር (reference) ያስቀምጡ

📱 **English:**
1. Dial *127# to access Telbirr menu
2. Select "Send Money"
3. Enter number **0953933030**
4. Enter amount (10-500 ETB)
5. Enter your PIN
6. Save the reference number'''),
                
                ('CBEBIRR', 'ሲቢኢ ቢር (CBE Birr)', 'mobile_money', 'CBE', 1000, 50000, 1,
                 '''💚 **ሲቢኢ ቢር ክፍያ መመሪያ / CBE Birr Payment Instructions**

1. ወደ ሲቢኢ ቢር ሜኑ ለመግባት *847# ይደውሉ
2. "ገንዘብ ላክ" የሚለውን ይምረጡ
3. ቁጥር **0953933030** ያስገቡ
4. መጠኑን ያስገቡ (ከ10 እስከ 500 ብር)
5. ፒንዎን ያስገቡ
6. ከተጠናቀቀ በኋላ የግብይት መለያ ቁጥር (transaction ID) ያስቀምጡ

📱 **English:**
1. Dial *847# to access CBE Birr menu
2. Select "Send Money"
3. Enter number **0953933030**
4. Enter amount (10-500 ETB)
5. Enter your PIN
6. Save the transaction ID''')
            ]
            
            for method in default_methods:
                cursor.execute('''
                    INSERT INTO payment_methods 
                    (method_code, method_name, type, provider, min_amount, max_amount, is_active, instructions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', method)
            
            conn.commit()
            logger.info("✅ Ethiopian payment methods inserted")
    
    def _insert_bingo_patterns(self):
        """Insert 100 bingo patterns"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) as count FROM patterns')
            if cursor.fetchone()['count'] > 0:
                return
            
            patterns = [
                # Basic patterns (1-20)
                {"name": "Full House", "description": "Mark all numbers on your card", "positions": json.dumps({"type": "full_house"})},
                {"name": "Four Corners", "description": "Mark the four corner squares", "positions": json.dumps([[0,0], [0,4], [4,0], [4,4]])},
                {"name": "X Pattern", "description": "Mark both diagonals", "positions": json.dumps([[0,0], [1,1], [2,2], [3,3], [4,4], [0,4], [1,3], [3,1], [4,0]])},
                {"name": "Plus Sign", "description": "Mark middle row and middle column", "positions": json.dumps([[2,0], [2,1], [2,2], [2,3], [2,4], [0,2], [1,2], [3,2], [4,2]])},
                {"name": "Top Row", "description": "Mark the entire top row", "positions": json.dumps([[0,0], [0,1], [0,2], [0,3], [0,4]])},
                {"name": "Middle Row", "description": "Mark the entire middle row", "positions": json.dumps([[2,0], [2,1], [2,2], [2,3], [2,4]])},
                {"name": "Bottom Row", "description": "Mark the entire bottom row", "positions": json.dumps([[4,0], [4,1], [4,2], [4,3], [4,4]])},
                {"name": "First Column", "description": "Mark the entire first column", "positions": json.dumps([[0,0], [1,0], [2,0], [3,0], [4,0]])},
                {"name": "Middle Column", "description": "Mark the entire middle column", "positions": json.dumps([[0,2], [1,2], [2,2], [3,2], [4,2]])},
                {"name": "Last Column", "description": "Mark the entire last column", "positions": json.dumps([[0,4], [1,4], [2,4], [3,4], [4,4]])},
                {"name": "Small Diamond", "description": "Mark a diamond shape in the center", "positions": json.dumps([[1,2], [2,1], [2,2], [2,3], [3,2]])},
                {"name": "Big Diamond", "description": "Mark a large diamond shape", "positions": json.dumps([[0,2], [1,1], [1,3], [2,0], [2,4], [3,1], [3,3], [4,2]])},
                {"name": "Letter L", "description": "Mark L shape", "positions": json.dumps([[0,0], [0,1], [0,2], [0,3], [0,4], [1,4], [2,4], [3,4], [4,4]])},
                {"name": "Letter T", "description": "Mark T shape", "positions": json.dumps([[0,0], [0,1], [0,2], [0,3], [0,4], [1,2], [2,2], [3,2], [4,2]])},
                {"name": "Letter U", "description": "Mark U shape", "positions": json.dumps([[0,0], [0,4], [1,0], [1,4], [2,0], [2,4], [3,0], [3,4], [4,0], [4,1], [4,2], [4,3], [4,4]])},
                {"name": "Frame", "description": "Mark the outer border", "positions": json.dumps([[0,0], [0,1], [0,2], [0,3], [0,4], [1,0], [1,4], [2,0], [2,4], [3,0], [3,4], [4,0], [4,1], [4,2], [4,3], [4,4]])},
                {"name": "Checkerboard", "description": "Mark alternating squares", "positions": json.dumps([[0,0], [0,2], [0,4], [1,1], [1,3], [2,0], [2,2], [2,4], [3,1], [3,3], [4,0], [4,2], [4,4]])},
                {"name": "Zigzag", "description": "Mark a zigzag pattern", "positions": json.dumps([[0,0], [0,1], [1,1], [1,2], [2,2], [2,3], [3,3], [3,4], [4,4]])},
                {"name": "Spiral", "description": "Mark a spiral pattern", "positions": json.dumps([[0,0], [0,1], [0,2], [0,3], [0,4], [1,4], [2,4], [3,4], [4,4], [4,3], [4,2], [4,1], [4,0], [3,0], [2,0], [1,0], [1,1], [1,2], [1,3], [2,3], [3,3], [3,2], [3,1], [2,1], [2,2]])},
                {"name": "Smiley Face", "description": "Mark a smiley face pattern", "positions": json.dumps([[1,1], [1,3], [3,0], [3,1], [3,2], [3,3], [3,4], [4,2]])}
            ]
            
            # Add 80 more patterns
            for i in range(20, 100):
                pattern_type = random.choice(["row", "column", "diagonal", "cross", "letter", "shape"])
                if pattern_type == "row":
                    row = random.randint(0, 4)
                    name = f"Row {row + 1} Variant {i}"
                    description = f"Mark row {row + 1} with a twist"
                    positions = json.dumps([[row, col] for col in range(5)])
                elif pattern_type == "column":
                    col = random.randint(0, 4)
                    name = f"Column {col + 1} Variant {i}"
                    description = f"Mark column {col + 1} with a twist"
                    positions = json.dumps([[row, col] for row in range(5)])
                elif pattern_type == "diagonal":
                    name = f"Diagonal Variant {i}"
                    description = "Mark a diagonal pattern"
                    positions = json.dumps([[j, j] for j in range(5)] + [[j, 4-j] for j in range(5)])
                elif pattern_type == "cross":
                    name = f"Cross Variant {i}"
                    description = "Mark a cross pattern"
                    positions = json.dumps([[2, j] for j in range(5)] + [[j, 2] for j in range(5)])
                else:
                    name = f"Random Shape {i}"
                    description = "Mark a random pattern"
                    pos_set = set()
                    for _ in range(random.randint(5, 15)):
                        pos_set.add((random.randint(0, 4), random.randint(0, 4)))
                    positions = json.dumps([list(p) for p in pos_set])
                
                patterns.append({
                    "name": name,
                    "description": description,
                    "positions": positions
                })
            
            for p in patterns:
                cursor.execute('''
                    INSERT INTO patterns (name, description, positions)
                    VALUES (?, ?, ?)
                ''', (p["name"], p["description"], p["positions"]))
            
            conn.commit()
            logger.info(f"✅ Inserted {len(patterns)} bingo patterns")
    
    # ==================== PAYMENT METHOD METHODS ====================
    
    def get_payment_methods(self, type=None, active_only=True):
        """Get payment methods - FIXED METHOD"""
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
        """Get primary account for payment method"""
        # For now, return None - accounts not implemented
        return None
    
    # ==================== PAYMENT REQUEST METHODS ====================
    
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
    
    # ==================== USER METHODS ====================
    
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
            
            cursor.execute('SELECT COUNT(*) as count FROM payment_requests WHERE status = "pending"')
            stats['pending_payments'] = cursor.fetchone()['count']
            
            return stats