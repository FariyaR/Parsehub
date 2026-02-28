import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional
import threading

# Optional PostgreSQL support
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
    HAS_PSYCOPG2 = True
except ImportError:
    try:
        import pg8000
        POSTGRES_AVAILABLE = True
        HAS_PSYCOPG2 = False
    except ImportError:
        POSTGRES_AVAILABLE = False
        HAS_PSYCOPG2 = False


# Load environment variables from .env files
dotenv_path = Path(__file__).parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
else:
    load_dotenv()



class ParseHubDatabase:
    # Shared thread-local storage for all instances of ParseHubDatabase
    _shared_local = threading.local()

    def __init__(self, db_path: str = None):
        # Database connection info — never open a connection here.
        # Connections are obtained lazily per-request from the pool (db_pool.py).
        self.db_url = os.getenv('DATABASE_URL')
        self.use_postgres = bool(self.db_url and POSTGRES_AVAILABLE)

        if self.use_postgres:
            print(f"Using PostgreSQL database: {self.db_url.split('@')[-1]}")
        else:
            # On Railway use only DATABASE_URL (Postgres). Do not set DATABASE_PATH when using Postgres.
            if db_path is None:
                db_path = os.getenv('DATABASE_PATH', None)
                if not db_path:
                    db_path = str(Path(__file__).parent / "parsehub.db")
                if not os.path.isabs(db_path):
                    project_root = Path(__file__).parent.parent
                    db_path = str(project_root / db_path)
            self.db_path = db_path
            print(f"Using SQLite database: {self.db_path}")

        # NOTE: No self.conn = None; no self.init_db() here.
        # Connections are managed by db_pool.ConnectionPool for PostgreSQL
        # and by thread-local storage for SQLite (legacy mode).

    @property
    def conn(self):
        return getattr(self._shared_local, 'conn', None)

    @conn.setter
    def conn(self, value):
        self._shared_local.conn = value

    def _get_connection(self):
        """Get a new database connection based on type"""
        if self.use_postgres:
            if HAS_PSYCOPG2:
                conn = psycopg2.connect(self.db_url)
            else:
                import pg8000
                from urllib.parse import urlparse
                url = urlparse(self.db_url)
                conn = pg8000.connect(
                    user=url.username,
                    password=url.password,
                    host=url.hostname,
                    port=url.port,
                    database=url.path[1:]
                )
            # Make PG behave similar to SQLite's isolation_level=None (autocommit)
            conn.autocommit = True
            return conn
        else:
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30,
                isolation_level=None
            )
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA busy_timeout=30000')
                conn.execute('PRAGMA synchronous=NORMAL')
            except:
                pass
            return conn

    class PgCursorShim:
        """Shim to make PostgreSQL cursor behave like SQLite cursor"""
        def __init__(self, cursor):
            self.cursor = cursor
        
        def execute(self, sql, params=None):
            # SQL source uses %s; Postgres expects %s. Only fix schema keywords.
            sql = sql.replace('AUTOINCREMENT', 'SERIAL')
            sql = sql.replace('INSERT OR REPLACE', 'INSERT') # Simple shim, technically incomplete for REPLACE
            
            # Convert SQLite specific REPLACE to PostgreSQL ON CONFLICT
            # Note: This is an optimistic shim for basic cases
            if 'INSERT INTO projects' in sql and 'updated_at' in sql:
                 sql += ' ON CONFLICT (token) DO UPDATE SET title=EXCLUDED.title, owner_email=EXCLUDED.owner_email, main_site=EXCLUDED.main_site, updated_at=CURRENT_TIMESTAMP'
            elif 'INSERT INTO analytics_cache' in sql:
                 sql += ' ON CONFLICT (project_token) DO UPDATE SET updated_at=CURRENT_TIMESTAMP, analytics_json=EXCLUDED.analytics_json'
            
            # Clean up SQLite types
            sql = sql.replace('DATETIME', 'TIMESTAMP')
            
            if params:
                return self.cursor.execute(sql, params)
            return self.cursor.execute(sql)

        def _row_to_dict(self, row):
            if row is None: return None
            # If it's already a dict (psycopg2 RealDictCursor), return it
            if isinstance(row, dict):
                return row
            # If it's a tuple (pg8000), convert using description
            if hasattr(self.cursor, 'description') and self.cursor.description:
                columns = [col[0] for col in self.cursor.description]
                return dict(zip(columns, row))
            return row

        def fetchone(self):
            row = self.cursor.fetchone()
            return self._row_to_dict(row)

        def fetchall(self):
            return [self._row_to_dict(row) for row in self.cursor.fetchall()]

        @property
        def lastrowid(self):
            try:
                self.cursor.execute("SELECT lastval() AS id")
                row = self.cursor.fetchone()
                return row['id'] if isinstance(row, dict) else row[0]
            except Exception:
                return None

        def __iter__(self):
            for row in self.cursor:
                yield self._row_to_dict(row)

        def __getattr__(self, name):
            return getattr(self.cursor, name)


    class _SQLiteCursorShim:
        """For SQLite: source uses %s; SQLite expects ?."""
        def __init__(self, cursor):
            self.cursor = cursor
        def execute(self, sql, params=None):
            sql = sql.replace('%s', '?')
            return self.cursor.execute(sql, params) if params else self.cursor.execute(sql)
        def fetchone(self): return self.cursor.fetchone()
        def fetchall(self): return self.cursor.fetchall()
        @property
        def lastrowid(self): return getattr(self.cursor, 'lastrowid', None)
        def __getattr__(self, name): return getattr(self.cursor, name)

    def cursor(self):
        """Get a cursor (RealDictCursor for Postgres, shim for SQLite)."""
        if not self.conn:
            self.connect()
        if self.use_postgres:
            cursor = self.conn.cursor(cursor_factory=RealDictCursor)
            return self.PgCursorShim(cursor)
        return self._SQLiteCursorShim(self.conn.cursor())


    def connect(self):
        """Obtain a database connection.

        PostgreSQL → borrow a raw DBAPI connection from the SQLAlchemy pool
                     (pool_size, max_overflow, pool_pre_ping all handled by SQLAlchemy).
        SQLite     → thread-local connection (unchanged).
        """
        if self.use_postgres:
            # Reuse the thread-local connection for the duration of a request.
            # teardown_appcontext calls disconnect() which returns it to the pool.
            if getattr(self._shared_local, 'conn', None) is None:
                try:
                    from db_pool import get_engine
                    engine = get_engine()
                    # raw_connection() borrows from the pool; calling .close() returns it.
                    conn = engine.raw_connection()
                    conn.autocommit = True
                    self._shared_local.conn = conn
                except Exception as exc:
                    raise RuntimeError(
                        f'[DB] Cannot obtain PostgreSQL connection from pool: {exc}'
                    ) from exc
            else:
                # pool_pre_ping handles stale detection inside SQLAlchemy;
                # do a cheap check here for belt-and-suspenders.
                try:
                    cur = self._shared_local.conn.cursor()
                    cur.execute('SELECT 1')
                    if hasattr(cur, 'close'):
                        cur.close()
                except Exception:
                    # Connection is dead — close it (pool will replace it)
                    try:
                        self._shared_local.conn.close()
                    except Exception:
                        pass
                    self._shared_local.conn = None
                    # Recurse once to get a fresh connection
                    return self.connect()
            return self._shared_local.conn

        # SQLite — unchanged thread-local behaviour
        if getattr(self._shared_local, 'conn', None):
            try:
                self._shared_local.conn.execute('SELECT 1')
                return self._shared_local.conn
            except Exception:
                try:
                    self._shared_local.conn.close()
                except Exception:
                    pass
                self._shared_local.conn = None

        self.conn = self._get_connection()
        return self.conn


    def disconnect(self):
        """Return the connection to the SQLAlchemy pool (PostgreSQL)
        or keep the thread-local alive (SQLite).

        For PostgreSQL, calling raw_conn.close() does NOT close the physical
        connection — SQLAlchemy intercepts it and returns the connection to
        the QueuePool.  This is the correct way to release pooled connections.
        """
        if self.use_postgres:
            conn = getattr(self._shared_local, 'conn', None)
            if conn is not None:
                try:
                    conn.close()   # returns to SQLAlchemy pool
                except Exception:
                    pass
                self._shared_local.conn = None
        # SQLite: keep thread-local alive for the lifetime of the worker thread

    def init_db(self):
        """Initialize database schema"""
        conn = self.connect()
        cursor = self.cursor()

        if self.use_postgres:
            # Load and execute PG specific schema
            try:
                sql_path = Path(__file__).parent / "init_postgres.sql"
                if sql_path.exists():
                    with open(sql_path, 'r') as f:
                        cursor.execute(f.read())
                    print("[DB] PostgreSQL schema initialized")
                    conn.commit()
                    self.disconnect()
                    return
            except Exception as e:
                print(f"[DB] Warning: Failed to load init_postgres.sql: {e}")
                # Fall through to standard init (sqlite compatible but shimmed)


        # Projects table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                owner_email TEXT,
                main_site TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Project Metadata Linking table - links projects to metadata
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                metadata_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY (metadata_id) REFERENCES metadata(id) ON DELETE CASCADE,
                UNIQUE(project_id, metadata_id)
            )
        ''')

        # Create indexes for project_metadata
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_project_metadata_project_id ON project_metadata(project_id)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_project_metadata_metadata_id ON project_metadata(metadata_id)')

        # Runs table - tracks each execution
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                run_token TEXT UNIQUE NOT NULL,
                status TEXT,
                pages_scraped INTEGER DEFAULT 0,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                duration_seconds INTEGER,
                records_count INTEGER DEFAULT 0,
                data_file TEXT,
                is_empty BOOLEAN DEFAULT 0,
                is_continuation BOOLEAN DEFAULT 0,
                completion_percentage REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        ''')

        # Scraped data table - stores individual records
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scraped_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                data_key TEXT,
                data_value TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES runs(id),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        ''')

        # Key metrics table - for analytics
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                date DATE,
                total_pages INTEGER DEFAULT 0,
                total_records INTEGER DEFAULT 0,
                runs_count INTEGER DEFAULT 0,
                avg_duration REAL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, date),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        ''')

        # Recovery operations tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recovery_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_run_id INTEGER NOT NULL,
                recovery_run_id INTEGER,
                project_id INTEGER NOT NULL,
                original_project_token TEXT,
                recovery_project_token TEXT,
                last_product_url TEXT,
                last_product_name TEXT,
                stopped_timestamp TIMESTAMP,
                recovery_triggered_timestamp TIMESTAMP,
                recovery_started_timestamp TIMESTAMP,
                recovery_completed_timestamp TIMESTAMP,
                status TEXT DEFAULT 'pending',
                original_data_count INTEGER DEFAULT 0,
                recovery_data_count INTEGER DEFAULT 0,
                final_data_count INTEGER DEFAULT 0,
                duplicates_removed INTEGER DEFAULT 0,
                attempt_number INTEGER DEFAULT 1,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (original_run_id) REFERENCES runs(id),
                FOREIGN KEY (recovery_run_id) REFERENCES runs(id)
            )
        ''')

        # Data lineage - tracks which data came from which run
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS data_lineage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_data_id INTEGER NOT NULL,
                source_run_id INTEGER NOT NULL,
                recovery_operation_id INTEGER,
                is_duplicate BOOLEAN DEFAULT 0,
                duplicate_of_data_id INTEGER,
                product_url TEXT,
                product_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (scraped_data_id) REFERENCES scraped_data(id),
                FOREIGN KEY (source_run_id) REFERENCES runs(id),
                FOREIGN KEY (recovery_operation_id) REFERENCES recovery_operations(id)
            )
        ''')

        # Run checkpoints - track progress snapshots
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS run_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                snapshot_timestamp TIMESTAMP,
                item_count_at_time INTEGER,
                items_per_minute REAL,
                estimated_completion_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            )
        ''')

        # Monitoring sessions - tracks real-time monitoring data collection
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitoring_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                run_token TEXT NOT NULL,
                target_pages INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                total_records INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                progress_percentage REAL DEFAULT 0,
                current_url TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        ''')

        # Enhanced scraped records with page and session tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scraped_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                run_token TEXT NOT NULL,
                page_number INTEGER,
                data_hash TEXT,
                data_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES monitoring_sessions(id),
                FOREIGN KEY (project_id) REFERENCES projects(id),
                UNIQUE(run_token, page_number, data_hash)
            )
        ''')

        # Analytics cache - stores complete analytics data
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analytics_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_token TEXT UNIQUE NOT NULL,
                run_token TEXT,
                total_records INTEGER DEFAULT 0,
                total_fields INTEGER DEFAULT 0,
                total_runs INTEGER DEFAULT 0,
                completed_runs INTEGER DEFAULT 0,
                progress_percentage REAL DEFAULT 0,
                status TEXT,
                analytics_json TEXT,
                stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # CSV exports - stores complete CSV data for export
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS csv_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_token TEXT NOT NULL,
                run_token TEXT,
                csv_data TEXT,
                row_count INTEGER DEFAULT 0,
                stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_token, run_token)
            )
        ''')

        # Analytics records - individual scraped records for display
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analytics_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_token TEXT NOT NULL,
                run_token TEXT NOT NULL,
                record_index INTEGER,
                record_data TEXT NOT NULL,
                stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_token, run_token, record_index)
            )
        ''')

        # Incremental scraping sessions - tracks overall scraping campaign
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scraping_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_token TEXT NOT NULL,
                project_name TEXT NOT NULL,
                total_pages_target INTEGER NOT NULL,
                current_iteration INTEGER DEFAULT 1,
                pages_completed INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                UNIQUE(project_token, total_pages_target)
            )
        ''')

        # Iteration runs - tracks each ParseHub run in the session
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS iteration_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                iteration_number INTEGER NOT NULL,
                parsehub_project_token TEXT NOT NULL,
                parsehub_project_name TEXT NOT NULL,
                start_page_number INTEGER NOT NULL,
                end_page_number INTEGER NOT NULL,
                pages_in_this_run INTEGER NOT NULL,
                run_token TEXT NOT NULL,
                csv_data TEXT,
                records_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES scraping_sessions(id)
            )
        ''')

        # Combined scraped data - consolidated final results
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS combined_scraped_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                consolidated_csv TEXT,
                total_records INTEGER DEFAULT 0,
                total_pages_scraped INTEGER DEFAULT 0,
                deduplicated_record_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id),
                FOREIGN KEY (session_id) REFERENCES scraping_sessions(id)
            )
        ''')

        # URL patterns - store detected patterns for pagination
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS url_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_token TEXT UNIQUE NOT NULL,
                original_url TEXT NOT NULL,
                pattern_type TEXT,
                pattern_regex TEXT,
                last_page_placeholder TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_token) REFERENCES projects(token)
            )
        ''')

        # Import batches - track Excel metadata imports
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                record_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                error_message TEXT,
                uploaded_by TEXT,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Metadata table - project metadata sourced from Excel imports
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                personal_project_id TEXT UNIQUE NOT NULL,
                project_id INTEGER,
                project_token TEXT UNIQUE,
                project_name TEXT NOT NULL,
                last_run_date TIMESTAMP,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                region TEXT,
                country TEXT,
                brand TEXT,
                website_url TEXT,
                total_pages INTEGER,
                total_products INTEGER,
                current_page_scraped INTEGER DEFAULT 0,
                current_product_scraped INTEGER DEFAULT 0,
                last_known_url TEXT,
                import_batch_id INTEGER,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY (project_token) REFERENCES projects(token)
            )
        ''')

        # Create indexes for metadata filtering and sorting
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_metadata_region ON metadata(region)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_metadata_country ON metadata(country)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_metadata_brand ON metadata(brand)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_metadata_updated_date ON metadata(updated_date)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_metadata_status ON metadata(status)')

        # Add missing columns to runs table if they don't exist (migration for existing DBs)
        try:
            cursor.execute(
                'ALTER TABLE runs ADD COLUMN is_continuation BOOLEAN DEFAULT 0')
        except:
            pass  # Column already exists

        try:
            cursor.execute(
                'ALTER TABLE runs ADD COLUMN completion_percentage REAL DEFAULT 0')
        except:
            pass  # Column already exists

        # Product data table - stores actual scraped product data
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS product_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                run_id INTEGER,
                run_token TEXT,
                name TEXT,
                part_number TEXT,
                brand TEXT,
                list_price REAL,
                sale_price REAL,
                case_unit_price REAL,
                country TEXT,
                currency TEXT,
                product_url TEXT,
                page_number INTEGER,
                extraction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE,
                UNIQUE(project_id, run_token, product_url, page_number)
            )
        ''')

        # Create indexes for efficient querying
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_product_data_project_id ON product_data(project_id)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_product_data_run_id ON product_data(run_id)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_product_data_run_token ON product_data(run_token)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_product_data_brand ON product_data(brand)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_product_data_country ON product_data(country)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_product_data_extraction_date ON product_data(extraction_date)')

        conn.commit()
        self.disconnect()

    def add_project(self, token: str, title: str, owner_email: str = None, main_site: str = None):
        """Add or update project"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO projects (token, title, owner_email, main_site, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ''', (token, title, owner_email, main_site))

        conn.commit()
        self.disconnect()

    def add_run(self, project_token: str, run_token: str, status: str, pages: int,
                start_time: str, end_time: str = None, data_file: str = None, is_empty: bool = False):
        """Add a new run record"""
        conn = self.connect()
        cursor = self.cursor()

        # Get project ID
        cursor.execute(
            'SELECT id FROM projects WHERE token = %s', (project_token,))
        project = cursor.fetchone()

        if not project:
            self.disconnect()
            return None

        project_id = project['id']
        duration = None

        if start_time and end_time:
            try:
                start = datetime.fromisoformat(start_time)
                end = datetime.fromisoformat(end_time)
                duration = int((end - start).total_seconds())
            except:
                pass

        cursor.execute('''
            INSERT INTO runs 
            (project_id, run_token, status, pages_scraped, start_time, end_time, duration_seconds, data_file, is_empty)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (project_id, run_token, status, pages, start_time, end_time, duration, data_file, is_empty))

        conn.commit()
        run_id = cursor.lastrowid
        self.disconnect()

        return run_id

    def store_scraped_data(self, run_id: int, project_id: int = None, data: dict | list = None):
        """Store scraped data from JSON"""
        conn = self.connect()
        cursor = self.cursor()

        # If project_id not provided, get it from run
        if project_id is None:
            cursor.execute(
                'SELECT project_id FROM runs WHERE id = %s', (run_id,))
            run = cursor.fetchone()
            if run:
                project_id = run['project_id']
            else:
                self.disconnect()
                return 0

        records = 0

        if isinstance(data, list):
            # Array of records
            for item in data:
                if isinstance(item, dict):
                    for key, value in item.items():
                        cursor.execute('''
                            INSERT INTO scraped_data (run_id, project_id, data_key, data_value)
                            VALUES (%s, %s, %s, %s)
                        ''', (run_id, project_id, key, str(value)))
                    records += 1
        elif isinstance(data, dict):
            # Check if it contains an array (like { product: [...] })
            for key, value in data.items():
                if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                    # This is the data array
                    for item in value:
                        for field, field_value in item.items():
                            cursor.execute('''
                                INSERT INTO scraped_data (run_id, project_id, data_key, data_value)
                                VALUES (%s, %s, %s, %s)
                            ''', (run_id, project_id, field, str(field_value)))
                        records += 1
                    break

        # Update records count in runs table
        cursor.execute(
            'UPDATE runs SET records_count = %s WHERE id = %s', (records, run_id))

        conn.commit()
        self.disconnect()

        return records

    def get_project_analytics(self, project_token: str) -> dict:
        """Get analytics for a specific project"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute(
            'SELECT id FROM projects WHERE token = %s', (project_token,))
        project = cursor.fetchone()

        if not project:
            self.disconnect()
            return None

        project_id = project['id']

        # Total runs
        cursor.execute(
            'SELECT COUNT(*) as count FROM runs WHERE project_id = %s', (project_id,))
        total_runs = cursor.fetchone()['count']

        # Completed runs
        cursor.execute('SELECT COUNT(*) as count FROM runs WHERE project_id = %s AND status = %s',
                       (project_id, 'complete'))
        completed_runs = cursor.fetchone()['count']

        # Total records scraped
        cursor.execute(
            'SELECT SUM(records_count) as total FROM runs WHERE project_id = %s', (project_id,))
        total_records = cursor.fetchone()['total'] or 0

        # Average duration
        cursor.execute('''
            SELECT AVG(duration_seconds) as avg_duration FROM runs 
            WHERE project_id = %s AND duration_seconds IS NOT NULL AND status = %s
        ''', (project_id, 'complete'))
        avg_duration = cursor.fetchone()['avg_duration'] or 0

        # Latest run
        cursor.execute('''
            SELECT run_token, status, pages_scraped, start_time, records_count FROM runs 
            WHERE project_id = %s ORDER BY created_at DESC LIMIT 1
        ''', (project_id,))
        latest_run = cursor.fetchone()

        # Pages scraped trend (last 10 runs)
        cursor.execute('''
            SELECT pages_scraped, start_time FROM runs 
            WHERE project_id = %s ORDER BY created_at DESC LIMIT 10
        ''', (project_id,))
        pages_trend = [dict(row) for row in cursor.fetchall()]

        self.disconnect()

        return {
            'project_token': project_token,
            'total_runs': total_runs,
            'completed_runs': completed_runs,
            'total_records': int(total_records),
            'avg_duration': round(avg_duration, 2),
            'latest_run': dict(latest_run) if latest_run else None,
            'pages_trend': pages_trend
        }

    def get_all_analytics(self) -> list:
        """Get analytics for all projects"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute('SELECT token FROM projects')
        projects = cursor.fetchall()
        self.disconnect()

        analytics = []
        for project in projects:
            analytics.append(self.get_project_analytics(project['token']))

        return analytics

    def import_from_json(self, json_file: str, project_token: str, run_token: str,
                         status: str, pages: int, start_time: str, end_time: str = None):
        """Import data from JSON file into database"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Ensure project exists
            conn = self.connect()
            cursor = self.cursor()
            cursor.execute(
                'SELECT id FROM projects WHERE token = %s', (project_token,))
            project = cursor.fetchone()
            self.disconnect()

            if not project:
                return None

            project_id = project['id']

            # Add run record
            run_id = self.add_run(
                project_token=project_token,
                run_token=run_token,
                status=status,
                pages=pages,
                start_time=start_time,
                end_time=end_time,
                data_file=json_file,
                is_empty=len(str(data)) < 10
            )

            if run_id:
                # Store the data with proper project_id
                records = self.store_scraped_data(run_id, project_id, data)
                return {'run_id': run_id, 'records': records}
        except Exception as e:
            print(f"Error importing JSON: {e}")
            return None

    def export_data(self, project_token: str, format: str = 'json') -> str | None:
        """Export all project data"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute(
            'SELECT id FROM projects WHERE token = %s', (project_token,))
        project = cursor.fetchone()

        if not project:
            self.disconnect()
            return None

        project_id = project['id']

        cursor.execute('''
            SELECT run_token, status, pages_scraped, start_time, records_count 
            FROM runs WHERE project_id = %s ORDER BY created_at DESC
        ''', (project_id,))

        runs = [dict(row) for row in cursor.fetchall()]

        if format == 'json':
            self.disconnect()
            return json.dumps({'runs': runs, 'project_token': project_token}, indent=2)

        self.disconnect()
        return None

    # ========== RECOVERY OPERATIONS ==========

    def create_recovery_operation(self, original_run_id: int, project_id: int,
                                  last_product_url: str, last_product_name: str = None) -> int:
        """Create a new recovery operation record"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute('''
            INSERT INTO recovery_operations 
            (original_run_id, project_id, last_product_url, last_product_name, 
             stopped_timestamp, recovery_triggered_timestamp, status)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'pending')
        ''', (original_run_id, project_id, last_product_url, last_product_name))

        conn.commit()
        recovery_id = cursor.lastrowid
        self.disconnect()
        return recovery_id

    def link_recovery_run(self, recovery_operation_id: int, recovery_run_id: int):
        """Link a recovery run to a recovery operation"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute('''
            UPDATE recovery_operations 
            SET recovery_run_id = %s, recovery_started_timestamp = CURRENT_TIMESTAMP, status = 'in_progress'
            WHERE id = %s
        ''', (recovery_run_id, recovery_operation_id))

        conn.commit()
        self.disconnect()

    def complete_recovery_operation(self, recovery_operation_id: int,
                                    final_count: int, duplicates: int):
        """Mark recovery operation as complete"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute('''
            UPDATE recovery_operations 
            SET recovery_completed_timestamp = CURRENT_TIMESTAMP, 
                status = 'completed',
                final_data_count = %s,
                duplicates_removed = %s
            WHERE id = %s
        ''', (final_count, duplicates, recovery_operation_id))

        conn.commit()
        self.disconnect()

    def get_last_product(self, run_id: int) -> dict:
        """Get the last product scraped from a run"""
        conn = self.connect()
        cursor = self.cursor()

        cursor.execute('''
            SELECT data_key, data_value FROM scraped_data 
            WHERE run_id = %s 
            ORDER BY created_at DESC 
            LIMIT 1
        ''', (run_id,))

        result = cursor.fetchone()
        self.disconnect()
        return dict(result) if result else None

    def get_run_data_summary(self, run_id: int) -> dict:
        """Get summary of all data for a run"""
        conn = self.connect()
        cursor = self.cursor()

        # Get run info
        cursor.execute('''
            SELECT r.run_token, r.status, r.pages_scraped, r.start_time, 
                   r.records_count, p.token as project_token
            FROM runs r
            JOIN projects p ON r.project_id = p.id
            WHERE r.id = %s
        ''', (run_id,))

        run_info = cursor.fetchone()

        if not run_info:
            self.disconnect()
            return None

        # Get sample of data
        cursor.execute('''
            SELECT data_key, data_value FROM scraped_data 
            WHERE run_id = %s 
            ORDER BY created_at DESC 
            LIMIT 5
        ''', (run_id,))

        sample_data = [dict(row) for row in cursor.fetchall()]

        self.disconnect()

        return {
            'run_token': run_info['run_token'],
            'status': run_info['status'],
            'pages_scraped': run_info['pages_scraped'],
            'start_time': run_info['start_time'],
            'records_count': run_info['records_count'],
            'project_token': run_info['project_token'],
            'sample_data': sample_data
        }

    def get_unique_product_urls(self, run_id: int) -> list:
        """Extract unique product URLs from scraped data"""
        conn = self.connect()
        cursor = self.cursor()

        # Try common field names for URLs
        url_fields = ['url', 'product_url', 'link', 'href', 'page_url']

        for field in url_fields:
            cursor.execute('''
                SELECT DISTINCT data_value FROM scraped_data 
                WHERE run_id = %s AND data_key = %s AND data_value LIKE 'http%'
                ORDER BY created_at DESC
            ''', (run_id, field))

            urls = [row['data_value'] for row in cursor.fetchall()]
            if urls:
                self.disconnect()
                return urls

        self.disconnect()
        return []

    def record_data_lineage(self, run_id: int, product_urls: list, recovery_op_id: int = None):
        """Record which products came from which run for deduplication"""
        conn = self.connect()
        cursor = self.cursor()

        for url in product_urls:
            # Create hash of URL for quick duplicate detection
            import hashlib
            product_hash = hashlib.md5(url.encode()).hexdigest()

            cursor.execute('''
                INSERT INTO data_lineage 
                (source_run_id, recovery_operation_id, product_url, product_hash)
                VALUES (%s, %s, %s, %s)
            ''', (run_id, recovery_op_id, url, product_hash))

        conn.commit()
        self.disconnect()

    def get_recovery_status(self, project_id: int) -> dict:
        """Get current recovery status for a project"""
        conn = self.connect()
        cursor = self.cursor()

        # Latest recovery operation
        cursor.execute('''
            SELECT * FROM recovery_operations 
            WHERE project_id = %s 
            ORDER BY created_at DESC 
            LIMIT 1
        ''', (project_id,))

        recovery = cursor.fetchone()
        self.disconnect()

        if not recovery:
            return {'status': 'none', 'in_recovery': False}

        return {
            'status': recovery['status'],
            'in_recovery': recovery['status'] in ['pending', 'in_progress'],
            'last_product_url': recovery['last_product_url'],
            'last_product_name': recovery['last_product_name'],
            'stopped_timestamp': recovery['stopped_timestamp'],
            'recovery_triggered_timestamp': recovery['recovery_triggered_timestamp'],
            'attempt_number': recovery['attempt_number'],
            'original_data_count': recovery['original_data_count'],
            'recovery_data_count': recovery['recovery_data_count'],
            'final_data_count': recovery['final_data_count'],
            'duplicates_removed': recovery['duplicates_removed']
        }

    def get_analytics_data(self, project_id: int) -> dict:
        """Get comprehensive analytics for a project"""
        conn = self.connect()
        cursor = self.cursor()

        # Get all runs for this project
        cursor.execute('''
            SELECT id, run_token, status, pages_scraped, start_time, 
                   end_time, duration_seconds, records_count, created_at
            FROM runs 
            WHERE project_id = %s 
            ORDER BY created_at DESC
        ''', (project_id,))

        runs = [dict(row) for row in cursor.fetchall()]

        # Get total and unique records
        cursor.execute('''
            SELECT COUNT(*) as total_records FROM scraped_data
            WHERE project_id = %s
        ''', (project_id,))

        total_records = cursor.fetchone()['total_records']

        # Get latest run info
        cursor.execute('''
            SELECT records_count, pages_scraped, status FROM runs
            WHERE project_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        ''', (project_id,))

        latest_run = cursor.fetchone()

        # Check for active recovery
        recovery_status = self.get_recovery_status(project_id)

        self.disconnect()

        return {
            'total_runs': len(runs),
            'total_records': total_records,
            'latest_run': dict(latest_run) if latest_run else None,
            'runs_history': runs[:10],  # Last 10 runs
            'recovery_status': recovery_status,
            'scraping_rate': self._calculate_scraping_rate(runs)
        }

    def _calculate_scraping_rate(self, runs: list) -> dict:
        """Calculate scraping rate (items per minute)"""
        if not runs or len(runs) < 2:
            return {'items_per_minute': 0, 'estimated_total': 0}

        completed_runs = [
            r for r in runs if r['duration_seconds'] and r['records_count']]
        if not completed_runs:
            return {'items_per_minute': 0, 'estimated_total': 0}

        avg_rate = sum(r['records_count'] / (r['duration_seconds'] / 60)
                       for r in completed_runs) / len(completed_runs)

        # If current run is in progress, estimate total
        current_run = runs[0]
        estimated_total = current_run['records_count']
        if current_run['status'] != 'complete' and avg_rate > 0 and current_run['duration_seconds']:
            # Estimate based on current progress and average rate
            minutes_elapsed = current_run['duration_seconds'] / 60
            # 1.5x multiplier for estimated completion
            estimated_total = int(avg_rate * minutes_elapsed * 1.5)

        return {
            'items_per_minute': round(avg_rate, 2),
            'estimated_total': estimated_total
        }

    def create_project_with_pages(self, token: str, title: str, url: str,
                                  target_pages: int = 1) -> bool:
        """
        Create a project with target pages for scraping

        Args:
            token: ParseHub API token
            title: Project title
            url: Target URL to scrape
            target_pages: Number of pages to scrape
        """
        self.connect()
        cursor = self.cursor()

        try:
            # First, add the project
            cursor.execute('''
                INSERT INTO projects (token, title, url)
                VALUES (%s, %s, %s)
            ''', (token, title, url))

            project_id = cursor.lastrowid

            # Store target pages in run_checkpoints
            cursor.execute('''
                INSERT INTO run_checkpoints 
                (project_id, checkpoint_type, checkpoint_data, created_at)
                VALUES (%s, 'target_pages', %s, CURRENT_TIMESTAMP)
            ''', (project_id, json.dumps({
                'target_pages': target_pages,
                'url': url,
                'created_at': datetime.now().isoformat()
            })))

            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error creating project with pages: {e}")
            return False
        finally:
            self.disconnect()

    def get_last_scraped_page(self, project_id: int) -> Optional[int]:
        """
        Get the last (highest) page number scraped for a project

        Returns:
            Page number or None if no pages scraped
        """
        self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT json_extract(data, '$.page_number') as page_number
                FROM scraped_data
                WHERE project_id = %s
                ORDER BY CAST(json_extract(data, '$.page_number') AS INTEGER) DESC
                LIMIT 1
            ''', (project_id,))

            result = cursor.fetchone()
            return int(result[0]) if result and result[0] else None
        except Exception as e:
            print(f"Error getting last scraped page: {e}")
            return None
        finally:
            self.disconnect()

    def get_total_scraped_count(self, project_id: int) -> int:
        """Get total number of records scraped for a project"""
        self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT COUNT(*) as total
                FROM scraped_data
                WHERE project_id = %s
            ''', (project_id,))

            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            print(f"Error getting scraped count: {e}")
            return 0
        finally:
            self.disconnect()

    def get_target_pages(self, project_id: int) -> Optional[int]:
        """Get the target page count for a project"""
        self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT checkpoint_data
                FROM run_checkpoints
                WHERE project_id = %s AND checkpoint_type = 'target_pages'
                ORDER BY created_at DESC
                LIMIT 1
            ''', (project_id,))

            result = cursor.fetchone()
            if result:
                data = json.loads(result[0])
                return data.get('target_pages')
            return None
        except Exception as e:
            print(f"Error getting target pages: {e}")
            return None
        finally:
            self.disconnect()

    def record_scraped_data_with_page(self, run_id: int, project_id: int,
                                      page_number: int, data: dict) -> bool:
        """
        Record scraped data with page tracking

        Args:
            run_id: Run ID
            project_id: Project ID
            page_number: Current page number
            data: Scraped data (dict)
        """
        self.connect()
        cursor = self.cursor()

        try:
            # Add page_number to data
            data_with_page = dict(data)
            data_with_page['page_number'] = page_number

            cursor.execute('''
                INSERT INTO scraped_data (run_id, project_id, data)
                VALUES (%s, %s, %s)
            ''', (run_id, project_id, json.dumps(data_with_page)))

            # Update run checkpoint
            cursor.execute('''
                INSERT OR REPLACE INTO run_checkpoints
                (project_id, checkpoint_type, checkpoint_data, created_at)
                VALUES (%s, 'last_page', %s, CURRENT_TIMESTAMP)
            ''', (project_id, json.dumps({
                'last_page': page_number,
                'total_records': self.get_total_scraped_count(project_id),
                'timestamp': datetime.now().isoformat()
            })))

            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error recording scraped data with page: {e}")
            return False
        finally:
            self.disconnect()

    def get_pagination_checkpoint(self, project_id: int) -> dict:
        """
        Get pagination checkpoint data for a project

        Returns:
            {
                'last_page': int,
                'target_pages': int,
                'total_records': int,
                'pages_remaining': int,
                'completion_percentage': float
            }
        """
        self.connect()
        cursor = self.cursor()

        try:
            # Get last page and target pages
            cursor.execute('''
                SELECT checkpoint_data
                FROM run_checkpoints
                WHERE project_id = %s AND checkpoint_type IN ('last_page', 'target_pages')
                ORDER BY created_at DESC
            ''', (project_id,))

            checkpoints = cursor.fetchall()
            last_page_data = None
            target_pages_data = None

            for checkpoint in checkpoints:
                data = json.loads(checkpoint[0])
                if 'last_page' in data:
                    last_page_data = data
                elif 'target_pages' in data:
                    target_pages_data = data

            last_page = last_page_data['last_page'] if last_page_data else 0
            target_pages = target_pages_data['target_pages'] if target_pages_data else 0
            total_records = last_page_data.get(
                'total_records', self.get_total_scraped_count(project_id)) if last_page_data else 0

            pages_remaining = max(
                0, target_pages - last_page) if target_pages else 0
            completion_pct = (last_page / target_pages *
                              100) if target_pages > 0 else 0

            return {
                'last_page': last_page,
                'target_pages': target_pages,
                'total_records': total_records,
                'pages_remaining': pages_remaining,
                'completion_percentage': round(completion_pct, 2)
            }
        except Exception as e:
            print(f"Error getting pagination checkpoint: {e}")
            return None
        finally:
            self.disconnect()

    # ========== REAL-TIME MONITORING METHODS ==========

    def create_monitoring_session(self, project_id: int, run_token: str, target_pages: int = 1) -> int:
        """
        Create a new monitoring session for real-time data collection

        Args:
            project_id: Project ID
            run_token: ParseHub run token
            target_pages: Target number of pages to scrape

        Returns:
            Session ID or None if failed
        """
        conn = self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                INSERT INTO monitoring_sessions 
                (project_id, run_token, target_pages, status, start_time, created_at, updated_at)
                VALUES (%s, %s, %s, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ''', (project_id, run_token, target_pages))

            conn.commit()
            session_id = cursor.lastrowid
            return session_id
        except Exception as e:
            print(f"Error creating monitoring session: {e}")
            return None
        finally:
            self.disconnect()

    def update_monitoring_session(self, session_id: int, status: str = None,
                                  total_records: int = None, total_pages: int = None,
                                  progress_percentage: float = None, current_url: str = None,
                                  error_message: str = None) -> bool:
        """
        Update monitoring session with current status

        Args:
            session_id: Session ID to update
            status: New status ('active', 'completed', 'failed', 'cancelled')
            total_records: Total records found so far
            total_pages: Total pages scraped so far
            progress_percentage: Progress percentage (0-100)
            current_url: Current URL being scraped
            error_message: Error message if any

        Returns:
            True if update successful, False otherwise
        """
        conn = self.connect()
        cursor = self.cursor()

        try:
            update_fields = ['updated_at = CURRENT_TIMESTAMP']
            update_params = []

            if status is not None:
                update_fields.append('status = %s')
                update_params.append(status)

            if total_records is not None:
                update_fields.append('total_records = %s')
                update_params.append(total_records)

            if total_pages is not None:
                update_fields.append('total_pages = %s')
                update_params.append(total_pages)

            if progress_percentage is not None:
                update_fields.append('progress_percentage = %s')
                update_params.append(progress_percentage)

            if current_url is not None:
                update_fields.append('current_url = %s')
                update_params.append(current_url)

            if error_message is not None:
                update_fields.append('error_message = %s')
                update_params.append(error_message)

            if status == 'completed' or status == 'failed':
                update_fields.append('end_time = CURRENT_TIMESTAMP')

            update_params.append(session_id)

            query = f'''
                UPDATE monitoring_sessions 
                SET {', '.join(update_fields)}
                WHERE id = %s
            '''

            cursor.execute(query, tuple(update_params))
            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating monitoring session: {e}")
            return False
        finally:
            self.disconnect()

    def store_scraped_records(self, session_id: int, project_id: int, run_token: str,
                              records: list, page_number: int) -> int:
        """
        Store scraped records from a page with deduplication

        Args:
            session_id: Monitoring session ID
            project_id: Project ID
            run_token: ParseHub run token
            records: List of data records (dicts)
            page_number: Current page number

        Returns:
            Number of records stored (new records)
        """
        import hashlib

        conn = self.connect()
        cursor = self.cursor()

        records_stored = 0

        try:
            for record in records:
                # Create hash of record data for deduplication
                record_json = json.dumps(record, sort_keys=True)
                data_hash = hashlib.md5(record_json.encode()).hexdigest()

                try:
                    cursor.execute('''
                        INSERT INTO scraped_records 
                        (session_id, project_id, run_token, page_number, data_hash, data_json)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (session_id, project_id, run_token, page_number, data_hash, record_json))
                    records_stored += 1
                except sqlite3.IntegrityError:
                    # Record already exists (duplicate), skip
                    pass

            conn.commit()
            return records_stored
        except Exception as e:
            print(f"Error storing scraped records: {e}")
            return 0
        finally:
            self.disconnect()

    def get_session_records(self, session_id: int, limit: int = 100, offset: int = 0) -> list:
        """
        Get paginated records from a monitoring session

        Args:
            session_id: Monitoring session ID
            limit: Number of records to fetch
            offset: Number of records to skip

        Returns:
            List of records as dicts
        """
        conn = self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT id, page_number, data_json, created_at
                FROM scraped_records
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT %s OFFSET %s
            ''', (session_id, limit, offset))

            records = cursor.fetchall()
            result = []

            for record in records:
                data = json.loads(record['data_json'])
                result.append({
                    'id': record['id'],
                    'page_number': record['page_number'],
                    'data': data,
                    'created_at': record['created_at']
                })

            return result
        except Exception as e:
            print(f"Error getting session records: {e}")
            return []
        finally:
            self.disconnect()

    def get_session_records_count(self, session_id: int) -> int:
        """Get total number of records in a session"""
        conn = self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT COUNT(*) as total FROM scraped_records WHERE session_id = %s
            ''', (session_id,))

            result = cursor.fetchone()
            return result['total'] if result else 0
        except Exception as e:
            print(f"Error getting session records count: {e}")
            return 0
        finally:
            self.disconnect()

    def get_session_summary(self, session_id: int) -> dict:
        """
        Get summary of a monitoring session

        Args:
            session_id: Monitoring session ID

        Returns:
            Session summary dict with status, counts, timing
        """
        conn = self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT id, project_id, run_token, target_pages, status, 
                       start_time, end_time, total_records, total_pages, 
                       progress_percentage, current_url, error_message, created_at
                FROM monitoring_sessions
                WHERE id = %s
            ''', (session_id,))

            session = cursor.fetchone()

            if not session:
                return None

            # Get actual record count
            cursor.execute('''
                SELECT COUNT(*) as total, 
                       COUNT(DISTINCT page_number) as pages,
                       MAX(page_number) as max_page
                FROM scraped_records
                WHERE session_id = %s
            ''', (session_id,))

            counts = cursor.fetchone()

            return {
                'session_id': session['id'],
                'project_id': session['project_id'],
                'run_token': session['run_token'],
                'target_pages': session['target_pages'],
                'status': session['status'],
                'start_time': session['start_time'],
                'end_time': session['end_time'],
                'total_records': counts['total'],
                'total_pages': counts['pages'] or 0,
                'max_page_scraped': counts['max_page'],
                'progress_percentage': session['progress_percentage'],
                'current_url': session['current_url'],
                'error_message': session['error_message'],
                'created_at': session['created_at']
            }
        except Exception as e:
            print(f"Error getting session summary: {e}")
            return None
        finally:
            self.disconnect()

    def get_data_as_csv(self, session_id: int) -> str:
        """
        Export session data as CSV

        Args:
            session_id: Monitoring session ID

        Returns:
            CSV string or None if no records
        """
        import csv
        from io import StringIO

        records = self.get_session_records(session_id, limit=1000000, offset=0)

        if not records:
            return None

        try:
            # Get all unique keys across all records
            all_keys = set()
            for record in records:
                if isinstance(record['data'], dict):
                    all_keys.update(record['data'].keys())

            all_keys = sorted(list(all_keys))

            # Add metadata columns
            columns = ['page_number', 'created_at'] + all_keys

            # Create CSV
            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()

            for record in records:
                row = {
                    'page_number': record['page_number'],
                    'created_at': record['created_at']
                }

                if isinstance(record['data'], dict):
                    row.update(record['data'])

                writer.writerow({k: row.get(k, '') for k in columns})

            return output.getvalue()
        except Exception as e:
            print(f"Error converting to CSV: {e}")
            return None

    def get_monitoring_status_for_project(self, project_id: int) -> dict:
        """
        Get latest monitoring session status for a project

        Args:
            project_id: Project ID

        Returns:
            Latest session summary or None
        """
        conn = self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT id FROM monitoring_sessions
                WHERE project_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            ''', (project_id,))

            session = cursor.fetchone()

            if not session:
                return None

            return self.get_session_summary(session['id'])
        except Exception as e:
            print(f"Error getting monitoring status: {e}")
            return None
        finally:
            self.disconnect()

    def store_analytics_data(self, project_token: str, run_token: str, analytics_data: dict, records: list, csv_data: str = None):
        """Store analytics data and records to database with improved error handling"""
        try:
            # Validate inputs
            if not project_token or not isinstance(project_token, str):
                raise ValueError(f"Invalid project_token: {project_token}")
            if not run_token or not isinstance(run_token, str):
                raise ValueError(f"Invalid run_token: {run_token}")
            if not isinstance(analytics_data, dict):
                raise ValueError(
                    f"analytics_data must be dict, got {type(analytics_data)}")
            if not isinstance(records, list):
                raise ValueError(f"records must be list, got {type(records)}")

            conn = self.connect()
            if not conn:
                raise Exception("Failed to establish database connection")

            cursor = self.cursor()

            # Validate and serialize analytics data
            try:
                analytics_json = json.dumps(analytics_data, default=str)
                # Verify the JSON is valid by parsing it back
                json.loads(analytics_json)
            except Exception as e:
                print(
                    f"Error serializing analytics_data: {e}, attempting to extract valid fields...")
                # Fallback: extract only serializable fields
                safe_data = {}
                for key, value in analytics_data.items():
                    try:
                        test_json = json.dumps({key: value}, default=str)
                        safe_data[key] = value
                    except:
                        safe_data[key] = str(value)
                analytics_json = json.dumps(safe_data, default=str)

            # Store analytics cache with error handling
            try:
                overview = analytics_data.get('overview', {})
                data_quality = analytics_data.get('data_quality', {})
                recovery = analytics_data.get('recovery', {})

                total_records = overview.get('total_records_scraped', 0)
                total_fields = data_quality.get('total_fields', 0)
                total_runs = overview.get('total_runs', 0)
                completed_runs = overview.get('completed_runs', 0)
                progress_percentage = overview.get('progress_percentage', 0)
                status = recovery.get('status', 'unknown')

                cursor.execute('''
                    INSERT OR REPLACE INTO analytics_cache
                    (project_token, run_token, total_records, total_fields, total_runs, completed_runs, 
                     progress_percentage, status, analytics_json, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    project_token,
                    run_token,
                    total_records,
                    total_fields,
                    total_runs,
                    completed_runs,
                    progress_percentage,
                    status,
                    analytics_json,
                    datetime.now().isoformat()
                ))
            except Exception as e:
                print(f"Warning: Failed to store analytics cache: {e}")
                # Attempt to continue with record storage

            # Store CSV data if provided
            if csv_data:
                try:
                    cursor.execute('''
                        INSERT OR REPLACE INTO csv_exports
                        (project_token, run_token, csv_data, row_count, updated_at)
                        VALUES (%s, %s, %s, %s, %s)
                    ''', (
                        project_token,
                        run_token,
                        csv_data,
                        len(records),
                        datetime.now().isoformat()
                    ))
                except Exception as e:
                    print(f"Warning: Failed to store CSV data: {e}")

            # Store individual records with batch processing
            stored_count = 0
            batch_size = 100
            for idx in range(0, len(records), batch_size):
                batch = records[idx:idx + batch_size]
                for i, record in enumerate(batch):
                    try:
                        record_json = json.dumps(record, default=str) if isinstance(
                            record, dict) else str(record)
                        cursor.execute('''
                            INSERT OR REPLACE INTO analytics_records
                            (project_token, run_token, record_index, record_data)
                            VALUES (%s, %s, %s, %s)
                        ''', (
                            project_token,
                            run_token,
                            idx + i,
                            record_json
                        ))
                        stored_count += 1
                    except Exception as e:
                        print(
                            f"Warning: Failed to store record {idx + i}: {e}")
                        continue

            conn.commit()
            self.disconnect()
            print(
                f"Successfully stored analytics: {stored_count} records, {total_records} total_records")
            return True

        except Exception as e:
            print(f"Error storing analytics data: {e}")
            import traceback
            traceback.print_exc()
            self.disconnect()
            return False

    def get_analytics_data(self, project_token: str):
        """Retrieve stored analytics data"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            # Get analytics cache
            cursor.execute('''
                SELECT project_token, run_token, total_records, total_fields, total_runs, 
                       completed_runs, progress_percentage, status, analytics_json, updated_at
                FROM analytics_cache
                WHERE project_token = %s
                ORDER BY updated_at DESC
                LIMIT 1
            ''', (project_token,))

            analytics_row = cursor.fetchone()

            if not analytics_row:
                self.disconnect()
                return None

            analytics = json.loads(analytics_row['analytics_json'])

            # Get CSV data
            cursor.execute('''
                SELECT csv_data FROM csv_exports
                WHERE project_token = %s
                ORDER BY updated_at DESC
                LIMIT 1
            ''', (project_token,))

            csv_row = cursor.fetchone()
            if csv_row and csv_row['csv_data']:
                analytics['csv_data'] = csv_row['csv_data']

            # Get records
            cursor.execute('''
                SELECT record_data FROM analytics_records
                WHERE project_token = %s
                ORDER BY record_index ASC
            ''', (project_token,))

            records = []
            for row in cursor.fetchall():
                try:
                    records.append(json.loads(row['record_data']))
                except:
                    records.append(row['record_data'])

            if records:
                analytics['raw_data'] = records

            self.disconnect()
            return analytics

        except Exception as e:
            print(f"Error retrieving analytics data: {e}")
            self.disconnect()
            return None

    def clear_analytics_data(self, project_token: str):
        """Clear analytics data for a project"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute(
                'DELETE FROM analytics_cache WHERE project_token = %s', (project_token,))
            cursor.execute(
                'DELETE FROM csv_exports WHERE project_token = %s', (project_token,))
            cursor.execute(
                'DELETE FROM analytics_records WHERE project_token = %s', (project_token,))

            conn.commit()
            self.disconnect()
            return True

        except Exception as e:
            print(f"Error clearing analytics data: {e}")
            self.disconnect()
            return False

    # ===== METADATA MANAGEMENT METHODS =====

    def create_import_batch(self, file_name: str, record_count: int = 0, uploaded_by: str = None) -> int:
        """Create an import batch record and return batch_id"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute('''
                INSERT INTO import_batches (file_name, record_count, uploaded_by, status)
                VALUES (%s, %s, %s, %s)
            ''', (file_name, record_count, uploaded_by, 'success'))

            conn.commit()
            batch_id = cursor.lastrowid
            self.disconnect()
            return batch_id

        except Exception as e:
            print(f"Error creating import batch: {e}")
            self.disconnect()
            return None

    def add_metadata_record(self, personal_project_id: str, project_name: str,
                            region: str = None, country: str = None, brand: str = None,
                            website_url: str = None, total_pages: int = None,
                            total_products: int = None, import_batch_id: int = None,
                            project_token: str = None, project_id: int = None) -> int:
        """Add or update a metadata record"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO metadata 
                (personal_project_id, project_id, project_token, project_name, 
                 region, country, brand, website_url, total_pages, total_products,
                 import_batch_id, updated_date, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                personal_project_id, project_id, project_token, project_name,
                region, country, brand, website_url, total_pages, total_products,
                import_batch_id, datetime.now().isoformat(), 'pending'
            ))

            conn.commit()
            metadata_id = cursor.lastrowid
            self.disconnect()
            return metadata_id

        except Exception as e:
            print(f"Error adding metadata record: {e}")
            self.disconnect()
            return None

    def get_metadata_filtered(self, project_token: str = None, region: str = None, country: str = None,
                              brand: str = None, limit: int = 100, offset: int = 0):
        """Get metadata records with optional filters"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            query = "SELECT * FROM metadata WHERE 1=1"
            params = []

            if project_token:
                query += " AND project_token = %s"
                params.append(project_token)
            if region:
                query += " AND region = %s"
                params.append(region)
            if country:
                query += " AND country = %s"
                params.append(country)
            if brand:
                query += " AND brand = %s"
                params.append(brand)

            # Sort by updated_date descending
            query += " ORDER BY updated_date DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            cursor.execute(query, params)
            records = [dict(row) for row in cursor.fetchall()]

            self.disconnect()
            return records

        except Exception as e:
            print(f"Error getting metadata: {e}")
            self.disconnect()
            return []

    def get_metadata_by_id(self, metadata_id: int):
        """Get a specific metadata record by ID"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute(
                "SELECT * FROM metadata WHERE id = %s", (metadata_id,))
            record = cursor.fetchone()

            self.disconnect()
            return dict(record) if record else None

        except Exception as e:
            print(f"Error getting metadata by ID: {e}")
            self.disconnect()
            return None

    def update_metadata_progress(self, metadata_id: int, current_page_scraped: int = None,
                                 current_product_scraped: int = None, last_known_url: str = None,
                                 last_run_date: str = None, completion_percentage: float = None):
        """Update scraping progress in metadata"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            updates = ["updated_date = %s"]
            params = [datetime.now().isoformat()]

            if current_page_scraped is not None:
                updates.append("current_page_scraped = %s")
                params.append(current_page_scraped)
            if current_product_scraped is not None:
                updates.append("current_product_scraped = %s")
                params.append(current_product_scraped)
            if last_known_url is not None:
                updates.append("last_known_url = %s")
                params.append(last_known_url)
            if last_run_date is not None:
                updates.append("last_run_date = %s")
                params.append(last_run_date)

            params.append(metadata_id)

            query = f"UPDATE metadata SET {', '.join(updates)} WHERE id = %s"
            cursor.execute(query, params)

            conn.commit()
            self.disconnect()
            return True

        except Exception as e:
            print(f"Error updating metadata progress: {e}")
            self.disconnect()
            return False

    def get_distinct_filter_values(self, filter_type: str):
        """Get distinct values for a filter (region, country, brand)"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            if filter_type not in ['region', 'country', 'brand']:
                return []

            cursor.execute(
                f"SELECT DISTINCT {filter_type} FROM metadata WHERE {filter_type} IS NOT NULL ORDER BY {filter_type}")
            values = [row[0] for row in cursor.fetchall()]

            self.disconnect()
            return values

        except Exception as e:
            print(f"Error getting filter values: {e}")
            self.disconnect()
            return []

    def get_metadata_by_personal_id(self, personal_project_id: str):
        """Get metadata by personal project ID"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute(
                "SELECT * FROM metadata WHERE personal_project_id = %s", (personal_project_id,))
            record = cursor.fetchone()

            self.disconnect()
            return dict(record) if record else None

        except Exception as e:
            print(f"Error getting metadata by personal ID: {e}")
            self.disconnect()
            return None

    def delete_metadata(self, metadata_id: int):
        """Delete a metadata record"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute("DELETE FROM metadata WHERE id = %s", (metadata_id,))

            conn.commit()
            self.disconnect()
            return True

        except Exception as e:
            print(f"Error deleting metadata: {e}")
            self.disconnect()
            return False

    def get_import_batches(self, limit: int = 50, offset: int = 0):
        """Get import batch history"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute("""
                SELECT id, file_name, record_count, status, uploaded_by, upload_date 
                FROM import_batches 
                ORDER BY upload_date DESC 
                LIMIT %s OFFSET %s
            """, (limit, offset))

            records = cursor.fetchall()
            self.disconnect()

            return [dict(record) for record in records] if records else []

        except Exception as e:
            print(f"Error getting import batches: {e}")
            self.disconnect()
            return []

    def sync_projects(self, projects_list: list) -> dict:
        """Sync projects from ParseHub API to database (insert/update)"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            inserted = 0
            updated = 0

            for project in projects_list:
                token = project.get('token')
                title = project.get('title') or project.get('name', '')
                owner_email = project.get('owner_email')
                main_site = project.get('main_site')

                if not token:
                    continue

                # Try to insert, update if exists
                cursor.execute('''
                    INSERT INTO projects (token, title, owner_email, main_site, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT(token) DO UPDATE SET
                        title = excluded.title,
                        owner_email = excluded.owner_email,
                        main_site = excluded.main_site,
                        updated_at = CURRENT_TIMESTAMP
                ''', (token, title, owner_email, main_site))

                cursor.execute(
                    'SELECT id FROM projects WHERE token = %s', (token,))
                result = cursor.fetchone()

                if cursor.rowcount > 0:
                    if cursor.lastrowid:
                        inserted += 1
                    else:
                        updated += 1

            # Auto-link projects to metadata by project_name matching
            self._link_projects_to_metadata(cursor)

            conn.commit()
            self.disconnect()

            return {
                'success': True,
                'inserted': inserted,
                'updated': updated,
                'total': len(projects_list)
            }
        except Exception as e:
            print(f"Error syncing projects: {e}")
            self.disconnect()
            return {'success': False, 'error': str(e)}

    def sync_metadata_with_projects(self, projects_list: list) -> dict:
        """
        Update metadata.project_token/project_id after fetching ParseHub projects.

        Matching strategy priority:
        1) Exact project title == metadata.project_name (case-insensitive)
        2) Normalized title equality (collapse whitespace)
        3) Website domain extracted from title matched in metadata.website_url
        """
        try:
            conn = self.connect()
            cursor = self.cursor()

            def normalize(text: str) -> str:
                return ' '.join((text or '').strip().lower().split())

            cursor.execute('''
                SELECT id, project_name, website_url, project_token
                FROM metadata
            ''')
            metadata_rows = cursor.fetchall()

            metadata_exact = {}
            metadata_norm = {}
            metadata_by_id = {}

            for row in metadata_rows:
                metadata_id = row['id']
                project_name = (row['project_name'] or '').strip()
                website_url = (row['website_url'] or '').strip().lower()

                metadata_by_id[metadata_id] = {
                    'project_name': project_name,
                    'website_url': website_url,
                    'project_token': row['project_token']
                }

                if project_name:
                    metadata_exact[project_name.lower()] = metadata_id
                    metadata_norm[normalize(project_name)] = metadata_id

            linked = 0
            skipped = 0
            errors = []

            for project in projects_list:
                token = project.get('token')
                title = (project.get('title')
                         or project.get('name') or '').strip()

                if not token or not title:
                    skipped += 1
                    continue

                cursor.execute(
                    'SELECT id FROM projects WHERE token = %s', (token,))
                project_row = cursor.fetchone()
                if not project_row:
                    skipped += 1
                    continue

                project_id = project_row['id']
                matched_metadata_id = None

                title_lower = title.lower()
                title_norm = normalize(title)

                if title_lower in metadata_exact:
                    matched_metadata_id = metadata_exact[title_lower]
                elif title_norm in metadata_norm:
                    matched_metadata_id = metadata_norm[title_norm]
                else:
                    website = self.extract_website_from_title(title).lower()
                    if website and website != 'unknown':
                        for metadata_id, item in metadata_by_id.items():
                            website_url = item.get('website_url', '')
                            if website_url and website in website_url:
                                matched_metadata_id = metadata_id
                                break

                if not matched_metadata_id:
                    skipped += 1
                    continue

                try:
                    cursor.execute('''
                        UPDATE metadata
                        SET project_id = %s,
                            project_token = %s,
                            updated_date = %s
                        WHERE id = %s
                    ''', (project_id, token, datetime.now().isoformat(), matched_metadata_id))

                    cursor.execute('''
                        INSERT OR IGNORE INTO project_metadata (project_id, metadata_id)
                        VALUES (%s, %s)
                    ''', (project_id, matched_metadata_id))

                    linked += 1
                except Exception as update_error:
                    errors.append(f"{token}: {str(update_error)}")

            conn.commit()
            self.disconnect()

            return {
                'success': True,
                'linked': linked,
                'skipped': skipped,
                'errors': errors
            }

        except Exception as e:
            print(f"Error syncing metadata with projects: {e}")
            self.disconnect()
            return {'success': False, 'error': str(e), 'linked': 0, 'skipped': 0, 'errors': []}

    def _link_projects_to_metadata(self, cursor):
        """Auto-link projects to metadata by matching project names"""
        try:
            # Get unlinked metadata records
            cursor.execute('''
                SELECT id, project_name FROM metadata 
                WHERE project_name IS NOT NULL AND project_name != ''
                AND id NOT IN (SELECT metadata_id FROM project_metadata)
            ''')

            unlinked_metadata = cursor.fetchall()

            for metadata in unlinked_metadata:
                metadata_id = metadata[0]
                project_name = metadata[1].strip().lower()

                # Search for matching project by name
                cursor.execute('''
                    SELECT id FROM projects 
                    WHERE LOWER(title) LIKE %s
                    LIMIT 1
                ''', (f'%{project_name}%',))

                project = cursor.fetchone()

                if project:
                    project_id = project[0]
                    try:
                        cursor.execute('''
                            INSERT OR IGNORE INTO project_metadata (project_id, metadata_id)
                            VALUES (%s, %s)
                        ''', (project_id, metadata_id))
                    except:
                        pass  # Link already exists

        except Exception as e:
            print(f"Error linking projects to metadata: {e}")

    def get_projects_with_metadata(self, limit: int = 100, offset: int = 0,
                                   region: str = None, country: str = None,
                                   brand: str = None) -> dict:
        """Get projects joined with metadata, with optional filtering"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            # Build base query
            base_query = '''
                SELECT DISTINCT p.id
                FROM projects p
                LEFT JOIN project_metadata pm ON p.id = pm.project_id
                LEFT JOIN metadata m ON pm.metadata_id = m.id
                WHERE 1=1
            '''

            params = []

            if region:
                base_query += ' AND m.region = %s'
                params.append(region)

            if country:
                base_query += ' AND m.country = %s'
                params.append(country)

            if brand:
                base_query += ' AND m.brand = %s'
                params.append(brand)

            # Count total with filters
            count_query = f"SELECT COUNT(*) AS c FROM ({base_query}) as count_subquery"
            cursor.execute(count_query, params)
            r = cursor.fetchone()
            total = r['c'] if isinstance(r, dict) else r[0]

            # Get full results with pagination
            full_query = '''
                SELECT
                    p.id, p.token, p.title, p.owner_email, p.main_site,
                    p.created_at, p.updated_at,
                    m.id as metadata_id, m.region, m.country, m.brand,
                    m.project_name, m.website_url, m.status
                FROM projects p
                LEFT JOIN project_metadata pm ON p.id = pm.project_id
                LEFT JOIN metadata m ON pm.metadata_id = m.id
                WHERE 1=1
            '''

            filter_params = []

            if region:
                full_query += ' AND m.region = %s'
                filter_params.append(region)

            if country:
                full_query += ' AND m.country = %s'
                filter_params.append(country)

            if brand:
                full_query += ' AND m.brand = %s'
                filter_params.append(brand)

            # Add ordering and pagination
            full_query += ' ORDER BY p.updated_at DESC LIMIT %s OFFSET %s'
            filter_params.extend([limit, offset])

            cursor.execute(full_query, filter_params)
            rows = cursor.fetchall()

            # Group results: one project with all its metadata (row is dict with RealDictCursor / or Row)
            projects_dict = {}
            for row in rows:
                r = row if isinstance(row, dict) else row
                project_id = r.get('id') if isinstance(r, dict) else row[0]

                if project_id not in projects_dict:
                    projects_dict[project_id] = {
                        'id': r.get('id') if isinstance(r, dict) else row[0],
                        'token': r.get('token') if isinstance(r, dict) else row[1],
                        'title': r.get('title') if isinstance(r, dict) else row[2],
                        'owner_email': r.get('owner_email') if isinstance(r, dict) else row[3],
                        'main_site': r.get('main_site') if isinstance(r, dict) else row[4],
                        'created_at': r.get('created_at') if isinstance(r, dict) else row[5],
                        'updated_at': r.get('updated_at') if isinstance(r, dict) else row[6],
                        'metadata': []
                    }

                # Add metadata if present
                mid = r.get('metadata_id') if isinstance(r, dict) else row[7]
                if mid:
                    projects_dict[project_id]['metadata'].append({
                        'id': mid,
                        'region': r.get('region') if isinstance(r, dict) else row[8],
                        'country': r.get('country') if isinstance(r, dict) else row[9],
                        'brand': r.get('brand') if isinstance(r, dict) else row[10],
                        'project_name': r.get('project_name') if isinstance(r, dict) else row[11],
                        'website_url': r.get('website_url') if isinstance(r, dict) else row[12],
                        'status': r.get('status') if isinstance(r, dict) else row[13]
                    })

            self.disconnect()

            return {
                'success': True,
                'projects': list(projects_dict.values()),
                'total': total,
                'limit': limit,
                'offset': offset
            }

        except Exception as e:
            print(f"Error getting projects with metadata: {e}")
            self.disconnect()
            return {'success': False, 'error': str(e), 'projects': []}

    def get_projects_count(self) -> int:
        """Get total count of synced projects"""
        try:
            conn = self.connect()
            cursor = self.cursor()

            cursor.execute('SELECT COUNT(*) AS c FROM projects')
            r = cursor.fetchone()
            count = r['c'] if isinstance(r, dict) else r[0]

            self.disconnect()
            return count
        except Exception as e:
            print(f"Error getting projects count: {e}")
            self.disconnect()
            return 0

    def extract_website_from_title(self, title: str) -> str:
        """
        Extract website domain from project title
        Pattern: "(Brand Name) ... website_domain_productname"
        Examples:
        "(MSA Pricing) Filter-technik.de_Kraftstoffvorfilter" -> "filter-technik.de"
        "(Brand) example.com_product" -> "example.com"
        "(Brand) aisbelgium.be_something" -> "aisbelgium.be"
        """
        import re
        if not title:
            return "Unknown"

        # Match pattern: ) followed by domain (with dots/hyphens), followed by _
        match = re.search(r'\)\s*([^_\s]+(?:\.[^_\s]+)*?)_', title)
        if match and match[1]:
            return match[1].lower()  # Normalize to lowercase

        # Alternative: look for domain pattern anywhere
        match = re.search(
            r'([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,})', title)
        if match:
            return match.group(1).lower()  # Normalize to lowercase

        # Fallback
        # Normalize to lowercase
        return (title.split('_')[0] if '_' in title else title[:30]).lower()

    def get_distinct_metadata_values(self, field: str) -> list:
        """Get distinct values from metadata table"""
        try:
            if field not in ['region', 'country', 'brand']:
                return []

            # Create a fresh connection for this operation (thread-safe)
            conn = self._get_connection()
            cursor = self.cursor()

            cursor.execute(
                f"SELECT DISTINCT {field} FROM metadata WHERE {field} IS NOT NULL AND {field} != '' ORDER BY {field}")
            values = [row[0] for row in cursor.fetchall()]

            conn.close()
            return values

        except Exception as e:
            print(f"Error getting distinct metadata values for {field}: {e}")
            return []

    def get_distinct_project_websites(self) -> list:
        """Get all distinct website domains from project titles"""
        try:
            # Create a fresh connection for this operation (thread-safe)
            conn = self._get_connection()
            cursor = self.cursor()

            cursor.execute(
                'SELECT DISTINCT title FROM projects ORDER BY title')
            rows = cursor.fetchall()

            websites = set()
            for row in rows:
                title = row[0] if row[0] else "Unknown"
                website = self.extract_website_from_title(title)
                if website:
                    websites.add(website)

            conn.close()
            return sorted(list(websites))

        except Exception as e:
            print(f"Error getting project websites: {e}")
            return []

    def get_projects_with_website_grouping(self, region: str = None, country: str = None,
                                           brand: str = None, website: str = None,
                                           limit: int = 100, offset: int = 0) -> dict:
        """
        Get projects with website grouping and metadata filtering
        Returns projects grouped by website with metadata mapping
        """
        try:
            # Create a fresh connection for this operation (thread-safe)
            conn = self._get_connection()
            cursor = self.cursor()

            # Build query with metadata joins and filtering
            base_query = '''
                SELECT DISTINCT p.id, p.token, p.title, p.owner_email, p.main_site,
                       p.created_at, p.updated_at,
                       m.id as metadata_id, m.region, m.country, m.brand,
                       m.project_name, m.website_url, m.status
                FROM projects p
                LEFT JOIN project_metadata pm ON p.id = pm.project_id
                LEFT JOIN metadata m ON pm.metadata_id = m.id
                WHERE 1=1
            '''

            params = []

            # Apply metadata filters
            if region:
                base_query += ' AND m.region = %s'
                params.append(region)

            if country:
                base_query += ' AND m.country = %s'
                params.append(country)

            if brand:
                base_query += ' AND m.brand = %s'
                params.append(brand)

            # For website filter, we need to match against extracted website from title
            # We'll do this in Python after fetching, not in SQL

            # For count: execute a simpler query
            count_query = 'SELECT COUNT(DISTINCT p.id) FROM projects p LEFT JOIN project_metadata pm ON p.id = pm.project_id LEFT JOIN metadata m ON pm.metadata_id = m.id WHERE 1=1'
            if region:
                count_query += ' AND m.region = %s'
            if country:
                count_query += ' AND m.country = %s'
            if brand:
                count_query += ' AND m.brand = %s'

            try:
                cursor.execute(count_query, params)
                count_result = cursor.fetchone()
                total = count_result[0] if count_result else 0
            except Exception as count_err:
                # If count fails, just set total to unknown
                total = -1

            # Add pagination
            base_query += ' ORDER BY p.updated_at DESC'

            cursor.execute(base_query, params)
            rows = cursor.fetchall()

            # Filter by website in Python after fetching (extract from title)
            if website:
                website_lower = website.strip().lower()
                filtered_rows = []
                for row in rows:
                    title = row[2]
                    extracted_website = self.extract_website_from_title(title)
                    if extracted_website and website_lower in extracted_website.lower():
                        filtered_rows.append(row)
                rows = filtered_rows
                total = len(filtered_rows)  # Update total after website filter

            # Apply pagination after website filtering
            start_idx = offset
            end_idx = offset + limit
            paginated_rows = rows[start_idx:end_idx]

            # Group by website and project
            websites_dict = {}
            projects_dict = {}

            for row in paginated_rows:
                project_id = row[0]
                title = row[2]
                website_extracted = self.extract_website_from_title(title)

                # Initialize website group
                if website_extracted not in websites_dict:
                    websites_dict[website_extracted] = {
                        'website': website_extracted,
                        'projects': [],
                        'project_count': 0,
                        'metadata_count': 0
                    }

                # Initialize project
                if project_id not in projects_dict:
                    project_data = {
                        'id': row[0],
                        'token': row[1],
                        'title': row[2],
                        'owner_email': row[3],
                        'main_site': row[4],
                        'created_at': row[5],
                        'updated_at': row[6],
                        'website': website_extracted,
                        'metadata': []
                    }
                    projects_dict[project_id] = project_data
                    websites_dict[website_extracted]['projects'].append(
                        project_data)
                    websites_dict[website_extracted]['project_count'] += 1

                # Add metadata if present
                if row[7]:  # metadata_id
                    metadata_item = {
                        'id': row[7],
                        'region': row[8],
                        'country': row[9],
                        'brand': row[10],
                        'project_name': row[11],
                        'website_url': row[12],
                        'status': row[13]
                    }
                    projects_dict[project_id]['metadata'].append(metadata_item)
                    websites_dict[website_extracted]['metadata_count'] += 1

            conn.close()

            return {
                'success': True,
                'by_website': list(websites_dict.values()),
                'by_project': list(projects_dict.values()),
                'total': total,
                'limit': limit,
                'offset': offset
            }

        except Exception as e:
            print(f"Error getting projects with website grouping: {e}")
            return {'success': False, 'error': str(e), 'by_website': [], 'by_project': []}

    def get_project_by_token(self, token: str) -> dict:
        """
        Get a specific project by token
        Returns project data with last run info from database
        """
        try:
            conn = self._get_connection()
            cursor = self.cursor()

            query = '''
                SELECT id, token, title, owner_email, main_site, created_at, updated_at
                FROM projects
                WHERE token = %s
            '''

            cursor.execute(query, (token,))
            row = cursor.fetchone()

            if not row:
                conn.close()
                return None

            project_id = row[0]
            project = {
                'id': row[0],
                'token': row[1],
                'title': row[2],
                'owner_email': row[3],
                'main_site': row[4],
                'created_at': row[5],
                'updated_at': row[6],
                'last_run': None
            }

            # Get the most recent run for this project
            run_query = '''
                SELECT run_token, status, pages_scraped, start_time, end_time, 
                       duration_seconds, created_at, updated_at
                FROM runs
                WHERE project_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            '''

            cursor.execute(run_query, (project_id,))
            run_row = cursor.fetchone()

            if run_row:
                project['last_run'] = {
                    'run_token': run_row[0],
                    'status': run_row[1],
                    'pages_scraped': run_row[2] or 0,
                    'pages': run_row[2] or 0,
                    'start_time': run_row[3],
                    'end_time': run_row[4],
                    'duration_seconds': run_row[5],
                    'created_at': run_row[6],
                    'updated_at': run_row[7]
                }

            conn.close()
            return project
        except Exception as e:
            print(f"Error getting project by token {token}: {e}")
            return None

    def get_project_by_id(self, project_id: int) -> dict:
        """
        Get a specific project by numeric ID.
        Returns project data with last run info from database, or None if not found.
        """
        try:
            conn = self._get_connection()
            cursor = self.cursor()

            query = '''
                SELECT id, token, title, owner_email, main_site, created_at, updated_at
                FROM projects
                WHERE id = %s
            '''
            cursor.execute(query, (project_id,))
            row = cursor.fetchone()

            if not row:
                conn.close()
                return None

            project = {
                'id': row[0],
                'token': row[1],
                'title': row[2],
                'owner_email': row[3],
                'main_site': row[4],
                'created_at': row[5],
                'updated_at': row[6],
                'last_run': None
            }

            run_query = '''
                SELECT run_token, status, pages_scraped, start_time, end_time,
                       duration_seconds, created_at, updated_at
                FROM runs
                WHERE project_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            '''
            cursor.execute(run_query, (project_id,))
            run_row = cursor.fetchone()

            if run_row:
                project['last_run'] = {
                    'run_token': run_row[0],
                    'status': run_row[1],
                    'pages_scraped': run_row[2] or 0,
                    'pages': run_row[2] or 0,
                    'start_time': run_row[3],
                    'end_time': run_row[4],
                    'duration_seconds': run_row[5],
                    'created_at': run_row[6],
                    'updated_at': run_row[7]
                }

            conn.close()
            return project
        except Exception as e:
            print(f"Error getting project by id {project_id}: {e}")
            return None

    def get_project_id_by_token(self, token: str) -> int:
        """
        Get project ID by project token
        Returns project ID or None if not found
        """
        try:
            conn = self._get_connection()
            cursor = self.cursor()

            cursor.execute('SELECT id FROM projects WHERE token = %s', (token,))
            row = cursor.fetchone()
            conn.close()

            if row:
                return row[0]
            return None
        except Exception as e:
            print(f"Error getting project ID by token {token}: {e}")
            return None

    def get_metadata_by_project_token(self, token: str) -> list:
        """
        Get all metadata records associated with a project token
        Returns list of metadata records
        """
        try:
            conn = self._get_connection()
            cursor = self.cursor()

            query = '''
                SELECT m.id, m.region, m.country, m.brand, m.project_name, 
                       m.website_url, m.total_pages, m.total_products, m.status
                FROM metadata m
                WHERE m.project_token = %s OR m.id IN (
                    SELECT metadata_id FROM project_metadata 
                    WHERE project_id = (SELECT id FROM projects WHERE token = %s)
                )
            '''

            cursor.execute(query, (token, token))
            rows = cursor.fetchall()
            conn.close()

            metadata_list = []
            for row in rows:
                metadata_list.append({
                    'id': row[0],
                    'region': row[1],
                    'country': row[2],
                    'brand': row[3],
                    'project_name': row[4],
                    'website_url': row[5],
                    'total_pages': row[6],
                    'total_products': row[7],
                    'status': row[8]
                })

            return metadata_list
        except Exception as e:
            print(f"Error getting metadata by project token {token}: {e}")
            return []

    def get_project_run_stats(self, project_id: int) -> dict:
        """
        Get run statistics for a project
        Returns stats like total runs, completed runs, pages scraped, success rate
        """
        try:
            conn = self._get_connection()
            cursor = self.cursor()

            # Get total runs and completed runs
            stats_query = '''
                SELECT 
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN status IN ('completed', 'success') THEN 1 ELSE 0 END) as completed_runs,
                    SUM(CASE WHEN status IN ('running', 'initializing') THEN 1 ELSE 0 END) as active_runs,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled_runs,
                    SUM(pages_scraped) as total_pages_scraped,
                    MAX(start_time) as last_run_date,
                    AVG(pages_scraped) as avg_pages_per_run
                FROM runs
                WHERE project_id = %s
            '''

            cursor.execute(stats_query, (project_id,))
            row = cursor.fetchone()

            total_runs = row[0] or 0
            completed_runs = row[1] or 0
            active_runs = row[2] or 0
            cancelled_runs = row[3] or 0
            total_pages_scraped = row[4] or 0
            last_run_date = row[5]
            avg_pages_per_run = row[6] or 0

            # Calculate success rate based on pages scraped vs total pages in metadata
            success_rate = 0

            # Get total_pages from project's metadata
            metadata_query = '''
                SELECT total_pages FROM metadata WHERE project_id = %s LIMIT 1
            '''
            cursor.execute(metadata_query, (project_id,))
            metadata_row = cursor.fetchone()

            if metadata_row and metadata_row[0]:
                total_pages = metadata_row[0]
                success_rate = (total_pages_scraped / total_pages) * 100
            elif total_runs > 0:
                # Fallback: if no metadata, calculate as completion rate of runs
                success_rate = (completed_runs / total_runs) * 100

            stats = {
                'total_runs': total_runs,
                'completed_runs': completed_runs,
                'active_runs': active_runs,
                'cancelled_runs': cancelled_runs,
                'pages_scraped': total_pages_scraped,
                'last_run_date': last_run_date,
                'average_pages_per_run': round(avg_pages_per_run, 2),
                'success_rate': round(min(success_rate, 100), 1)
            }

            conn.close()
            return stats
        except Exception as e:
            print(f"Error getting run stats for project {project_id}: {e}")
            return {
                'total_runs': 0,
                'completed_runs': 0,
                'active_runs': 0,
                'cancelled_runs': 0,
                'pages_scraped': 0,
                'last_run_date': None,
                'average_pages_per_run': 0,
                'success_rate': 0
            }

    def get_all_metadata_by_website(self) -> dict:
        """
        Get all metadata records indexed by website for fast lookups
        Returns dict: {website: metadata_dict}
        """
        try:
            conn = self._get_connection()
            cursor = self.cursor()

            cursor.execute('SELECT * FROM metadata')
            rows = cursor.fetchall()

            metadata_by_website = {}

            for row in rows:
                website = row[11]  # website_url column (0-indexed)
                if website:
                    metadata_by_website[website.lower()] = {
                        'id': row[0],
                        'personal_project_id': row[1],
                        'project_name': row[4],
                        'region': row[8],
                        'country': row[9],
                        'brand': row[10],
                        'website_url': row[11],
                        'status': row[18]
                    }

                # Also index by project_name for fallback
                project_name = row[4]
                if project_name and project_name.lower() not in metadata_by_website:
                    metadata_by_website[project_name.lower()] = {
                        'id': row[0],
                        'personal_project_id': row[1],
                        'project_name': project_name,
                        'region': row[8],
                        'country': row[9],
                        'brand': row[10],
                        'website_url': row[11],
                        'status': row[18]
                    }

            conn.close()
            return metadata_by_website

        except Exception as e:
            print(f"Error getting metadata: {e}")
            return {}

    def match_projects_to_metadata_batch(self, projects: list) -> list:
        """
        Match a batch of projects to metadata efficiently

        Args:
            projects: List of project dicts with 'title' field

        Returns:
            Same projects list with 'metadata' field added where matching
        """
        try:
            # Pre-load all metadata indexed by website
            metadata_by_website = self.get_all_metadata_by_website()

            # Quick match using pre-loaded metadata
            for proj in projects:
                title = proj.get('title', '')
                website = self.extract_website_from_title(title)

                if website and website != 'Unknown':
                    # Try exact match
                    if website.lower() in metadata_by_website:
                        proj['metadata'] = metadata_by_website[website.lower()]
                        continue

                    # Try partial match (contains)
                    for key, metadata in metadata_by_website.items():
                        if website.lower() in key or key in website.lower():
                            proj['metadata'] = metadata
                            break

            return projects

        except Exception as e:
            print(f"Error in batch metadata matching: {e}")
            return projects

    def match_project_to_metadata(self, project_title: str) -> dict:
        """
        Match a project title with metadata by multiple strategies:
        1. Extract website domain from title and match against metadata.project_name or website_url
        2. Fallback to domain-based matching if project_name matching fails

        Pattern: "(Brand) domain_product info"
        Extract: "domain" (the part before underscore)

        Args:
            project_title: Full project title from ParseHub API

        Returns:
            Metadata dict if matched, empty dict if not found
        """
        try:
            if not project_title:
                return {}

            import re

            # Extract website domain from title
            # Pattern: ") domain_something" -> extract "domain"
            website = self.extract_website_from_title(project_title)

            if not website or website == 'Unknown':
                return {}

            conn = self._get_connection()
            cursor = self.cursor()

            # Strategy 1: Match by website domain (case-insensitive)
            cursor.execute(
                'SELECT * FROM metadata WHERE LOWER(project_name) = %s LIMIT 1',
                (website.lower(),)
            )
            row = cursor.fetchone()

            if row:
                conn.close()
                return {
                    'id': row[0],
                    'personal_project_id': row[1],
                    'project_name': row[4],
                    'region': row[8],
                    'country': row[9],
                    'brand': row[10],
                    'website_url': row[11],
                    'status': row[18]
                }

            # Strategy 2: Match against website_url
            cursor.execute(
                'SELECT * FROM metadata WHERE website_url = %s LIMIT 1',
                (website,)
            )
            row = cursor.fetchone()

            if row:
                conn.close()
                return {
                    'id': row[0],
                    'personal_project_id': row[1],
                    'project_name': row[4],
                    'region': row[8],
                    'country': row[9],
                    'brand': row[10],
                    'website_url': row[11],
                    'status': row[18]
                }

            # Strategy 3: Partial match - search for website in project_name
            cursor.execute(
                'SELECT * FROM metadata WHERE LOWER(project_name) LIKE %s LIMIT 1',
                (f'%{website.lower()}%',)
            )
            row = cursor.fetchone()

            if row:
                conn.close()
                return {
                    'id': row[0],
                    'personal_project_id': row[1],
                    'project_name': row[4],
                    'region': row[8],
                    'country': row[9],
                    'brand': row[10],
                    'website_url': row[11],
                    'status': row[18]
                }

            conn.close()
            return {}

        except Exception as e:
            print(f"Error matching project to metadata: {e}")
            return {}

    def insert_product_data(self, project_id: int, run_id: int = None, run_token: str = None,
                            product_data_list: list = None, columns_map: dict = None) -> dict:
        """
        Insert product data from ParseHub runs into the database.
        Dynamically handles any column names found in the data.

        Args:
            project_id: ID of the project
            run_id: ID of the run
            run_token: Token of the run
            product_data_list: List of dictionaries with product data
            columns_map: Mapping of data keys to table columns (for normalization)

        Returns:
            Dictionary with success status and counts
        """
        if not product_data_list:
            return {'success': False, 'error': 'No product data provided', 'inserted': 0}

        conn = self.connect()
        cursor = self.cursor()

        try:
            # Standard column mapping with case-insensitive fallback
            default_columns = {
                'name': 'name',
                'product_name': 'name',
                'part_number': 'part_number',
                'partnumber': 'part_number',
                'sku': 'part_number',
                'brand': 'brand',
                'list_price': 'list_price',
                'sale_price': 'sale_price',
                'saleprice': 'sale_price',
                'case_unit_price': 'case_unit_price',
                'caseunitprice': 'case_unit_price',
                'country': 'country',
                'currency': 'currency',
                'product_url': 'product_url',
                'url': 'product_url',
                'page_number': 'page_number',
                'pagenumber': 'page_number',
                'page': 'page_number',
                'extraction_date': 'extraction_date',
                'date': 'extraction_date'
            }

            # Merge with provided columns_map
            final_map = {**default_columns, **(columns_map or {})}

            inserted_count = 0

            for product in product_data_list:
                try:
                    # Normalize product data using column mapping
                    normalized_data = {}

                    if isinstance(product, dict):
                        for key, value in product.items():
                            # Try to find matching column name (case-insensitive)
                            mapped_key = None
                            for pattern, column in final_map.items():
                                if key.lower() == pattern.lower():
                                    mapped_key = column
                                    break

                            if mapped_key:
                                normalized_data[mapped_key] = value
                            else:
                                # Keep original key if no mapping found
                                normalized_data[key] = value

                    # Prepare insert statement
                    columns = ['project_id']
                    values = [project_id]
                    placeholders = ['%s']

                    if run_id:
                        columns.append('run_id')
                        values.append(run_id)
                        placeholders.append('%s')

                    if run_token:
                        columns.append('run_token')
                        values.append(run_token)
                        placeholders.append('%s')

                    # Add product data columns
                    for key, value in normalized_data.items():
                        columns.append(key)
                        values.append(value)
                        placeholders.append('%s')

                    # Insert or update
                    insert_sql = f'''
                        INSERT OR REPLACE INTO product_data ({', '.join(columns)})
                        VALUES ({', '.join(placeholders)})
                    '''

                    cursor.execute(insert_sql, values)
                    inserted_count += 1

                except Exception as e:
                    print(f"Warning: Failed to insert product record: {e}")
                    continue

            conn.commit()
            conn.close()

            return {
                'success': True,
                'inserted': inserted_count,
                'total': len(product_data_list),
                'skipped': len(product_data_list) - inserted_count
            }

        except Exception as e:
            conn.close()
            print(f"Error inserting product data: {e}")
            return {'success': False, 'error': str(e), 'inserted': 0}

    def get_product_data_by_project(self, project_id: int, limit: int = 1000, offset: int = 0) -> list:
        """Get all product data for a specific project"""
        conn = self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT * FROM product_data
                WHERE project_id = %s
                ORDER BY extraction_date DESC, page_number ASC
                LIMIT %s OFFSET %s
            ''', (project_id, limit, offset))

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]
        except Exception as e:
            conn.close()
            print(f"Error fetching product data: {e}")
            return []

    def get_product_data_by_run(self, run_token: str, limit: int = 1000) -> list:
        """Get all product data for a specific run"""
        conn = self.connect()
        cursor = self.cursor()

        try:
            cursor.execute('''
                SELECT * FROM product_data
                WHERE run_token = %s
                ORDER BY page_number ASC
                LIMIT %s
            ''', (run_token, limit))

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]
        except Exception as e:
            conn.close()
            print(f"Error fetching product data by run: {e}")
            return []

    def get_product_data_stats(self, project_id: int) -> dict:
        """Get statistics about product data for a project"""
        conn = self.connect()
        cursor = self.cursor()

        try:
            # Total products
            cursor.execute(
                'SELECT COUNT(*) AS c FROM product_data WHERE project_id = %s', (project_id,))
            r = cursor.fetchone()
            total_count = r['c'] if isinstance(r, dict) else r[0]

            # Total runs with data
            cursor.execute('''
                SELECT COUNT(DISTINCT run_token) AS c FROM product_data 
                WHERE project_id = %s
            ''', (project_id,))
            r = cursor.fetchone()
            total_runs = r['c'] if isinstance(r, dict) else r[0]

            # Latest extraction date
            cursor.execute('''
                SELECT MAX(extraction_date) AS d FROM product_data 
                WHERE project_id = %s
            ''', (project_id,))
            r = cursor.fetchone()
            latest_date = r['d'] if isinstance(r, dict) else r[0]

            # Brand counts
            cursor.execute('''
                SELECT brand, COUNT(*) as count FROM product_data 
                WHERE project_id = %s AND brand IS NOT NULL
                GROUP BY brand
                ORDER BY count DESC
                LIMIT 10
            ''', (project_id,))
            brand_distribution = [{'brand': row[0], 'count': row[1]}
                                  for row in cursor.fetchall()]

            # Country counts
            cursor.execute('''
                SELECT country, COUNT(*) as count FROM product_data 
                WHERE project_id = %s AND country IS NOT NULL
                GROUP BY country
                ORDER BY count DESC
                LIMIT 10
            ''', (project_id,))
            country_distribution = [
                {'country': row[0], 'count': row[1]} for row in cursor.fetchall()]

            conn.close()

            return {
                'total_products': total_count,
                'total_runs_with_data': total_runs,
                'latest_extraction': latest_date,
                'top_brands': brand_distribution,
                'top_countries': country_distribution
            }
        except Exception as e:
            conn.close()
            print(f"Error getting product stats: {e}")
            return {}

    def export_product_data_csv(self, project_id: int, output_path: str = None) -> str:
        """Export product data to CSV file"""
        import csv
        from pathlib import Path

        if not output_path:
            output_path = f"product_export_project_{project_id}.csv"

        try:
            products = self.get_product_data_by_project(
                project_id, limit=999999)

            if not products:
                return None

            # Get all unique column names from products
            all_columns = set()
            for product in products:
                all_columns.update(product.keys())

            # Sort columns for consistent output
            columns = sorted(list(all_columns))

            # Write CSV
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(
                    f, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(products)

            print(f"Export successful: {output_path}")
            return output_path

        except Exception as e:
            print(f"Error exporting product data: {e}")
            return None


if __name__ == '__main__':
    db = ParseHubDatabase()

    # Test
    db.add_project('test_token', 'Test Project',
                   'test@example.com', 'https://example.com')
    print("[OK] Database initialized successfully!")
    print(f"Database file: {db.db_path}")
