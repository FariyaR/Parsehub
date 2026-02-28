#!/usr/bin/env python3
"""
Fetch and return session progress for incremental scraping.
Called by the frontend API to show real-time progress.
"""

import sys
import json
import sqlite3
import os
from datetime import datetime

# Add backend directory to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

from scraping_session_service import ScrapingSessionService

def calculate_estimated_time(completed_runs: int, total_runs: int) -> str:
    """Calculate estimated remaining time based on completed runs."""
    remaining_runs = total_runs - completed_runs
    minutes_per_run = 3  # Average ParseHub run takes ~3 minutes
    total_minutes = remaining_runs * minutes_per_run
    
    if total_minutes <= 0:
        return '< 1 minute'
    if total_minutes < 60:
        return f'{total_minutes} minutes'
    
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f'{hours}h {minutes}m'

def get_session_progress(session_id: int) -> dict:
    """Fetch and format session progress."""
    try:
        session_service = ScrapingSessionService()
        
        # Get session details
        conn = session_service.db.connect()
        cursor = conn.cursor()
        
        # Fetch session details
        cursor.execute('''
            SELECT id, project_token, project_name, total_pages_target, 
                   pages_completed, current_iteration, status, created_at
            FROM scraping_sessions WHERE id = %s
        ''', (session_id,))
        
        session = cursor.fetchone()
        if not session:
            return {'error': 'Session not found'}
        
        session_id, project_token, project_name, total_pages_target, pages_completed, current_iteration, status, created_at = session
        
        # Fetch all iteration runs
        cursor.execute('''
            SELECT id, iteration_number, parsehub_project_name, start_page_number,
                   end_page_number, records_count, status, completed_at
            FROM iteration_runs WHERE session_id = %s
            ORDER BY iteration_number
        ''', (session_id,))
        
        runs = cursor.fetchall()
        conn.close()
        
        # Calculate derived values
        total_iterations_needed = (total_pages_target + 9) // 10  # Ceiling division
        percentage = round((pages_completed / total_pages_target * 100)) if total_pages_target > 0 else 0
        iterations_completed = sum(1 for r in runs if r[6] == 'completed')
        
        # Format runs
        formatted_runs = []
        for run in runs:
            run_id, iteration_number, project_name_run, start_page, end_page, records_count, run_status, completed_at = run
            formatted_runs.append({
                'iteration': iteration_number,
                'pages': f'{start_page}-{end_page}',
                'records': records_count or 0,
                'status': run_status,
                'completed_at': completed_at
            })
        
        # Build response
        progress = {
            'session_id': session_id,
            'project_name': project_name,
            'total_pages_target': total_pages_target,
            'pages_completed': pages_completed,
            'current_iteration': current_iteration,
            'status': status,
            'percentage': percentage,
            'iterations_completed': iterations_completed,
            'total_iterations_needed': total_iterations_needed,
            'estimated_remaining_time': calculate_estimated_time(iterations_completed, total_iterations_needed),
            'runs': formatted_runs
        }
        
        return progress
        
    except Exception as e:
        print(f'[ERROR] Failed to fetch session progress: {str(e)}', file=sys.stderr)
        return {
            'error': str(e),
            'message': 'Failed to fetch session progress'
        }

if __name__ == '__main__':
    try:
        # Read input from stdin
        input_data = sys.stdin.read()
        if not input_data:
            print(json.dumps({'error': 'No input provided', 'success': False}))
            sys.exit(0)  # Exit 0 to allow frontend to handle the error
        
        data = json.loads(input_data)
        session_id = data.get('session_id')
        
        if not session_id:
            print(json.dumps({'error': 'session_id required', 'success': False}))
            sys.exit(0)
        
        # Get progress
        progress = get_session_progress(session_id)
        
        # Output as JSON to stdout
        print(json.dumps(progress))
        sys.exit(0)
        
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON input: {str(e)}', 'success': False}))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({'error': str(e), 'success': False}))
        sys.exit(0)
