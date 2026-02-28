#!/usr/bin/env python3
"""
Auto Sync Service - Automatically syncs data from ParseHub API to database
Fetches latest project details, run statuses, and updates database records
"""
import requests
import json
import os
import time
import logging
from datetime import datetime, timedelta
from threading import Thread, Event
from typing import Dict, List, Optional
from dotenv import load_dotenv
from database import ParseHubDatabase

load_dotenv('.env')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = os.getenv('PARSEHUB_API_KEY')
BASE_URL = 'https://www.parsehub.com/api/v2'
SYNC_INTERVAL = int(os.getenv('AUTO_SYNC_INTERVAL', '5'))  # Default 5 minutes


class AutoSyncService:
    """
    Automatically syncs ParseHub data to database:
    - Project details (title, main_site, last_run info)
    - Run statuses (running/completed/cancelled)
    - Latest run details for each project
    """

    def __init__(self):
        self.db = ParseHubDatabase()
        self.api_key = API_KEY
        self.base_url = BASE_URL
        self.sync_interval = SYNC_INTERVAL
        self.running = False
        self.thread = None
        self.stop_event = Event()

        if not self.api_key:
            raise Exception("PARSEHUB_API_KEY not configured in .env")

    def start(self):
        """Start the auto-sync service in background thread"""
        if self.running:
            logger.warning("Auto-sync service already running")
            return

        self.running = True
        self.stop_event.clear()
        self.thread = Thread(target=self._sync_loop, daemon=True)
        self.thread.start()
        logger.info(
            f"[OK] Auto-Sync Service started (interval: {self.sync_interval} minutes)")

    def stop(self):
        """Stop the auto-sync service"""
        if not self.running:
            return

        self.running = False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("[OK] Auto-Sync Service stopped")

    def _sync_loop(self):
        """Background loop that runs sync periodically"""
        while self.running and not self.stop_event.is_set():
            try:
                logger.info("\n" + "="*80)
                logger.info(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running auto-sync...")
                logger.info("="*80)

                # Run sync
                results = self.sync_all()

                # Log summary
                logger.info(f"\n[OK] Sync completed:")
                logger.info(
                    f"  - Projects synced: {results.get('projects_synced', 0)}")
                logger.info(
                    f"  - Runs updated: {results.get('runs_updated', 0)}")
                logger.info(
                    f"  - Projects updated: {results.get('projects_updated', 0)}")

                next_sync = datetime.now() + timedelta(minutes=self.sync_interval)
                logger.info(
                    f"\nNext sync: {next_sync.strftime('%Y-%m-%d %H:%M:%S')}")

            except Exception as e:
                logger.error(f"Error in sync loop: {e}")
                import traceback
                traceback.print_exc()

            # Wait for next interval or stop event
            self.stop_event.wait(timeout=self.sync_interval * 60)

    def sync_all(self):
        """
        Sync all data from ParseHub API:
        1. Fetch all projects
        2. Update project details
        3. Update last_run info for each project
        4. Update run statuses for active runs
        """
        results = {
            'projects_synced': 0,
            'runs_updated': 0,
            'projects_updated': 0
        }

        try:
            # 1. Fetch all projects from ParseHub API
            logger.info("\n1. Fetching all projects from ParseHub API...")
            projects = self.fetch_all_projects()

            if not projects:
                logger.warning("No projects found in ParseHub account")
                return results

            logger.info(f"   Found {len(projects)} projects")
            results['projects_synced'] = len(projects)

            # 2. Sync each project
            for project in projects:
                self.sync_project(project, results)

            # 3. Update active runs
            logger.info("\n3. Updating active runs...")
            self.update_active_runs(results)

            return results

        except Exception as e:
            logger.error(f"Error in sync_all: {e}")
            import traceback
            traceback.print_exc()
            return results

    def fetch_all_projects(self) -> List[Dict]:
        """Fetch all projects from ParseHub API with pagination"""
        try:
            all_projects = []
            offset = 0
            limit = 20  # ParseHub returns 20 projects per page

            while True:
                response = requests.get(
                    f"{self.base_url}/projects",
                    params={'api_key': self.api_key, 'offset': offset},
                    timeout=10
                )

                if response.status_code != 200:
                    logger.error(f"API error: {response.status_code}")
                    break

                data = response.json()
                projects = data.get('projects', [])

                if not projects:
                    break

                all_projects.extend(projects)

                # Check if there are more projects
                total_projects = data.get('total_projects', 0)
                if len(all_projects) >= total_projects:
                    break

                offset += limit
                time.sleep(0.5)  # Rate limiting

            return all_projects

        except Exception as e:
            logger.error(f"Error fetching projects: {e}")
            return []

    def sync_project(self, project: Dict, results: Dict):
        """Sync a single project to database"""
        try:
            token = project.get('token')
            title = project.get('title', 'Unknown Project')

            logger.info(f"\n2. Syncing: {title} ({token[:8]}...)")

            # Update project in database
            conn = self.db.connect()
            cursor = self.db.cursor()

            # Check if project exists
            cursor.execute('SELECT id FROM projects WHERE token = %s', (token,))
            existing = cursor.fetchone()

            if existing:
                # Update existing project
                cursor.execute('''
                    UPDATE projects 
                    SET title = %s, 
                        owner_email = %s,
                        main_site = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE token = %s
                ''', (
                    title,
                    project.get('owner_email'),
                    project.get('main_site'),
                    token
                ))
                project_id = existing['id'] if isinstance(existing, dict) else existing[0]
                logger.info(f"   [OK] Updated project (ID: {project_id})")
            else:
                # Insert new project
                cursor.execute('''
                    INSERT INTO projects (token, title, owner_email, main_site)
                    VALUES (%s, %s, %s, %s)
                ''', (
                    token,
                    title,
                    project.get('owner_email'),
                    project.get('main_site')
                ))
                project_id = cursor.lastrowid
                logger.info(f"   [OK] Created new project (ID: {project_id})")

            conn.commit()
            results['projects_updated'] += 1

            # Sync last_run info if available
            last_run = project.get('last_run')
            if last_run:
                self.sync_run(project_id, last_run, results)

            conn.close()

        except Exception as e:
            logger.error(f"Error syncing project {project.get('token')}: {e}")

    def sync_run(self, project_id: int, run_data: Dict, results: Dict):
        """Sync run information to database"""
        try:
            run_token = run_data.get('run_token')
            if not run_token:
                return

            status = run_data.get('status', 'unknown')

            conn = self.db.connect()
            cursor = self.db.cursor()

            # Check if run exists
            cursor.execute(
                'SELECT id, status FROM runs WHERE run_token = %s', (run_token,))
            existing = cursor.fetchone()

            # Parse timestamps
            start_time = run_data.get('start_time')
            end_time = run_data.get('end_time')

            # Calculate duration
            duration = None
            if start_time and end_time:
                try:
                    start_dt = datetime.fromisoformat(
                        start_time.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(
                        end_time.replace('Z', '+00:00'))
                    duration = int((end_dt - start_dt).total_seconds())
                except:
                    pass

            # Get pages scraped
            pages_scraped = run_data.get('pages', 0)

            # Get data readiness
            data_ready = run_data.get('data_ready', 0)

            if existing:
                run_id = existing['id'] if isinstance(existing, dict) else existing[0]
                old_status = existing['status'] if isinstance(existing, dict) else existing[1]

                # Only update if status changed or run is active
                if old_status != status or status in ['running', 'initializing']:
                    cursor.execute('''
                        UPDATE runs
                        SET status = %s,
                            pages_scraped = %s,
                            start_time = %s,
                            end_time = %s,
                            duration_seconds = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE run_token = %s
                    ''', (
                        status,
                        pages_scraped,
                        start_time,
                        end_time,
                        duration,
                        run_token
                    ))

                    conn.commit()

                    if old_status != status:
                        logger.info(
                            f"   [OK] Updated run {run_token[:8]}... ({old_status} -> {status})")
                        results['runs_updated'] += 1
            else:
                # Insert new run
                cursor.execute('''
                    INSERT INTO runs (
                        project_id, run_token, status, pages_scraped,
                        start_time, end_time, duration_seconds
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (
                    project_id,
                    run_token,
                    status,
                    pages_scraped,
                    start_time,
                    end_time,
                    duration
                ))

                run_id = cursor.lastrowid
                conn.commit()
                logger.info(
                    f"   [OK] Created new run {run_token[:8]}... (status: {status})")
                results['runs_updated'] += 1

            conn.close()

        except Exception as e:
            logger.error(f"Error syncing run {run_data.get('run_token')}: {e}")

    def update_active_runs(self, results: Dict):
        """Update status of all active runs in database"""
        try:
            conn = self.db.connect()
            cursor = conn.cursor()

            # Get all runs that might still be active
            cursor.execute('''
                SELECT r.id, r.run_token, p.token as project_token
                FROM runs r
                JOIN projects p ON r.project_id = p.id
                WHERE r.status IN ('running', 'initializing')
                ORDER BY r.start_time DESC
                LIMIT 50
            ''')

            active_runs = cursor.fetchall()

            if not active_runs:
                logger.info("   No active runs to update")
                conn.close()
                return

            logger.info(f"   Found {len(active_runs)} potentially active runs")

            for run in active_runs:
                run_id = run[0]
                run_token = run[1]
                project_token = run[2]

                # Fetch run details from ParseHub API
                run_details = self.fetch_run_details(project_token, run_token)

                if run_details:
                    old_status = run_details.get('status')
                    new_status = run_details.get('status')

                    if new_status:
                        # Update run status
                        cursor.execute('''
                            UPDATE runs
                            SET status = %s,
                                pages_scraped = %s,
                                end_time = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        ''', (
                            new_status,
                            run_details.get('pages', 0),
                            run_details.get('end_time'),
                            run_id
                        ))

                        logger.info(
                            f"   [OK] Updated run {run_token[:8]}... (status: {new_status})")
                        results['runs_updated'] += 1

                time.sleep(0.3)  # Rate limiting

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"Error updating active runs: {e}")

    def fetch_run_details(self, project_token: str, run_token: str) -> Optional[Dict]:
        """Fetch run details from ParseHub API"""
        try:
            response = requests.get(
                f"{self.base_url}/projects/{project_token}/run/{run_token}",
                params={'api_key': self.api_key},
                timeout=10
            )

            if response.status_code == 200:
                return response.json()

            return None

        except Exception as e:
            logger.error(f"Error fetching run {run_token}: {e}")
            return None

    def manual_sync(self):
        """Manually trigger a sync (useful for API endpoints)"""
        logger.info("Manual sync triggered")
        return self.sync_all()


# Global instance
_auto_sync_service = None


def start_auto_sync_service(interval_minutes: int = None):
    """Start the auto-sync service globally"""
    global _auto_sync_service

    if _auto_sync_service is not None:
        logger.warning("Auto-sync service already started")
        return _auto_sync_service

    # Create service with custom interval if provided
    if interval_minutes:
        os.environ['AUTO_SYNC_INTERVAL'] = str(interval_minutes)

    _auto_sync_service = AutoSyncService()
    _auto_sync_service.start()

    return _auto_sync_service


def stop_auto_sync_service():
    """Stop the auto-sync service globally"""
    global _auto_sync_service

    if _auto_sync_service is None:
        logger.warning("Auto-sync service not running")
        return

    _auto_sync_service.stop()
    _auto_sync_service = None


def get_auto_sync_service():
    """Get the current auto-sync service instance"""
    return _auto_sync_service


# For standalone execution
if __name__ == '__main__':
    import sys

    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    print(f"Starting Auto-Sync Service (interval: {interval} minutes)")
    print("Press Ctrl+C to stop\n")

    service = start_auto_sync_service(interval)

    try:
        # Keep running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nStopping Auto-Sync Service...")
        stop_auto_sync_service()
        print("Stopped.")
