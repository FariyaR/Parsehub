"""
Recovery Service - Handles auto-recovery of stopped projects
"""

import requests
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dotenv import load_dotenv
import os
import hashlib
from database import ParseHubDatabase

load_dotenv()

class RecoveryService:
    def __init__(self):
        self.api_key = os.getenv('PARSEHUB_API_KEY', '')
        self.base_url = os.getenv('PARSEHUB_BASE_URL', 'https://www.parsehub.com/api/v2')
        self.db = ParseHubDatabase()
        self.stop_detection_minutes = 5  # No data for 5 minutes = stopped

    def check_project_status(self, project_token: str) -> Dict:
        """Check if a project run has stopped"""
        try:
            # Get latest run from ParseHub API
            response = requests.get(
                f"{self.base_url}/projects/{project_token}",
                params={'api_key': self.api_key}
            )
            
            if response.status_code != 200:
                return {'status': 'error', 'message': 'Failed to fetch project'}

            project_data = response.json()
            latest_run = project_data.get('last_run', {})
            
            if not latest_run:
                return {'status': 'no_run', 'message': 'No runs found'}

            run_status = latest_run.get('status')
            last_update = latest_run.get('fetch_end') or latest_run.get('fetch_start')
            
            # Check if stopped
            if run_status == 'completed':
                return {'status': 'completed', 'run': latest_run}
            
            if run_status == 'cancelled':
                return {'status': 'cancelled', 'run': latest_run}

            # Check if stuck (no data update for X minutes)
            if last_update:
                try:
                    last_update_time = datetime.fromisoformat(last_update.replace('Z', '+00:00'))
                    time_since_update = datetime.now(last_update_time.tzinfo) - last_update_time
                    
                    if time_since_update > timedelta(minutes=self.stop_detection_minutes):
                        return {
                            'status': 'stuck',
                            'message': f'No data for {int(time_since_update.total_seconds() / 60)} minutes',
                            'run': latest_run
                        }
                except:
                    pass

            return {'status': 'running', 'run': latest_run}

        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def get_last_product_url(self, run_token: str) -> Optional[Dict]:
        """Fetch the last successful product URL from run data"""
        try:
            response = requests.get(
                f"{self.base_url}/runs/{run_token}/data",
                params={'api_key': self.api_key}
            )

            if response.status_code != 200:
                return None

            data = response.json()
            
            # Extract products list
            if isinstance(data, dict):
                # Look for common product array keys
                for key in ['products', 'items', 'results', 'data']:
                    if key in data and isinstance(data[key], list) and len(data[key]) > 0:
                        last_product = data[key][-1]  # Last item
                        return self._extract_product_info(last_product)

            elif isinstance(data, list) and len(data) > 0:
                last_product = data[-1]
                return self._extract_product_info(last_product)

            return None

        except Exception as e:
            print(f"Error fetching last product: {e}")
            return None

    def _extract_product_info(self, product: dict) -> Dict:
        """Extract URL and name from product object"""
        # Try common field names for URLs
        url_fields = ['url', 'product_url', 'link', 'href', 'page_url', 'product_link']
        name_fields = ['name', 'title', 'product_name', 'product_title']

        product_url = None
        product_name = None

        for field in url_fields:
            if field in product and isinstance(product[field], str) and product[field].startswith('http'):
                product_url = product[field]
                break

        for field in name_fields:
            if field in product:
                product_name = str(product[field])
                break

        if not product_name and product_url:
            product_name = product_url.split('/')[-1]

        return {
            'url': product_url,
            'name': product_name,
            'data': product,
            'timestamp': datetime.now().isoformat()
        }

    def detect_next_page_url(self, current_url: str, pagination_pattern: Optional[str] = None) -> Optional[str]:
        """
        Detect next page URL based on current URL pattern
        Handles: page numbers, offsets, cursors
        """
        if not current_url:
            return None

        import re

        # Pattern 1: ?page=X
        if '?page=' in current_url or '&page=' in current_url:
            match = re.search(r'([?&])page=(\d+)', current_url)
            if match:
                current_page = int(match.group(2))
                next_url = re.sub(r'([?&])page=\d+', rf'\1page={current_page + 1}', current_url)
                return next_url

        # Pattern 2: ?offset=X
        if '?offset=' in current_url or '&offset=' in current_url:
            match = re.search(r'([?&])offset=(\d+)', current_url)
            if match:
                current_offset = int(match.group(2))
                next_offset = current_offset + 20  # Assume 20 items per page
                next_url = re.sub(r'([?&])offset=\d+', rf'\1offset={next_offset}', current_url)
                return next_url

        # Pattern 3: /page/X/
        if '/page/' in current_url:
            match = re.search(r'/page/(\d+)/', current_url)
            if match:
                current_page = int(match.group(1))
                next_url = re.sub(r'/page/\d+/', f'/page/{current_page + 1}/', current_url)
                return next_url

        # Default: append ?page=2 or &page=2
        if '?' in current_url:
            return f"{current_url}&page=2"
        else:
            return f"{current_url}?page=2"

    def create_recovery_project(self, original_project_token: str, 
                               last_product_url: str, last_product_name: str) -> Optional[Dict]:
        """Create a new project for recovery from last product URL"""
        try:
            # Get original project details
            response = requests.get(
                f"{self.base_url}/projects/{original_project_token}",
                params={'api_key': self.api_key}
            )

            if response.status_code != 200:
                return None

            original_project = response.json()

            # Detect next page URL
            next_url = self.detect_next_page_url(last_product_url)
            if not next_url:
                next_url = last_product_url

            # Create new project with recovery name
            new_project_name = f"{original_project.get('title', 'Project')}-Recovery-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            create_response = requests.post(
                f"{self.base_url}/projects",
                params={'api_key': self.api_key},
                json={
                    'title': new_project_name,
                    'start_url': next_url,
                    'template': original_project.get('template', '')
                }
            )

            if create_response.status_code == 201:
                new_project = create_response.json()
                return {
                    'success': True,
                    'project_token': new_project.get('token'),
                    'title': new_project_name,
                    'start_url': next_url,
                    'original_token': original_project_token
                }

            return None

        except Exception as e:
            print(f"Error creating recovery project: {e}")
            return None

    def start_recovery_run(self, project_token: str) -> Optional[str]:
        """Start a recovery run for a project"""
        try:
            response = requests.post(
                f"{self.base_url}/projects/{project_token}/run",
                params={'api_key': self.api_key}
            )

            if response.status_code == 201:
                run_token = response.json().get('token')
                return run_token

            return None

        except Exception as e:
            print(f"Error starting recovery run: {e}")
            return None

    def deduplicate_data(self, original_run_id: int, recovery_run_id: int) -> Dict:
        """
        Merge data from original and recovery runs, removing duplicates
        Returns stats about the merge
        """
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            # Get URLs from original run
            cursor.execute('''
                SELECT DISTINCT data_value FROM scraped_data 
                WHERE run_id = %s AND data_key IN ('url', 'product_url')
            ''', (original_run_id,))
            
            original_urls = set(row['data_value'] for row in cursor.fetchall())

            # Get URLs from recovery run
            cursor.execute('''
                SELECT DISTINCT data_value FROM scraped_data 
                WHERE run_id = %s AND data_key IN ('url', 'product_url')
            ''', (recovery_run_id,))
            
            recovery_urls = set(row['data_value'] for row in cursor.fetchall())

            # Find overlapping URLs (duplicates)
            duplicates = original_urls & recovery_urls
            new_items = recovery_urls - original_urls

            # Count records
            cursor.execute('SELECT records_count FROM runs WHERE id = %s', (original_run_id,))
            r1 = cursor.fetchone()
            original_count = r1['records_count'] if isinstance(r1, dict) else r1[0]

            cursor.execute('SELECT records_count FROM runs WHERE id = %s', (recovery_run_id,))
            r2 = cursor.fetchone()
            recovery_count = r2['records_count'] if isinstance(r2, dict) else r2[0]

            total_unique = original_count + len(new_items)

            conn.commit()
            cursor.close()
            self.db.disconnect()

            return {
                'original_count': original_count,
                'recovery_count': recovery_count,
                'duplicates': len(duplicates),
                'new_items': len(new_items),
                'total_unique': total_unique,
                'duplicate_urls': list(duplicates)
            }

        except Exception as e:
            print(f"Error deduplicating data: {e}")
            return {'error': str(e)}

    def trigger_auto_recovery(self, project_token: str) -> Dict:
        """
        Main recovery flow:
        1. Get last product URL
        2. Create recovery project
        3. Start recovery run
        4. Track recovery operation
        """
        try:
            # Get project ID
            self.db.connect()
            cursor = self.db.cursor()
            cursor.execute('SELECT id FROM projects WHERE token = %s', (project_token,))
            project = cursor.fetchone()
            if not project:
                self.db.disconnect()
                return {'success': False, 'message': 'Project not found'}
            project_id = project['id'] if isinstance(project, dict) else project[0]
            self.db.disconnect()

            # Get latest run
            conn = self.db.connect()
            cursor = self.db.cursor()
            cursor.execute('''
                SELECT id, run_token FROM runs 
                WHERE project_id = %s 
                ORDER BY created_at DESC 
                LIMIT 1
            ''', (project_id,))
            
            latest_run = cursor.fetchone()
            self.db.disconnect()

            if not latest_run:
                return {'success': False, 'message': 'No previous runs found'}

            original_run_id = latest_run['id'] if isinstance(latest_run, dict) else latest_run[0]

            # Get last product
            last_product = self.get_last_product_url(latest_run['run_token'])
            if not last_product or not last_product.get('url'):
                return {'success': False, 'message': 'Could not extract last product URL'}

            # Create recovery operation record
            recovery_op_id = self.db.create_recovery_operation(
                original_run_id,
                project_id,
                last_product['url'],
                last_product.get('name')
            )

            # Create new recovery project
            new_project = self.create_recovery_project(
                project_token,
                last_product['url'],
                last_product.get('name')
            )

            if not new_project:
                return {'success': False, 'message': 'Failed to create recovery project'}

            # Start recovery run
            recovery_run_token = self.start_recovery_run(new_project['project_token'])
            if not recovery_run_token:
                return {'success': False, 'message': 'Failed to start recovery run'}

            # Link recovery run to operation
            self.db.link_recovery_run(recovery_op_id, recovery_run_token)

            return {
                'success': True,
                'recovery_operation_id': recovery_op_id,
                'new_project_token': new_project['project_token'],
                'recovery_run_token': recovery_run_token,
                'last_product': last_product,
                'message': f'Recovery started from: {last_product.get("name", "Unknown product")}'
            }

        except Exception as e:
            return {'success': False, 'message': f'Recovery failed: {str(e)}'}


if __name__ == '__main__':
    service = RecoveryService()
    print("[OK] Recovery Service initialized!")
