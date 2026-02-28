"""
Analytics Service - Provides detailed analytics and data analysis
"""

from database import ParseHubDatabase
from datetime import datetime
from typing import Dict, List, Any, Optional

import json
import sqlite3
import sys


class AnalyticsService:
    def __init__(self):
        self.db = ParseHubDatabase()

    def get_project_analytics(self, project_token: str) -> Dict:
        """Get comprehensive analytics for a project"""
        conn = None
        try:
            conn = self.db.connect()
            if not conn:
                return self._default_analytics(project_token)
            
            cursor = self.db.cursor()

            # Get project ID
            try:
                cursor.execute('SELECT id FROM projects WHERE token = %s', (project_token,))
                project = cursor.fetchone()
            except Exception as e:
                print(f"Error querying project: {e}", file=sys.stderr)
                project = None

            if not project:
                return self._default_analytics(project_token)

            try:
                project_id = project['id'] if isinstance(project, dict) else project[0]
            except Exception as e:
                print(f"Error accessing project id: {e}", file=sys.stderr)
                return self._default_analytics(project_token)

            # Get all runs
            try:
                cursor.execute('''
                    SELECT id, run_token, status, pages_scraped, start_time, 
                           end_time, duration_seconds, records_count, created_at, is_empty
                    FROM runs 
                    WHERE project_id = %s 
                    ORDER BY created_at DESC
                ''', (project_id,))

                runs = [dict(row) if isinstance(row, sqlite3.Row) else dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
            except Exception as e:
                print(f"Error querying runs: {e}", file=sys.stderr)
                runs = []

            # Get recovery operations
            try:
                cursor.execute('''
                    SELECT * FROM recovery_operations 
                    WHERE project_id = %s 
                    ORDER BY created_at DESC
                    LIMIT 5
                ''', (project_id,))

                recovery_ops = [dict(row) if isinstance(row, sqlite3.Row) else dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
            except Exception as e:
                print(f"Error querying recovery ops: {e}", file=sys.stderr)
                recovery_ops = []

            # Calculate metrics
            total_records = sum(r.get('records_count') or 0 for r in runs)
            completed_runs = [r for r in runs if r.get('status') == 'complete']
            
            # Calculate scraping rate
            scraping_rate = self._calculate_scraping_rate(completed_runs)
            
            # Get recovery status
            recovery_status = self._get_recovery_status(recovery_ops)

            # Estimate completion
            current_count = runs[0].get('records_count') if runs else 0
            estimated_total = self._estimate_total_items(runs, total_records)

            return {
                'project_token': project_token,
                'overview': {
                    'total_runs': len(runs),
                    'completed_runs': len(completed_runs),
                    'total_records_scraped': total_records,
                    'unique_records_estimate': self._estimate_unique_records(runs),
                    'total_pages_analyzed': sum(r.get('pages_scraped') or 0 for r in runs),
                    'progress_percentage': self._calculate_progress(estimated_total, total_records)
                },
                'performance': {
                    'items_per_minute': scraping_rate.get('items_per_minute', 0),
                    'estimated_completion_time': scraping_rate.get('estimated_completion_time'),
                    'estimated_total_items': estimated_total,
                    'average_run_duration_seconds': self._avg_duration(completed_runs),
                    'current_items_count': current_count
                },
                'recovery': recovery_status,
                'runs_history': runs[:10],
                'data_quality': self._analyze_data_quality(project_id),
                'timeline': self._build_timeline(runs, recovery_ops)
            }

        except Exception as e:
            import traceback
            print(f"Error in get_project_analytics: {e}", file=sys.stderr)
            traceback.print_exc()
            return self._default_analytics(project_token)
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
    
    def _default_analytics(self, project_token: str, error: bool = False) -> Dict:
        """Return default analytics structure when no data available"""
        return {
            'project_token': project_token,
            'overview': {
                'total_runs': 0,
                'completed_runs': 0,
                'total_records_scraped': 0,
                'unique_records_estimate': 0,
                'total_pages_analyzed': 0,
                'progress_percentage': 0
            },
            'performance': {
                'items_per_minute': 0,
                'estimated_completion_time': None,
                'estimated_total_items': 0,
                'average_run_duration_seconds': 0,
                'current_items_count': 0
            },
            'recovery': {
                'in_recovery': False,
                'status': 'no_runs' if not error else 'error',
                'total_recovery_attempts': 0
            },
            'runs_history': [],
            'data_quality': {
                'average_completion_percentage': 0,
                'total_fields': 0
            },
            'timeline': []
        }

    def get_export_data(self, project_token: str, format: str = 'json') -> str:
        """Export analytics as JSON or CSV"""
        analytics = self.get_project_analytics(project_token)

        if format == 'json':
            return json.dumps(analytics, indent=2, default=str)
        
        elif format == 'csv':
            return self._convert_to_csv(analytics)

        return json.dumps(analytics, indent=2, default=str)

    def _calculate_scraping_rate(self, completed_runs: List[Dict]) -> Dict:
        """Calculate items scraped per minute"""
        if not completed_runs:
            return {
                'items_per_minute': 0,
                'estimated_completion_time': None
            }

        total_items = sum(r['records_count'] or 0 for r in completed_runs)
        total_minutes = sum((r['duration_seconds'] or 0) / 60 for r in completed_runs)

        if total_minutes == 0:
            return {
                'items_per_minute': 0,
                'estimated_completion_time': None
            }

        items_per_minute = total_items / total_minutes
        
        # Estimate time to complete (assuming 2x current progress)
        if items_per_minute > 0:
            estimated_remaining_minutes = (items_per_minute * total_minutes) / items_per_minute
            estimated_time = datetime.now() + timedelta(minutes=estimated_remaining_minutes)
        else:
            estimated_time = None

        return {
            'items_per_minute': round(items_per_minute, 2),
            'estimated_completion_time': estimated_time.isoformat() if estimated_time else None
        }

    def _estimate_total_items(self, runs: List[Dict], total_records: int) -> int:
        """Estimate total items based on current progress"""
        if not runs or total_records == 0:
            return 0

        latest_run = runs[0]
        
        # If run is complete, return actual total
        if latest_run['status'] == 'complete':
            return total_records

        # Otherwise estimate based on pages and items
        if latest_run['pages_scraped'] and latest_run['records_count']:
            items_per_page = latest_run['records_count'] / max(latest_run['pages_scraped'], 1)
            # Rough estimate: assume similar density continues
            estimated_total_pages = latest_run['pages_scraped'] * 1.5  # 1.5x multiplier
            estimated_total = int(items_per_page * estimated_total_pages)
            return max(estimated_total, total_records)

        return total_records

    def _estimate_unique_records(self, runs: List[Dict]) -> int:
        """Estimate unique records (accounting for duplicates in recovery)"""
        if not runs:
            return 0

        total = sum(r['records_count'] or 0 for r in runs)
        
        # Account for ~10% duplicates in recovery runs
        duplicates_estimate = int(total * 0.1)
        return max(total - duplicates_estimate, 0)

    def _calculate_progress(self, estimated_total: int, current_count: int) -> int:
        """Calculate progress percentage"""
        if estimated_total == 0:
            return 0

        progress = int((current_count / estimated_total) * 100)
        return min(progress, 99)  # Cap at 99% until completion

    def _avg_duration(self, runs: List[Dict]) -> float:
        """Calculate average run duration in seconds"""
        if not runs:
            return 0

        durations = [r['duration_seconds'] for r in runs if r['duration_seconds']]
        if not durations:
            return 0

        return round(sum(durations) / len(durations), 2)

    def _analyze_data_quality(self, project_id: int) -> Dict:
        """Analyze data quality and completeness"""
        try:
            conn = self.db.connect()
            cursor = self.db.cursor()

            # Get all data keys
            cursor.execute('''
                SELECT DISTINCT data_key FROM scraped_data 
                WHERE project_id = %s
            ''', (project_id,))

            fields = [row['data_key'] for row in cursor.fetchall()]

            if not fields:
                conn.close()
                self.db.disconnect()
                return {'total_fields': 0, 'fields': []}

            # Calculate completion percentage for each field
            field_stats = []
            for field in fields:
                cursor.execute('''
                    SELECT COUNT(*) as total_records,
                           SUM(CASE WHEN data_value IS NOT NULL AND data_value != '' THEN 1 ELSE 0 END) as filled
                    FROM scraped_data 
                    WHERE project_id = %s AND data_key = %s
                ''', (project_id, field))

                stats = cursor.fetchone()
                total = (stats['total_records'] if isinstance(stats, dict) else stats[0]) if stats else 0
                filled = (stats.get('filled') or 0) if isinstance(stats, dict) else (stats[1] if stats and len(stats) > 1 else 0)

                completion = int((filled / total * 100)) if total > 0 else 0

                field_stats.append({
                    'field': field,
                    'completion_percentage': completion,
                    'filled_records': filled,
                    'total_records': total
                })

            conn.close()
            self.db.disconnect()

            # Calculate average quality
            avg_completion = sum(f['completion_percentage'] for f in field_stats) / len(field_stats) if field_stats else 0

            return {
                'total_fields': len(fields),
                'average_completion_percentage': round(avg_completion, 2),
                'fields': sorted(field_stats, key=lambda x: x['completion_percentage'], reverse=True)
            }

        except Exception as e:
            return {'error': str(e)}

    def _get_recovery_status(self, recovery_ops: List[Dict]) -> Dict:
        """Get latest recovery status"""
        if not recovery_ops:
            return {
                'in_recovery': False,
                'status': 'none',
                'total_recovery_attempts': 0
            }

        latest = recovery_ops[0]
        in_recovery = latest['status'] in ['pending', 'in_progress']

        completed_recoveries = [op for op in recovery_ops if op['status'] == 'completed']
        total_recovered_items = sum(op['recovery_data_count'] or 0 for op in completed_recoveries)

        return {
            'in_recovery': in_recovery,
            'status': latest['status'],
            'latest_recovery_timestamp': latest['stopped_timestamp'],
            'last_product_name': latest['last_product_name'],
            'last_product_url': latest['last_product_url'],
            'total_recovery_attempts': len(recovery_ops),
            'completed_recoveries': len(completed_recoveries),
            'total_recovered_items': total_recovered_items,
            'original_data_count': latest['original_data_count'],
            'recovery_data_count': latest['recovery_data_count'],
            'final_data_count': latest['final_data_count'],
            'duplicates_removed': latest['duplicates_removed']
        }

    def _build_timeline(self, runs: List[Dict], recovery_ops: List[Dict]) -> List[Dict]:
        """Build a timeline of all events"""
        events = []

        # Add run events
        for run in runs:
            events.append({
                'timestamp': run['start_time'],
                'type': 'run_started',
                'run_token': run['run_token'],
                'details': f"Run started"
            })

            if run['end_time']:
                events.append({
                    'timestamp': run['end_time'],
                    'type': 'run_ended',
                    'run_token': run['run_token'],
                    'status': run['status'],
                    'records': run['records_count'],
                    'details': f"Run completed with {run['records_count']} records"
                })

        # Add recovery events
        for recovery in recovery_ops:
            events.append({
                'timestamp': recovery['stopped_timestamp'],
                'type': 'run_stopped',
                'details': f"Run stopped at: {recovery['last_product_name']}"
            })

            if recovery['recovery_triggered_timestamp']:
                events.append({
                    'timestamp': recovery['recovery_triggered_timestamp'],
                    'type': 'recovery_triggered',
                    'recovery_id': recovery['id'],
                    'details': 'Auto-recovery triggered'
                })

            if recovery['recovery_started_timestamp']:
                events.append({
                    'timestamp': recovery['recovery_started_timestamp'],
                    'type': 'recovery_started',
                    'recovery_id': recovery['id'],
                    'details': 'Recovery run started'
                })

            if recovery['recovery_completed_timestamp']:
                events.append({
                    'timestamp': recovery['recovery_completed_timestamp'],
                    'type': 'recovery_completed',
                    'recovery_id': recovery['id'],
                    'new_items': recovery['final_data_count'] - recovery['original_data_count'],
                    'duplicates': recovery['duplicates_removed'],
                    'details': f"Recovery completed: +{recovery['final_data_count'] - recovery['original_data_count']} new items"
                })

        # Sort by timestamp
        events.sort(key=lambda x: x['timestamp'], reverse=True)
        return events

    def _convert_to_csv(self, analytics: Dict) -> str:
        """Convert analytics to CSV format"""
        csv_lines = [
            "ParseHub Project Analytics Export",
            f"Project Token: {analytics.get('project_token', 'N/A')}",
            f"Generated: {datetime.now().isoformat()}",
            ""
        ]

        overview = analytics.get('overview', {})
        csv_lines.extend([
            "=== OVERVIEW ===",
            f"Total Runs, {overview.get('total_runs', 0)}",
            f"Completed Runs, {overview.get('completed_runs', 0)}",
            f"Total Records Scraped, {overview.get('total_records_scraped', 0)}",
            f"Progress Percentage, {overview.get('progress_percentage', 0)}%",
            ""
        ])

        performance = analytics.get('performance', {})
        csv_lines.extend([
            "=== PERFORMANCE ===",
            f"Items Per Minute, {performance.get('items_per_minute', 0)}",
            f"Estimated Total Items, {performance.get('estimated_total_items', 0)}",
            f"Average Run Duration (seconds), {performance.get('average_run_duration_seconds', 0)}",
            ""
        ])

        return "\n".join(csv_lines)

    def trigger_post_run_analytics(self, metadata_id: int, run_token: str = None) -> Dict:
        """
        Trigger analytics after a successful run completion
        
        Args:
            metadata_id: ID of metadata record
            run_token: Optional ParseHub run token for additional context
            
        Returns:
            Dictionary with analytics result
        """
        try:
            # Get metadata record
            metadata = self.db.get_metadata_by_id(metadata_id)
            
            if not metadata:
                return {
                    'success': False,
                    'error': f'Metadata record not found: {metadata_id}'
                }
            
            project_token = metadata.get('project_token')
            if not project_token:
                return {
                    'success': False,
                    'error': f'No project_token in metadata {metadata_id}'
                }
            
            # Get analytics for the project
            analytics = self.get_project_analytics(project_token)
            
            if not analytics:
                return {
                    'success': False,
                    'error': f'Failed to generate analytics for {project_token}'
                }
            
            # Store to database for caching
            self.db.store_analytics_data(
                project_token,
                run_token,
                analytics,
                csv_data=self.generate_analytics_csv(project_token)
            )
            
            print(f"[ANALYTICS] Triggered for metadata {metadata_id} (project: {metadata.get('project_name')})", file=sys.stderr)
            
            return {
                'success': True,
                'metadata_id': metadata_id,
                'project_token': project_token,
                'project_name': metadata.get('project_name'),
                'analytics': {
                    'total_records': analytics.get('overview', {}).get('total_records_scraped'),
                    'completion_percentage': analytics.get('overview', {}).get('progress_percentage'),
                    'total_runs': analytics.get('overview', {}).get('total_runs')
                }
            }
            
        except Exception as e:
            print(f"[ERROR] Error triggering post-run analytics: {str(e)}", file=sys.stderr)
            return {
                'success': False,
                'error': str(e)
            }


if __name__ == "__main__":
    service = AnalyticsService()
    print("[OK] Analytics Service initialized!")