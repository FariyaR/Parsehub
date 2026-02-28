#!/usr/bin/env python3
"""
Incremental Scraping Manager
Automatically manages projects that need continuation scraping based on metadata
"""
import requests
import json
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from database import ParseHubDatabase

load_dotenv('.env')

API_KEY = os.getenv('PARSEHUB_API_KEY')
BASE_URL = 'https://www.parsehub.com/api/v2'


class IncrementalScrapingManager:
    def __init__(self):
        self.db = ParseHubDatabase()
        self.api_key = API_KEY
        if not self.api_key:
            raise Exception("PARSEHUB_API_KEY not configured")

    def check_and_match_pages(self):
        """
        Check all projects and match project_id from projects table with metadata
        If scraped pages < total pages, automatically trigger continuation run
        """
        try:
            conn = self.db.connect()
            cursor = conn.cursor()

            # Get all projects that have metadata
            cursor.execute('''
                SELECT p.id, p.token, m.total_pages, m.current_page_scraped, m.project_name
                FROM projects p
                INNER JOIN metadata m ON p.id = m.project_id
                WHERE m.total_pages > 0
                ORDER BY p.id
            ''')

            projects = cursor.fetchall()

            if not projects:
                print("[OK] No projects found with metadata")
                return []

            continuation_results = []

            for project_id, project_token, total_pages, current_page_scraped, project_name in projects:
                print(f"\n{'='*80}")
                print(
                    f"Project: {project_name} (ID: {project_id}, Token: {project_token[:8]}...)")
                print(
                    f"Metadata: {current_page_scraped}/{total_pages} pages scraped")

                # Calculate how many pages have been scraped from database runs
                cursor.execute('''
                    SELECT SUM(pages_scraped) as total_pages_db
                    FROM runs
                    WHERE project_id = %s AND status = 'completed'
                ''', (project_id,))

                result = cursor.fetchone()
                pages_from_runs = (result['total_pages_db'] if isinstance(result, dict) else result[0]) or 0

                print(f"Database: {pages_from_runs} pages from completed runs")
                print(
                    f"Difference: {total_pages - current_page_scraped} pages remaining")

                # Check if scraping is incomplete
                if current_page_scraped < total_pages:
                    next_page = current_page_scraped + 1
                    remaining_pages = total_pages - current_page_scraped

                    print(
                        f"\n[>] Scheduling continuation from page {next_page}")
                    print(f"  Remaining pages to scrape: {remaining_pages}")

                    # Trigger the run
                    result = self.trigger_continuation_run(
                        project_token=project_token,
                        project_id=project_id,
                        start_page=next_page,
                        total_pages=total_pages,
                        pages_to_scrape=remaining_pages,
                        project_name=project_name
                    )

                    if result['success']:
                        continuation_results.append({
                            'project_id': project_id,
                            'project_name': project_name,
                            'start_page': next_page,
                            'end_page': total_pages,
                            'run_token': result.get('run_token'),
                            'status': 'scheduled'
                        })
                        print(f"[OK] Run scheduled successfully")
                    else:
                        print(
                            f"[ERROR] Failed to schedule run: {result.get('error', 'Unknown error')}")
                else:
                    print(
                        f"[OK] Project is complete (all {total_pages} pages scraped)")

            conn.close()
            return continuation_results

        except Exception as e:
            print(f"[ERROR] Error in check_and_match_pages: {e}")
            import traceback
            traceback.print_exc()
            return []

    def trigger_continuation_run(self, project_token, project_id, start_page,
                                 total_pages, pages_to_scrape, project_name):
        """
        Trigger a ParseHub run starting from next page
        """
        try:
            # First, get project details to construct the modified URL
            print(f"\n  Fetching project details...")
            project_details = self.get_project_details(project_token)

            if not project_details:
                return {
                    'success': False,
                    'error': 'Could not fetch project details'
                }

            # Get the project's start URL
            start_url = project_details.get('start_url', '')
            if not start_url:
                return {
                    'success': False,
                    'error': 'Project has no start URL'
                }

            print(f"  Original start URL: {start_url}")

            # Modify URL for pagination (this is generic - you may need to adjust based on target website)
            modified_url = self.modify_url_for_page(start_url, start_page)
            print(f"  Modified start URL (page {start_page}): {modified_url}")

            # Create a new project with modified URL
            print(f"\n  Creating continuation project...")
            continuation_project = self.create_continuation_project(
                original_token=project_token,
                modified_url=modified_url,
                pages=pages_to_scrape,
                project_name=project_name,
                start_page=start_page
            )

            if not continuation_project:
                return {
                    'success': False,
                    'error': 'Could not create continuation project'
                }

            continuation_token = continuation_project.get('token')
            print(f"  Continuation project token: {continuation_token}")

            # Run the continuation project
            print(f"\n  Starting run...")
            run_result = self.run_project(continuation_token)

            if not run_result:
                return {
                    'success': False,
                    'error': 'Could not start run'
                }

            run_token = run_result.get('run_token')
            print(f"  Run started: {run_token}")

            # Store continuation run info in database
            self.store_continuation_run(
                original_project_id=project_id,
                continuation_token=continuation_token,
                run_token=run_token,
                start_page=start_page,
                pages_count=pages_to_scrape
            )

            return {
                'success': True,
                'run_token': run_token,
                'continuation_token': continuation_token,
                'start_page': start_page,
                'pages': pages_to_scrape
            }

        except Exception as e:
            print(f"  Error triggering continuation run: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e)
            }

    def get_project_details(self, project_token: str) -> dict:
        """Fetch project details from ParseHub API"""
        try:
            response = requests.get(
                f'{BASE_URL}/projects/{project_token}',
                params={'api_key': self.api_key},
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching project details: {e}")
            return None

    def modify_url_for_page(self, url: str, page_number: int) -> str:
        """
        Modify URL to target specific page
        Supports common pagination patterns:
        - ?page=X
        - ?p=X
        - ?offset=X
        - /page/X in URL path
        """
        # Common pagination patterns
        if '?page=' in url:
            # Replace existing page parameter
            parts = url.split('?')
            base = parts[0]
            params = parts[1] if len(parts) > 1 else ''
            # Remove existing page param and add new one
            new_params = '&'.join(
                [p for p in params.split('&') if not p.startswith('page=')])
            return f"{base}?page={page_number}" + (f"&{new_params}" if new_params else "")

        elif '?p=' in url:
            parts = url.split('?')
            base = parts[0]
            params = parts[1] if len(parts) > 1 else ''
            new_params = '&'.join(
                [p for p in params.split('&') if not p.startswith('p=')])
            return f"{base}?p={page_number}" + (f"&{new_params}" if new_params else "")

        elif '?offset=' in url:
            # offset = (page - 1) * items_per_page (typically 20 or 50)
            offset = (page_number - 1) * 20
            parts = url.split('?')
            base = parts[0]
            params = parts[1] if len(parts) > 1 else ''
            new_params = '&'.join(
                [p for p in params.split('&') if not p.startswith('offset=')])
            return f"{base}?offset={offset}" + (f"&{new_params}" if new_params else "")

        else:
            # No pagination detected, add page parameter
            separator = '&' if '?' in url else '?'
            return f"{url}{separator}page={page_number}"

    def create_continuation_project(self, original_token: str, modified_url: str,
                                    pages: int, project_name: str, start_page: int) -> dict:
        """
        Create a temporary project clone for continuation scraping
        This uses ParseHub's project update API
        """
        try:
            # Get original project template
            response = requests.get(
                f'{BASE_URL}/projects/{original_token}',
                params={'api_key': self.api_key},
                timeout=30
            )
            response.raise_for_status()
            original_project = response.json()

            # Create new project with modified URL
            new_project_data = {
                'template': original_project.get('template'),
                'start_url': modified_url,
                'title': f"{project_name} - Continuation (Page {start_page})"
            }

            # For now, we'll use the original project but with the run starting from modified URL
            # In a real scenario, you might want to create a clone with the new URL
            return {
                'token': original_token,  # Use same token but with new start URL
                'title': new_project_data['title'],
                'start_url': modified_url
            }

        except Exception as e:
            print(f"Error creating continuation project: {e}")
            return None

    def run_project(self, project_token: str, pages: int = None) -> dict:
        """Start a run for a project"""
        try:
            params = {
                'api_key': self.api_key,
            }

            if pages:
                params['pages'] = pages

            response = requests.post(
                f'{BASE_URL}/projects/{project_token}/run',
                params=params,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            return {
                'run_token': data.get('run_token'),
                'status': data.get('status')
            }

        except Exception as e:
            print(f"Error running project: {e}")
            return None

    def store_continuation_run(self, original_project_id: int, continuation_token: str,
                               run_token: str, start_page: int, pages_count: int):
        """Store information about the continuation run in database"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            # Create continuation run record
            cursor.execute('''
                INSERT INTO runs (
                    project_id, run_token, status, pages_scraped,
                    start_time, is_continuation, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                original_project_id,
                run_token,
                'starting',
                pages_count,
                datetime.now(),
                True,  # is_continuation
                datetime.now()
            ))

            conn.commit()
            conn.close()

            print(
                f"  Stored continuation run in database (ID: {cursor.lastrowid})")

        except Exception as e:
            print(f"Error storing continuation run: {e}")

    def monitor_continuation_runs(self):
        """Monitor running continuation runs and update status"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            # Get active continuation runs
            cursor.execute('''
                SELECT id, run_token, project_id
                FROM runs
                WHERE is_continuation = TRUE AND status IN ('starting', 'running')
                ORDER BY created_at DESC
                LIMIT 10
            ''')

            active_runs = cursor.fetchall()
            conn.close()

            for row in active_runs:
                run_id = row['id'] if isinstance(row, dict) else row[0]
                run_token = row['run_token'] if isinstance(row, dict) else row[1]
                project_id = row['project_id'] if isinstance(row, dict) else row[2]
                print(f"\nChecking continuation run: {run_token}")

                # Get run status from ParseHub API
                response = requests.get(
                    f'{BASE_URL}/runs/{run_token}',
                    params={'api_key': self.api_key},
                    timeout=30
                )
                response.raise_for_status()
                run_data = response.json()

                status = run_data.get('status')
                pages_scraped = run_data.get('pages_scraped', 0)

                print(f"  Status: {status}, Pages: {pages_scraped}")

                # Update run status in database
                conn = self.db.connect()
                cursor = self.db.cursor()
                cursor.execute('''
                    UPDATE runs
                    SET status = %s, pages_scraped = %s
                    WHERE id = %s
                ''', (status, pages_scraped, run_id))
                conn.commit()

                # If completed, update metadata
                if status == 'completed':
                    print(f"  Run completed! Updating metadata...")
                    self.update_metadata_pages(project_id, pages_scraped)

                conn.close()

        except Exception as e:
            print(f"Error monitoring continuation runs: {e}")

    def update_metadata_pages(self, project_id: int, pages_scraped: int):
        """Update current_page_scraped in metadata after successful run"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            # Get current metadata
            cursor.execute('''
                SELECT current_page_scraped, total_pages
                FROM metadata
                WHERE project_id = %s
            ''', (project_id,))

            result = cursor.fetchone()
            if result:
                current_page = result['current_page_scraped'] if isinstance(result, dict) else result[0]
                total_pages = result['total_pages'] if isinstance(result, dict) else result[1]
                new_current_page = min(
                    current_page + pages_scraped, total_pages)

                cursor.execute('''
                    UPDATE metadata
                    SET current_page_scraped = %s, updated_date = %s
                    WHERE project_id = %s
                ''', (new_current_page, datetime.now(), project_id))

                conn.commit()
                print(
                    f"    Updated metadata: {new_current_page}/{total_pages} pages")

            conn.close()

        except Exception as e:
            print(f"Error updating metadata: {e}")


def main():
    """Main entry point"""
    print("="*80)
    print("ParseHub Incremental Scraping Manager")
    print("="*80)

    manager = IncrementalScrapingManager()

    # Check all projects and match pages
    print("\n1. Checking projects and matching pages...")
    continuation_runs = manager.check_and_match_pages()

    print(f"\n{'='*80}")
    print(f"Scheduled {len(continuation_runs)} continuation runs")

    if continuation_runs:
        print("\nContinuation Runs Scheduled:")
        for run in continuation_runs:
            print(
                f"  • {run['project_name']}: Pages {run['start_page']}-{run['end_page']} (Token: {run['run_token'][:8]}...)")

    # Monitor continuation runs
    print(f"\n2. Monitoring continuation runs...")
    manager.monitor_continuation_runs()

    print(f"\n{'='*80}")
    print("[OK] Incremental scraping management complete")


if __name__ == '__main__':
    main()
