"""
Scraping Session Service
Manages incremental scraping sessions and iteration tracking
"""

import json
import sys
import sqlite3
from datetime import datetime
from database import ParseHubDatabase


class ScrapingSessionService:
    """Service for managing incremental scraping sessions"""

    def __init__(self):
        self.db = ParseHubDatabase()

    def create_session(self, project_token: str, project_name: str, total_pages_target: int):
        """Create a new scraping session"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                INSERT INTO scraping_sessions 
                (project_token, project_name, total_pages_target, status)
                VALUES (%s, %s, %s, 'running')
            ''', (project_token, project_name, total_pages_target))

            conn.commit()
            session_id = cursor.lastrowid

            print(f"[OK] Created scraping session {session_id} for {project_name} (target: {total_pages_target} pages)", file=sys.stderr)
            return {
                'success': True,
                'session_id': session_id,
                'project_token': project_token,
                'total_pages_target': total_pages_target
            }
        except sqlite3.IntegrityError:
            # Session already exists for this project_token and target
            cursor.execute('''
                SELECT id, status FROM scraping_sessions
                WHERE project_token = %s AND total_pages_target = %s
            ''', (project_token, total_pages_target))
            
            result = cursor.fetchone()
            if result:
                print(f"[WARNING] Session already exists for {project_token} with target {total_pages_target}", file=sys.stderr)
                return {
                    'success': True,
                    'session_id': result[0],
                    'status': result[1],
                    'existing': True
                }
        except Exception as e:
            print(f"[ERROR] Error creating session: {str(e)}", file=sys.stderr)
            return {'success': False, 'error': str(e)}

    def get_session(self, session_id: int):
        """Get session details"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                SELECT id, project_token, project_name, total_pages_target,
                       current_iteration, pages_completed, status, created_at, updated_at
                FROM scraping_sessions WHERE id = %s
            ''', (session_id,))

            result = cursor.fetchone()
            if result:
                return {
                    'success': True,
                    'session': {
                        'id': result[0],
                        'project_token': result[1],
                        'project_name': result[2],
                        'total_pages_target': result[3],
                        'current_iteration': result[4],
                        'pages_completed': result[5],
                        'status': result[6],
                        'created_at': result[7],
                        'updated_at': result[8]
                    }
                }
            return {'success': False, 'error': 'Session not found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def add_iteration_run(self, session_id: int, iteration_number: int,
                         parsehub_project_token: str, parsehub_project_name: str,
                         start_page: int, end_page: int, run_token: str):
        """Record a new iteration run"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            pages_in_run = end_page - start_page + 1

            cursor.execute('''
                INSERT INTO iteration_runs
                (session_id, iteration_number, parsehub_project_token, parsehub_project_name,
                 start_page_number, end_page_number, pages_in_this_run, run_token, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running')
            ''', (session_id, iteration_number, parsehub_project_token, parsehub_project_name,
                  start_page, end_page, pages_in_run, run_token))

            conn.commit()
            run_id = cursor.lastrowid

            print(f"[OK] Created iteration run {iteration_number} (pages {start_page}-{end_page})")
            return {
                'success': True,
                'run_id': run_id,
                'iteration_number': iteration_number
            }
        except Exception as e:
            print(f"[ERROR] Error adding iteration run: {str(e)}", file=sys.stderr)
            return {'success': False, 'error': str(e)}

    def update_iteration_run(self, run_id: int, csv_data: str, records_count: int, status: str):
        """Update iteration run with results"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                UPDATE iteration_runs
                SET csv_data = %s, records_count = %s, status = %s, completed_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (csv_data, records_count, status, run_id))

            conn.commit()

            print(f"[OK] Updated iteration run {run_id} with {records_count} records", file=sys.stderr)
            return {'success': True}
        except Exception as e:
            print(f"[ERROR] Error updating iteration run: {str(e)}", file=sys.stderr)
            return {'success': False, 'error': str(e)}

    def get_session_runs(self, session_id: int):
        """Get all iteration runs for a session"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                SELECT id, iteration_number, parsehub_project_name,
                       start_page_number, end_page_number, pages_in_this_run,
                       records_count, status, created_at
                FROM iteration_runs
                WHERE session_id = %s
                ORDER BY iteration_number
            ''', (session_id,))

            runs = cursor.fetchall()
            return {
                'success': True,
                'runs': [dict(zip([desc[0] for desc in cursor.description], row)) for row in runs]
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def update_session_progress(self, session_id: int, pages_completed: int, status: str):
        """Update session progress"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                UPDATE scraping_sessions
                SET pages_completed = %s, status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (pages_completed, status, session_id))

            conn.commit()
            print(f"[OK] Updated session {session_id}: {pages_completed} pages completed, status: {status}", file=sys.stderr)
            return {'success': True}
        except Exception as e:
            print(f"[ERROR] Error updating session progress: {str(e)}", file=sys.stderr)
            return {'success': False, 'error': str(e)}

    def mark_session_complete(self, session_id: int):
        """Mark session as complete"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                UPDATE scraping_sessions
                SET status = 'complete', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (session_id,))

            conn.commit()
            print(f"[OK] Marked session {session_id} as complete", file=sys.stderr)
            return {'success': True}
        except Exception as e:
            print(f"[ERROR] Error marking session complete: {str(e)}", file=sys.stderr)
            return {'success': False, 'error': str(e)}

    def save_combined_data(self, session_id: int, consolidated_csv: str, 
                          total_records: int, total_pages: int, deduplicated_count: int):
        """Save consolidated/combined data"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO combined_scraped_data
                (session_id, consolidated_csv, total_records, total_pages_scraped, deduplicated_record_count)
                VALUES (%s, %s, %s, %s, %s)
            ''', (session_id, consolidated_csv, total_records, total_pages, deduplicated_count))

            conn.commit()
            print(f"[OK] Saved combined data for session {session_id}", file=sys.stderr)
            return {'success': True}
        except Exception as e:
            print(f"[ERROR] Error saving combined data: {str(e)}", file=sys.stderr)
            return {'success': False, 'error': str(e)}

    def get_combined_data(self, session_id: int):
        """Get consolidated data for a session"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                SELECT consolidated_csv, total_records, total_pages_scraped, deduplicated_record_count
                FROM combined_scraped_data
                WHERE session_id = %s
            ''', (session_id,))

            result = cursor.fetchone()
            if result:
                return {
                    'success': True,
                    'consolidated_csv': result[0],
                    'total_records': result[1],
                    'total_pages': result[2],
                    'deduplicated_count': result[3]
                }
            return {'success': False, 'error': 'No combined data found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def save_url_pattern(self, project_token: str, original_url: str, pattern_type: str,
                        pattern_regex: str, placeholder: str):
        """Save detected URL pattern for a project"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO url_patterns
                (project_token, original_url, pattern_type, pattern_regex, last_page_placeholder)
                VALUES (%s, %s, %s, %s, %s)
            ''', (project_token, original_url, pattern_type, pattern_regex, placeholder))

            conn.commit()
            print(f"[OK] Saved URL pattern for {project_token}", file=sys.stderr)
            return {'success': True}
        except Exception as e:
            print(f"[ERROR] Error saving URL pattern: {str(e)}", file=sys.stderr)
            return {'success': False, 'error': str(e)}

    def get_url_pattern(self, project_token: str):
        """Get stored URL pattern for a project"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            cursor.execute('''
                SELECT original_url, pattern_type, pattern_regex, last_page_placeholder
                FROM url_patterns WHERE project_token = %s
            ''', (project_token,))

            result = cursor.fetchone()
            if result:
                return {
                    'success': True,
                    'original_url': result[0],
                    'pattern_type': result[1],
                    'pattern_regex': result[2],
                    'placeholder': result[3]
                }
            return {'success': False, 'error': 'No pattern found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
