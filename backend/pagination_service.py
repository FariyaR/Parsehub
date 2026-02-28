"""
Pagination Service - Handles pagination detection, URL generation, and recovery
"""

import re
import sqlite3
import json
from datetime import datetime
from typing import Dict, Optional, Tuple


class PaginationService:
    """Service for managing pagination and automatic recovery"""
    
    def __init__(self, db_path: str = "parsehub.db"):
        self.db_path = db_path
    
    def extract_page_number(self, url: str) -> int:
        """
        Extract page number from URL
        Supports: ?page=N, ?p=N, /page/N, /page-N, ?offset=N
        """
        if not url:
            return 1
        
        patterns = [
            r'[?&]page[=](\d+)',
            r'[?&]p[=](\d+)',
            r'/page[/-](\d+)',
            r'[?&]offset[=](\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        return 1
    
    def generate_next_page_url(self, base_url: str, current_page: int) -> str:
        """
        Generate URL for next page based on detected pattern
        """
        next_page = current_page + 1
        
        patterns = [
            (r'([?&]page[=])\d+', rf'\g<1>{next_page}', 'query'),
            (r'([?&]p[=])\d+', rf'\g<1>{next_page}', 'query'),
            (r'(/page[/-])\d+', rf'\g<1>{next_page}', 'path'),
            (r'([?&]offset[=])\d+', rf'\g<1>{current_page * 20}', 'offset'),
        ]
        
        for pattern, replacement, style in patterns:
            if re.search(pattern, base_url):
                return re.sub(pattern, replacement, base_url)
        
        # Default: append page parameter
        separator = '&' if '?' in base_url else '?'
        return f"{base_url}{separator}page={next_page}"
    
    def detect_pagination_pattern(self, url: str) -> Dict:
        """Detect pagination pattern in URL"""
        patterns = {
            'query_page': r'[?&]page[=]\d+',
            'query_p': r'[?&]p[=]\d+',
            'path_style': r'/page[/-]\d+',
            'offset': r'[?&]offset[=]\d+'
        }
        
        detected = {}
        for pattern_name, pattern in patterns.items():
            if re.search(pattern, url):
                detected[pattern_name] = True
        
        return detected
    
    def check_pagination_needed(self, project_id: int, target_pages: int) -> Dict:
        """
        Check if pagination recovery is needed
        
        Returns:
            {
                'needs_recovery': bool,
                'last_page_scraped': int,
                'target_pages': int,
                'total_data_count': int,
                'pages_remaining': int
            }
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get last page number from data
        cursor.execute('''
            SELECT MAX(CAST(json_extract(data, '$.page_number') AS INTEGER)) as last_page
            FROM scraped_data 
            WHERE project_id = %s
        ''', (project_id,))
        
        result = cursor.fetchone()
        last_page = result['last_page'] or 1 if result else 1
        
        # Get total data count
        cursor.execute('''
            SELECT COUNT(*) as total FROM scraped_data 
            WHERE project_id = %s
        ''', (project_id,))
        
        total_count = cursor.fetchone()['total'] or 0
        
        conn.close()
        
        return {
            'needs_recovery': last_page < target_pages,
            'last_page_scraped': last_page,
            'target_pages': target_pages,
            'total_data_count': total_count,
            'pages_remaining': max(0, target_pages - last_page)
        }
    
    def create_recovery_project_info(self, original_url: str, current_page: int, 
                                     target_pages: int) -> Dict:
        """
        Create recovery project information
        Generates next page URL and metadata
        """
        next_url = self.generate_next_page_url(original_url, current_page)
        
        return {
            'original_url': original_url,
            'recovery_url': next_url,
            'start_page': current_page + 1,
            'target_pages': target_pages,
            'created_at': datetime.now().isoformat()
        }
    
    def record_scraping_progress(self, project_id: int, page_number: int, 
                                data_count: int, items_per_minute: float) -> None:
        """Record scraping progress checkpoint"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO run_checkpoints
            (run_id, snapshot_timestamp, item_count_at_time, items_per_minute)
            VALUES (
                (SELECT id FROM runs WHERE project_id = %s ORDER BY created_at DESC LIMIT 1),
                CURRENT_TIMESTAMP,
                %s,
                %s
            )
        ''', (project_id, data_count, items_per_minute))
        
        conn.commit()
        conn.close()


class PaginationDetector:
    """Advanced pagination detection using data analysis"""
    
    @staticmethod
    def estimate_total_pages(url_patterns: list) -> Optional[int]:
        """
        Estimate total pages from URL patterns
        """
        if not url_patterns:
            return None
        
        page_numbers = []
        detector = PaginationService()
        
        for url in url_patterns:
            page_num = detector.extract_page_number(url)
            if page_num:
                page_numbers.append(page_num)
        
        if page_numbers:
            return max(page_numbers)
        
        return None
    
    @staticmethod
    def detect_items_per_page(data_per_page: list) -> Tuple[int, float]:
        """
        Analyze data count per page to estimate items per page
        Returns: (average_items_per_page, consistency_score)
        """
        if not data_per_page:
            return 0, 0.0
        
        avg = sum(data_per_page) / len(data_per_page)
        
        # Calculate consistency (coefficient of variation)
        if avg > 0:
            variance = sum((x - avg) ** 2 for x in data_per_page) / len(data_per_page)
            std_dev = variance ** 0.5
            consistency = 1 - (std_dev / avg)  # 0-1 scale
        else:
            consistency = 0.0
        
        return int(avg), max(0, consistency)
