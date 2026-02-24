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
                    min_amount INTEGER DEFAULT 100,  -- In cents/local currency smallest unit
                    max_amount INTEGER DEFAULT 1000000,
                    fee_percentage REAL DEFAULT 0,
                    fee_fixed INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    instructions TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Payment Accounts table (for admin to manage receiving accounts)
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
            
            # Payment Requests table (for manual/Telbirr/CBE payments)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    method_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT DEFAULT 'ETB',
                    status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'failed', 'expired')) DEFAULT 'pending',
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
            
            # Payment Proofs table (for manual payments)
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
            
            # Withdrawal Requests table (for manual withdrawals)
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
            
            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_requests_user_id ON payment_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_user_id ON withdrawal_requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_withdrawal_requests_status ON withdrawal_requests(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_payment_methods_is_active ON payment_methods(is_active)')
            
            conn.commit()
            logger.info("✅ Database tables created/verified")
    
    def _insert_default_payment_methods(self):
        """Insert default payment methods for Ethiopia"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if methods already exist
            cursor.execute('SELECT COUNT(*) as count FROM payment_methods')
            if cursor.fetchone()['count'] > 0:
                return
            
            # Ethiopian payment methods
            default_methods = [
                # Telbirr (Ethio Telecom)
                ('TELBIRR', 'ቴልቢር (Telbirr)', 'mobile_money', 'Ethio Telecom', None, None, 1000, 500000, 0, 0, 1, 
                 '1. Dial *127#\n2. Select "Send Money"\n3. Enter our number 0999123456\n4. Enter amount\n5. Enter PIN\n6. Save reference number'),
                
                # CBE Birr (Commercial Bank of Ethiopia)
                ('CBEBIRR', 'ሲቢኢ ቢር (CBE Birr)', 'mobile_money', 'CBE', None, None, 1000, 500000, 0, 0, 1,
                 '1. Dial *847#\n2. Select "Send Money"\n3. Enter our number 0999123456\n4. Enter amount\n5. Enter PIN\n6. Save transaction ID'),
                
                # CBE Bank Transfer
                ('CBE_BANK', 'ሲቢኢ ባንክ (CBE Bank)', 'bank', 'CBE', '1000134567890', 'Bingo Entertainment PLC', 10000, 5000000, 0, 0, 1,
                 'Account Number: 1000134567890\nAccount Name: Bingo Entertainment PLC\nBank: Commercial Bank of Ethiopia\nBranch: Bole Branch'),
                
                # Awash Bank
                ('AWASH_BANK', 'አዋሽ ባንክ (Awash Bank)', 'bank', 'Awash Bank', '013456789012', 'Bingo Entertainment PLC', 10000, 5000000, 0, 0, 1,
                 'Account Number: 013456789012\nAccount Name: Bingo Entertainment PLC\nBank: Awash Bank\nBranch: Head Office'),
                
                # Dashen Bank
                ('DASHEN_BANK', 'ዳሸን ባንክ (Dashen Bank)', 'bank', 'Dashen Bank', '023456789012', 'Bingo Entertainment PLC', 10000, 5000000, 0, 0, 1,
                 'Account Number: 023456789012\nAccount Name: Bingo Entertainment PLC\nBank: Dashen Bank\nBranch: Main Branch'),
                
                # Manual Payment (Cash/Agent)
                ('MANUAL', 'በአካል (Manual/Cash)', 'manual', None, None, None, 1000, 10000000, 0, 0, 1,
                 '1. Visit our agent near you\n2. Provide your user ID\n3. Pay cash\n4. Agent will confirm payment\n5. Balance updates instantly'),
                
                # Stripe (International)
                ('STRIPE', 'Credit/Debit Card', 'card', 'Stripe', None, None, 500, 1000000, 2.9, 30, 1,
                 'Secure online payment via Stripe. All major cards accepted.')
            ]
            
            for method in default_methods:
                cursor.execute('''
                    INSERT INTO payment_methods 
                    (method_code, method_name, type, provider, account_number, account_name, 
                     min_amount, max_amount, fee_percentage, fee_fixed, is_active, instructions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', method)
            
            # Add primary accounts for each method
            # Telbirr account
            cursor.execute('SELECT id FROM payment_methods WHERE method_code = "TELBIRR"')
            telbirr_id = cursor.fetchone()['id']
            cursor.execute('''
                INSERT INTO payment_accounts (method_id, account_number, account_name, phone_number, is_primary)
                VALUES (?, ?, ?, ?, ?)
            ''', (telbirr_id, '0999123456', 'Bingo Entertainment', '0999123456', 1))
            
            # CBE Birr account
            cursor.execute('SELECT id FROM payment_methods WHERE method_code = "CBEBIRR"')
            cbebirr_id = cursor.fetchone()['id']
            cursor.execute('''
                INSERT INTO payment_accounts (method_id, account_number, account_name, phone_number, is_primary)
                VALUES (?, ?, ?, ?, ?)
            ''', (cbebirr_id, '0999123456', 'Bingo Entertainment', '0999123456', 1))
            
            conn.commit()
            logger.info("✅ Default payment methods inserted")
    
    # ==================== PAYMENT METHODS ====================
    
    def get_payment_methods(self, type=None, active_only=True):
        """Get all payment methods"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM payment_methods WHERE 1=1"
            params = []
            
            if active_only:
                query += " AND is_active = 1"
            
            if type:
                query += " AND type = ?"
                params.append(type)
            
            query += " ORDER BY type, method_name"
            
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def get_payment_method(self, method_id):
        """Get payment method by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payment_methods WHERE id = ?', (method_id,))
            return cursor.fetchone()
    
    def get_payment_method_by_code(self, method_code):
        """Get payment method by code"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payment_methods WHERE method_code = ?', (method_code,))
            return cursor.fetchone()
    
    def get_payment_accounts(self, method_id, active_only=True):
        """Get payment accounts for a method"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM payment_accounts WHERE method_id = ?"
            params = [method_id]
            
            if active_only:
                query += " AND is_active = 1"
            
            query += " ORDER BY is_primary DESC, id"
            
            cursor.execute(query, params)
            return cursor.fetchall()
    
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
            
            # Get method to validate amount
            cursor.execute('SELECT * FROM payment_methods WHERE id = ?', (method_id,))
            method = cursor.fetchone()
            
            if not method:
                return None
            
            if amount < method['min_amount'] or amount > method['max_amount']:
                return None
            
            # Calculate fee
            fee = int(amount * method['fee_percentage'] / 100) + method['fee_fixed']
            total_amount = amount + fee
            
            cursor.execute('''
                INSERT INTO payment_requests 
                (request_id, user_id, method_id, amount, sender_phone, sender_name, expiry_time)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now', '+1 hour'))
            ''', (request_id, user_id, method_id, total_amount, sender_phone, sender_name))
            
            conn.commit()
            return request_id
    
    def get_payment_request(self, request_id):
        """Get payment request by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT pr.*, pm.method_name, pm.type, pm.provider, pm.instructions
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
    
    def update_payment_request_status(self, request_id, status, transaction_reference=None, admin_notes=None):
        """Update payment request status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if status == 'completed':
                cursor.execute('''
                    UPDATE payment_requests 
                    SET status = ?, transaction_reference = ?, admin_notes = ?, 
                        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE request_id = ?
                ''', (status, transaction_reference, admin_notes, request_id))
            else:
                cursor.execute('''
                    UPDATE payment_requests 
                    SET status = ?, admin_notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE request_id = ?
                ''', (status, admin_notes, request_id))
            
            conn.commit()
            
            if cursor.rowcount > 0 and status == 'completed':
                # Get request to update user balance
                cursor.execute('SELECT user_id, amount FROM payment_requests WHERE request_id = ?', (request_id,))
                req = cursor.fetchone()
                
                if req:
                    self.update_balance(
                        user_id=req['user_id'],
                        amount=req['amount'],
                        transaction_type='deposit',
                        description=f'Payment via {request_id}'
                    )
            
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
    
    def process_withdrawal_request(self, request_id, status, admin_notes=None, processed_by=None):
        """Process a withdrawal request (approve/reject)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE withdrawal_requests 
                SET status = ?, admin_notes = ?, processed_by = ?, 
                    processed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?
            ''', (status, admin_notes, processed_by, request_id))
            
            conn.commit()
            
            if cursor.rowcount > 0 and status == 'completed':
                # Get request to update user balance
                cursor.execute('''
                    SELECT user_id, amount FROM withdrawal_requests WHERE request_id = ?
                ''', (request_id,))
                req = cursor.fetchone()
                
                if req:
                    self.update_balance(
                        user_id=req['user_id'],
                        amount=-req['amount'],
                        transaction_type='withdrawal',
                        description=f'Withdrawal via {request_id}',
                        status='completed'
                    )
            elif cursor.rowcount > 0 and status == 'rejected':
                # Refund if needed (if amount was already deducted)
                cursor.execute('''
                    SELECT user_id, amount FROM withdrawal_requests WHERE request_id = ?
                ''', (request_id,))
                req = cursor.fetchone()
                
                if req:
                    # You might want to add a refund transaction here
                    pass
            
            return cursor.rowcount > 0
    
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
                
                logger.info(f"✅ Transaction {transaction_id}: User {user_id}, {transaction_type}, {amount/100:.2f} {self.get_user_currency(user_id)}, Status: {status}")
                
                return {
                    'new_balance': new_balance, 
                    'transaction_id': transaction_id, 
                    'status': status
                }
                
            except Exception as e:
                cursor.execute('ROLLBACK')
                logger.error(f"Database error in update_balance: {e}")
                raise e
    
    def get_user_currency(self, user_id):
        """Get user's preferred currency"""
        user = self.get_user(user_id)
        return user['currency'] if user else 'ETB'
    
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
        """Get all pending withdrawals (legacy)"""
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
            
            # Payment requests stats
            cursor.execute('SELECT COUNT(*) as count FROM payment_requests WHERE status = "pending"')
            stats['pending_payment_requests'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM withdrawal_requests WHERE status = "pending"')
            stats['pending_withdrawal_requests'] = cursor.fetchone()['count']
            
            # Today's transactions
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
    
    # ==================== PAYMENT INTENTS ====================
    
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
            logger.info(f"✅ Game {game_id} winner: User {user_id}, Prize: {prize_amount/100:.2f}")
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
            
            # Payment stats
            cursor.execute('SELECT COUNT(*) as count FROM payment_requests')
            stats['total_payment_requests'] = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM withdrawal_requests')
            stats['total_withdrawal_requests'] = cursor.fetchone()['count']
            
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