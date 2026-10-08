import sqlite3
import os

class Database:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.getenv("RACE_DB_PATH", os.path.abspath("race_state.db"))
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self._initialize_db()

    def _initialize_db(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS race_state (
                channel_id INTEGER PRIMARY KEY,
                lap INTEGER DEFAULT 0,  -- Changed to 0 for 0.1 start
                node INTEGER DEFAULT 0,  -- Changed to 0 so first newnode = 0.1
                ofa_holder INTEGER DEFAULT NULL,
                ofa_target TEXT DEFAULT NULL,
                ofa_done BOOLEAN DEFAULT 0,
                dyp_progress INTEGER DEFAULT 0,
                dyp_target INTEGER DEFAULT 10,
                je_progress INTEGER DEFAULT 0,
                je_target INTEGER DEFAULT 4,
                je_done BOOLEAN DEFAULT 0,
                mute_until REAL DEFAULT 0,
                event_active BOOLEAN DEFAULT 0
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_ofa (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                user_id INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS dyp_contributors (
                channel_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (channel_id, user_id)
            )
        """)
        # Add missing columns to existing tables
        for col, dtype, default in [
            ("event_active", "BOOLEAN", "0"),
            ("ofa_done", "BOOLEAN", "0"),
        ]:
            try:
                self.cursor.execute(f"ALTER TABLE race_state ADD COLUMN {col} {dtype} DEFAULT {default}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def get_state(self, channel_id):
        self.cursor.execute("SELECT * FROM race_state WHERE channel_id = ?", (channel_id,))
        row = self.cursor.fetchone()
        if not row:
            self.cursor.execute("INSERT INTO race_state (channel_id) VALUES (?)", (channel_id,))
            self.conn.commit()
            return self.get_state(channel_id)
        
        columns = [description[0] for description in self.cursor.description]
        return dict(zip(columns, row))

    ALLOWED_COLUMNS = {
        'lap', 'node', 'ofa_holder', 'ofa_target', 'ofa_done', 'dyp_progress', 'dyp_target',
        'je_progress', 'je_target', 'je_done', 'mute_until', 'event_active'
    }

    def update_state(self, channel_id, **kwargs):
        if not kwargs: return
        safe_kwargs = {k: v for k, v in kwargs.items() if k in self.ALLOWED_COLUMNS}
        if not safe_kwargs: return
        
        self.get_state(channel_id)
        set_clause = ", ".join([f"{key} = ?" for key in safe_kwargs.keys()])
        values = list(safe_kwargs.values())
        values.append(channel_id)
        self.cursor.execute(f"UPDATE race_state SET {set_clause} WHERE channel_id = ?", values)
        self.conn.commit()

    def increment_dyp(self, channel_id, user_id):
        self.cursor.execute("SELECT 1 FROM dyp_contributors WHERE channel_id = ? AND user_id = ?", (channel_id, user_id))
        if self.cursor.fetchone():
            return False
        
        self.cursor.execute("""
            UPDATE race_state 
            SET dyp_progress = MIN(dyp_progress + 1, dyp_target) 
            WHERE channel_id = ? AND dyp_progress < dyp_target
        """, (channel_id,))
        if self.cursor.rowcount > 0:
            self.cursor.execute("INSERT INTO dyp_contributors (channel_id, user_id) VALUES (?, ?)", (channel_id, user_id))
            self.conn.commit()
            return True
        return False

    def reset_dyp_contributors(self, channel_id):
        self.cursor.execute("DELETE FROM dyp_contributors WHERE channel_id = ?", (channel_id,))
        self.conn.commit()

    def add_pending_ofa(self, message_id, channel_id, user_id):
        self.cursor.execute("INSERT INTO pending_ofa (message_id, channel_id, user_id) VALUES (?, ?, ?)", (message_id, channel_id, user_id))
        self.conn.commit()

    def get_pending_ofa(self, message_id):
        self.cursor.execute("SELECT channel_id, user_id FROM pending_ofa WHERE message_id = ?", (message_id,))
        row = self.cursor.fetchone()
        return {"channel_id": row[0], "user_id": row[1]} if row else None

    def get_pending_ofa_by_channel(self, channel_id):
        self.cursor.execute("SELECT message_id, user_id FROM pending_ofa WHERE channel_id = ?", (channel_id,))
        row = self.cursor.fetchone()
        return {"message_id": row[0], "user_id": row[1]} if row else None

    def delete_pending_ofa(self, message_id):
        self.cursor.execute("DELETE FROM pending_ofa WHERE message_id = ?", (message_id,))
        self.conn.commit()

    def clear_pending_ofa_for_channel(self, channel_id):
        self.cursor.execute("DELETE FROM pending_ofa WHERE channel_id = ?", (channel_id,))
        self.conn.commit()

db = Database()