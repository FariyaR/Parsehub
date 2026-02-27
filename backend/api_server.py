"""
Flask API Server for ParseHub Real-Time Monitoring
Exposes REST endpoints for the Next.js frontend to control and monitor real-time data collection
"""

from pathlib import Path
import sys
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from dotenv import load_dotenv
import os
import logging
import uuid
from typing import Optional, Dict, List
import json
from datetime import datetime
import requests

# Load environment variables (.env file in the same directory)
load_dotenv()

# Add the backend directory itself to sys.path so all local modules are
# importable regardless of the working directory Railway sets at runtime.
_backend_dir = Path(__file__).parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# Import local services — straightforward single-attempt import.
# The old try/except "fallback" re-imported the identical names and would
# simply re-raise any ImportError, hiding the real missing module.
from database import ParseHubDatabase
from monitoring_service import MonitoringService
from analytics_service import AnalyticsService
from excel_import_service import ExcelImportService
from auto_runner_service import AutoRunnerService
from fetch_projects import fetch_all_projects, get_all_projects_with_cache
from incremental_scraping_scheduler import start_incremental_scraping_scheduler, stop_incremental_scraping_scheduler
from auto_sync_service import start_auto_sync_service, stop_auto_sync_service, get_auto_sync_service

# Initialize Flask app
app = Flask(__name__)

# All calls to this backend come from the Next.js server (server-to-server),
# never directly from a browser.  Server-side requests carry no Origin header,
# so Flask-CORS is effectively a no-op here — but we still configure it
# permissively so Railway health checks and any future direct-call use case
# never get a CORS rejection.
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Service registry ─────────────────────────────────────────────────────────────
# Services are created ONCE after the module finishes loading.
# No DB connection is opened at import time (fixes "too many clients").
_db = None
_monitoring_service = None
_analytics_service = None
_excel_import_service = None
_auto_runner_service = None
_services_initialized = False
_services_lock = __import__('threading').Lock()


def get_db() -> 'ParseHubDatabase':
    """Return the module-level DB helper (no connection opened yet)."""
    global _db
    if _db is None:
        _db = ParseHubDatabase()   # __init__ no longer opens a connection
    return _db


def _initialize_services():
    """Initialize all services exactly once per worker process.

    IMPORTANT: This function NEVER raises.  Any exception is caught, logged,
    and the app continues running.  The health endpoint must always work.

    _services_initialized is set to True regardless of DB success so that
    we don't hammer PostgreSQL with connection retries on every request.
    DB-dependent routes will get a 503 on first failure; the pool's
    pool_pre_ping will recover connections automatically when PG is healthy.
    """
    global _db, _monitoring_service, _analytics_service
    global _excel_import_service, _auto_runner_service, _services_initialized

    with _services_lock:
        if _services_initialized:
            return

        # Mark initialized FIRST — stops retry storms against an overloaded DB
        _services_initialized = True

        try:
            _db = ParseHubDatabase()   # metadata only, never opens a connection
        except Exception as exc:
            logger.critical(f'[boot] ParseHubDatabase() failed (fatal): {exc}')
            return

        try:
            _db.init_db()   # runs schema migrations
        except Exception as exc:
            logger.error(
                f'[boot] init_db() failed — DB may not be ready yet: {exc}\n'
                '        DB-dependent routes will return 503 until DB is reachable.'
            )
            # Don't return — still set up the non-DB services below

        try:
            _monitoring_service   = MonitoringService()
            _analytics_service    = AnalyticsService()
            _excel_import_service = ExcelImportService(_db)
            _auto_runner_service  = AutoRunnerService()
        except Exception as exc:
            logger.error(f'[boot] Service setup failed: {exc}')

        try:
            _start_background_services()
        except Exception as exc:
            logger.error(f'[boot] Background services failed: {exc}')

        logger.info('[boot] Initialization complete (DB errors above are non-fatal).')


@app.before_request
def ensure_services():
    """Attach a request_id to every request and lazily initialize services.

    request_id is a short hex token included in every error response so that
    log lines can be correlated without exposing internal stack traces.

    Service init is SKIPPED for /api/health so that the liveness probe always
    returns 200 even when the database is completely unavailable.
    """
    g.request_id = uuid.uuid4().hex[:12]
    # All routes that need the DB use g.db; get_db() is cheap (no connection until first use).
    g.db = get_db()
    if request.path in ('/api/health', '/api/health/'):
        return
    if not _services_initialized:
        _initialize_services()



@app.teardown_appcontext
def return_db_connection(exc=None):
    """Return the PostgreSQL connection to the pool after every request.
    Fully guarded — must never raise under any circumstances.
    """
    try:
        conn = g.get('db') or _db
        if conn is not None and getattr(conn, 'use_postgres', False):
            conn.disconnect()   # returns raw_connection to SQLAlchemy pool
    except Exception as e:
        logger.warning(f'[teardown] Error returning DB connection: {e}')

# ── Health Blueprint ─────────────────────────────────────────────────────────────
# Rules that eliminate AssertionError on startup:
#   1. Routes live in a Blueprint (not on `app` directly).
#   2. Every @route decorator has an explicit `endpoint=` argument.
#   3. The Python function names are unique across the whole file
#      (health_liveness_route / health_db_route) — Flask cannot use them as
#      accidental default endpoint names.
#   4. register_blueprint is called exactly once at module level.

from flask import Blueprint as _Blueprint

_health_bp = _Blueprint('health', __name__)


@_health_bp.route('/api/health', methods=['GET'], endpoint='liveness')
def health_liveness_route():
    """Lightweight liveness probe — never touches the database."""
    return jsonify({'status': 'ok', 'service': 'parsehub-backend'}), 200


@_health_bp.route('/api/health/db', methods=['GET'], endpoint='readiness')
def health_db_route():
    """Readiness probe — verifies database connectivity."""
    try:
        from db_pool import ping_db
        ok = ping_db()
        if ok:
            return jsonify({'status': 'ok', 'database': 'reachable'}), 200
        return jsonify({'status': 'error', 'database': 'unreachable'}), 503
    except Exception as exc:
        return jsonify({'status': 'error', 'detail': str(exc)}), 503


# Register exactly once — guard prevents double-registration on hot reloads
if 'health' not in app.blueprints:
    app.register_blueprint(_health_bp)


# ── Background services ───────────────────────────────────────────────────────────
# Started inside _initialize_services() → once per worker, after DB is ready.

def _start_background_services():
    """Start background scheduler services (called inside _initialize_services)."""
    try:
        check_interval = int(os.getenv('INCREMENTAL_SCRAPING_INTERVAL', '30'))
        sync_interval  = int(os.getenv('AUTO_SYNC_INTERVAL', '5'))
        logger.info(
            f'[bg] Starting background services: scraper={check_interval}m, sync={sync_interval}m'
        )
        if get_auto_sync_service() is None:
            start_incremental_scraping_scheduler(check_interval)
            start_auto_sync_service(sync_interval)
            logger.info('[bg] Background services started.')
    except Exception as exc:
        logger.error(f'[bg] Failed to start background services: {exc}')


# API Key validation
BACKEND_API_KEY = os.getenv('BACKEND_API_KEY', 't_hmXetfMCq3')


def validate_api_key(request_obj):
    """Validate API key from Authorization header"""
    auth_header = request_obj.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return False

    token = auth_header.replace('Bearer ', '')
    return token == BACKEND_API_KEY

# ========== MONITORING ENDPOINTS ==========


@app.route('/api/monitor/start', methods=['POST'])
def start_monitoring():
    """
    Start real-time monitoring of a ParseHub run

    Request body:
    {
        "run_token": "...",
        "pages": 1,
        "project_id": 1 (optional)
    }
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        run_token = data.get('run_token')
        pages = data.get('pages', 1)
        project_id = data.get('project_id')

        if not run_token:
            return jsonify({'error': 'Missing required field: run_token'}), 400

        # If project_id not provided, infer from run token in active runs
        if not project_id:
            try:
                with open('../active_runs.json', 'r') as f:
                    active_runs = json.load(f)
                    for project in active_runs.get('projects', []):
                        for run in project.get('runs', []):
                            if run.get('run_token') == run_token:
                                project_id = project.get('id')
                                break
            except:
                pass

        if not project_id:
            return jsonify({'error': 'Could not determine project_id'}), 400

        # Create monitoring session in database
        session_id = g.db.create_monitoring_session(project_id, run_token, pages)

        if not session_id:
            return jsonify({'error': 'Failed to create monitoring session'}), 500

        # Start real-time monitoring in background
        # This will run the monitoring loop in the monitoring service
        try:
            _monitoring_service.monitor_run_realtime(
                project_id, run_token, pages)
        except Exception as e:
            logger.error(f'Error starting real-time monitoring: {e}')
            # Still return success since session was created, monitoring will retry

        return jsonify({
            'session_id': session_id,
            'run_token': run_token,
            'project_id': project_id,
            'target_pages': pages,
            'status': 'monitoring_started'
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/monitor/start: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/monitor/status', methods=['GET'])
def get_monitor_status():
    """
    Get current monitoring status

    Query parameters:
    - session_id: Monitoring session ID (optional)
    - project_id: Project ID (optional)
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        session_id = request.args.get('session_id', type=int)
        project_id = request.args.get('project_id', type=int)

        if not session_id and not project_id:
            return jsonify({'error': 'Missing required parameter: session_id or project_id'}), 400

        # Get session summary
        if session_id:
            summary = g.db.get_session_summary(session_id)
        else:  # project_id provided
            summary = g.db.get_monitoring_status_for_project(project_id)

        if not summary:
            return jsonify({'error': 'Monitoring session not found'}), 404

        return jsonify({
            'success': True,
            'status': summary['status'],
            'session_id': summary.get('session_id'),
            'project_id': summary.get('project_id'),
            'run_token': summary.get('run_token'),
            'target_pages': summary.get('target_pages'),
            'total_pages': summary.get('total_pages'),
            'total_records': summary.get('total_records'),
            'progress_percentage': summary.get('progress_percentage'),
            'current_url': summary.get('current_url'),
            'error_message': summary.get('error_message'),
            'start_time': summary.get('start_time'),
            'end_time': summary.get('end_time'),
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/monitor/status: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/monitor/data', methods=['GET'])
def get_monitor_data():
    """
    Get scraped data from a monitoring session

    Query parameters:
    - session_id: Monitoring session ID (required)
    - limit: Number of records to fetch (default: 100)
    - offset: Number of records to skip (default: 0)
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        session_id = request.args.get('session_id', type=int)
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)

        if not session_id:
            return jsonify({'error': 'Missing required parameter: session_id'}), 400

        # Validate limit and offset
        limit = min(max(limit, 1), 1000)  # Clamp between 1 and 1000
        offset = max(offset, 0)

        # Get records from database
        records = g.db.get_session_records(session_id, limit, offset)
        total = g.db.get_session_records_count(session_id)

        return jsonify({
            'success': True,
            'session_id': session_id,
            'records': records,
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': (offset + limit) < total,
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/monitor/data: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/monitor/data/csv', methods=['GET'])
def get_monitor_data_csv():
    """
    Export monitoring session data as CSV

    Query parameters:
    - session_id: Monitoring session ID (required)
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        session_id = request.args.get('session_id', type=int)

        if not session_id:
            return jsonify({'error': 'Missing required parameter: session_id'}), 400

        # Get CSV data
        csv_data = g.db.get_data_as_csv(session_id)

        if not csv_data:
            return jsonify({'error': 'No records found for session'}), 404

        return csv_data, 200, {
            'Content-Type': 'text/csv',
            'Content-Disposition': f'attachment; filename="session_{session_id}_data.csv"'
        }

    except Exception as e:
        logger.error(f'Error in /api/monitor/data/csv: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/monitor/stop', methods=['POST'])
def stop_monitoring():
    """
    Stop real-time monitoring of a run

    Request body:
    {
        "session_id": 1 (optional),
        "run_token": "..." (optional)
    }
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        session_id = data.get('session_id')
        run_token = data.get('run_token')

        if not session_id and not run_token:
            return jsonify({'error': 'Missing required field: session_id or run_token'}), 400

        # Update session status to cancelled
        if session_id:
            g.db.update_monitoring_session(session_id, status='cancelled')
            summary = g.db.get_session_summary(session_id)
        else:
            # Find session by run_token (get most recent)
            # This would need a database method to find by run_token
            return jsonify({'error': 'Session ID required for stopping'}), 400

        return jsonify({
            'success': True,
            'status': 'cancelled',
            'session_id': session_id,
            'total_records': summary.get('total_records') if summary else 0,
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/monitor/stop: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/runs/<run_token>/cancel', methods=['POST'])
def cancel_run(run_token: str):
    """
    Cancel a running ParseHub job

    URL parameter:
    - run_token: The token of the run to cancel
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        api_key = os.getenv('PARSEHUB_API_KEY')

        if not api_key:
            logger.error('[API] Missing PARSEHUB_API_KEY for cancel run')
            return jsonify({'error': 'Missing API key configuration'}), 500

        if not run_token:
            return jsonify({'error': 'Missing required parameter: run_token'}), 400

        logger.info(f'[API] Cancelling run: {run_token}')

        # Call ParseHub API to cancel the run
        cancel_url = f'https://www.parsehub.com/api/v2/runs/{run_token}/cancel'

        response = requests.post(
            cancel_url,
            data={'api_key': api_key},
            timeout=10
        )

        if response.status_code != 200:
            logger.error(
                f'[API] ParseHub cancel failed: {response.status_code} - {response.text}')
            return jsonify({
                'error': 'Failed to cancel run',
                'details': response.text
            }), response.status_code

        result = response.json()

        logger.info(f'[API] Run cancelled successfully: {run_token}')

        return jsonify({
            'success': True,
            'message': f'Run {run_token} cancelled successfully',
            'run': result
        }), 200

    except requests.exceptions.Timeout:
        logger.error(f'[API] Timeout while cancelling run {run_token}')
        return jsonify({'error': 'Request timeout'}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f'[API] Network error cancelling run {run_token}: {e}')
        return jsonify({'error': 'Network error', 'details': str(e)}), 500
    except json.JSONDecodeError:
        logger.error(f'[API] Invalid JSON response from ParseHub')
        return jsonify({'error': 'Invalid response from ParseHub API'}), 500
    except Exception as e:
        logger.error(f'[API] Error cancelling run {run_token}: {e}')
        return jsonify({'error': str(e)}), 500


# ========== METADATA MANAGEMENT ENDPOINTS ==========

@app.route('/api/metadata', methods=['GET'])
def get_metadata():
    """
    Get metadata records with optional filtering

    Query parameters:
    - project_token: Filter by project token
    - region: Filter by region
    - country: Filter by country
    - brand: Filter by brand
    - limit: Records per page (default: 100)
    - offset: Pagination offset (default: 0)
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        project_token = request.args.get('project_token')
        region = request.args.get('region')
        country = request.args.get('country')
        brand = request.args.get('brand')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))

        records = g.db.get_metadata_filtered(
            project_token=project_token,
            region=region,
            country=country,
            brand=brand,
            limit=limit,
            offset=offset
        )

        return jsonify({
            'success': True,
            'records': records,
            'count': len(records),
            'limit': limit,
            'offset': offset
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/metadata: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/metadata/<int:metadata_id>', methods=['GET'])
def get_metadata_by_id(metadata_id):
    """Get a specific metadata record"""
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        record = g.db.get_metadata_by_id(metadata_id)

        if not record:
            return jsonify({'error': 'Metadata not found'}), 404

        return jsonify({
            'success': True,
            'record': record
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/metadata/{metadata_id}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/metadata/<int:metadata_id>', methods=['PUT'])
def update_metadata(metadata_id):
    """
    Update metadata record

    Request body:
    {
        "current_page_scraped": 5,
        "current_product_scraped": 100,
        "last_known_url": "..."
    }
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()

        success = g.db.update_metadata_progress(
            metadata_id,
            current_page_scraped=data.get('current_page_scraped'),
            current_product_scraped=data.get('current_product_scraped'),
            last_known_url=data.get('last_known_url')
        )

        if not success:
            return jsonify({'error': 'Failed to update metadata'}), 500

        record = g.db.get_metadata_by_id(metadata_id)

        return jsonify({
            'success': True,
            'message': 'Metadata updated',
            'record': record
        }), 200

    except Exception as e:
        logger.error(f'Error updating /api/metadata/{metadata_id}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/metadata/<int:metadata_id>', methods=['DELETE'])
def delete_metadata(metadata_id):
    """Delete a metadata record"""
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        success = g.db.delete_metadata(metadata_id)

        if not success:
            return jsonify({'error': 'Failed to delete metadata'}), 500

        return jsonify({
            'success': True,
            'message': f'Metadata {metadata_id} deleted'
        }), 200

    except Exception as e:
        logger.error(f'Error deleting /api/metadata/{metadata_id}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/metadata/<int:metadata_id>/completion-status', methods=['GET'])
def get_completion_status(metadata_id):
    """Get completion status for a metadata record"""
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        result = auto_runner_service.check_scraping_completion(metadata_id)

        if not result.get('success'):
            return jsonify({'error': result.get('error')}), 400

        return jsonify({
            'success': True,
            **result
        }), 200

    except Exception as e:
        logger.error(
            f'Error in /api/metadata/{metadata_id}/completion-status: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/metadata/import', methods=['POST'])
def import_metadata():
    """
    Import metadata from Excel file

    Request form data:
    - file: Excel file (multipart/form-data)
    - uploaded_by: Username of uploader (optional)
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        uploaded_by = request.form.get('uploaded_by', 'api')

        if not file.filename:
            return jsonify({'error': 'No file selected'}), 400

        if not file.filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            return jsonify({'error': 'Only .xlsx, .xls, and .csv files are supported'}), 400

        # Save file temporarily
        import tempfile
        file_extension = os.path.splitext(file.filename)[1].lower() or '.xlsx'
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp:
            file.save(tmp.name)
            temp_path = tmp.name

        try:
            # Import metadata
            result = excel_import_service.bulk_import_metadata(
                temp_path, uploaded_by)

            # Clean up temp file
            os.remove(temp_path)

            return jsonify(result), 200 if result.get('success') else 400

        except Exception as e:
            os.remove(temp_path)
            raise e

    except Exception as e:
        logger.error(f'Error in /api/metadata/import: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/metadata/import-history', methods=['GET'])
def get_import_history():
    """
    Get import batch history

    Query parameters:
    - limit: Records per page (default: 50)
    - offset: Pagination offset (default: 0)
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))

        batches = g.db.get_import_batches(limit, offset)

        return jsonify({
            'success': True,
            'batches': batches,
            'count': len(batches),
            'limit': limit,
            'offset': offset
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/metadata/import-history: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/filters/values', methods=['GET'])
def get_filter_values():
    """
    Get distinct values for filter fields

    Query parameters:
    - field: 'region', 'country', or 'brand'
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        field = request.args.get('field')

        if field not in ['region', 'country', 'brand']:
            return jsonify({'error': 'Invalid field. Must be: region, country, or brand'}), 400

        values = g.db.get_distinct_filter_values(field)

        return jsonify({
            'success': True,
            'field': field,
            'values': values,
            'count': len(values)
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/filters/values: {e}')
        return jsonify({'error': str(e)}), 500


# ========== BATCH OPERATIONS ENDPOINTS ==========

@app.route('/api/runs/batch-execute', methods=['POST'])
def batch_execute_runs():
    """
    Execute multiple projects sequentially

    Request body:
    {
        "metadata_ids": [1, 2, 3],
        "pages_per_iteration": 5 (optional)
    }
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        metadata_ids = data.get('metadata_ids', [])
        pages_per_run = data.get('pages_per_run', 1)

        if not metadata_ids:
            return jsonify({'error': 'Missing required field: metadata_ids'}), 400

        run_queue = []

        for metadata_id in metadata_ids:
            metadata = g.db.get_metadata_by_id(metadata_id)

            if not metadata:
                logger.warning(f"Metadata {metadata_id} not found, skipping")
                continue

            project_token = metadata.get('project_token')
            if not project_token:
                logger.warning(
                    f"Metadata {metadata_id} has no project_token, skipping")
                continue

            run_queue.append({
                'metadata_id': metadata_id,
                'project_token': project_token,
                'project_name': metadata.get('project_name'),
                'status': 'queued'
            })

        if not run_queue:
            return jsonify({'error': 'No valid projects to execute'}), 400

        return jsonify({
            'success': True,
            'message': f'Queued {len(run_queue)} projects for execution',
            'queue_id': datetime.now().isoformat(),
            'run_queue': run_queue
        }), 200

    except Exception as e:
        logger.error(f'Error in /api/runs/batch-execute: {e}')
        return jsonify({'error': str(e)}), 500


# ========== PROJECTS ENDPOINTS ==========

@app.route('/api/projects', methods=['GET'])
def get_projects():
    """
    Fetch paginated projects from ParseHub with metadata enrichment
    Returns paginated results with fast response times

    Query parameters:
    - page: page number (default 1)
    - limit: items per page (default 50, max 1000)
    - filter_keyword: optional keyword filter for title/description
    - region: filter by region
    - country: filter by country
    - brand: filter by brand
    - website: filter by website
    """
    try:
        api_key = request.args.get('api_key') or os.getenv('PARSEHUB_API_KEY')
        page = request.args.get('page', 1, type=int)
        # Default 50, max 1000 per page
        limit = min(int(request.args.get('limit', 50)), 1000)
        filter_keyword = request.args.get('filter_keyword', '').lower().strip()

        # Filter parameters (new)
        region = request.args.get('region')
        country = request.args.get('country')
        brand = request.args.get('brand')
        website = request.args.get('website')

        if not api_key:
            logger.error('[API] Missing API key for projects fetch')
            return jsonify({'error': 'Missing API key'}), 400

        if page < 1:
            page = 1
        if limit < 1 or limit > 1000:
            limit = 50

        # If any filters are applied, delegate to search endpoint logic
        if region or country or brand or website:
            logger.info(
                f'[API] Filters detected - delegating to search logic: region={region}, country={country}, brand={brand}, website={website}')
            offset = (page - 1) * limit

            result = g.db.get_projects_with_website_grouping(
                limit=limit,
                offset=offset,
                region=region,
                country=country,
                brand=brand,
                website=website
            )

            if result.get('success'):
                return jsonify({
                    'success': True,
                    'projects': result.get('by_website', []),
                    'by_website': result.get('by_website', []),
                    'project_count': len(result.get('by_project', [])),
                    'pagination': {
                        'page': page,
                        'limit': limit,
                        'total': result.get('total', 0),
                        'total_pages': (result.get('total', 0) + limit - 1) // limit,
                        'has_more': offset + limit < result.get('total', 0)
                    }
                }), 200
            else:
                return jsonify({'error': result.get('error', 'Failed to fetch filtered projects')}), 500

        logger.info(
            f'[API] Fetching projects: page={page}, limit={limit}, filter={filter_keyword or "none"}')

        # Fetch all projects from cache (this is still fast via cache)
        all_projects = get_all_projects_with_cache(api_key)
        logger.info(
            f'[API] Retrieved {len(all_projects)} total projects from cache')

        # Sync projects if cache was refreshed (optional, can skip on pagination calls)
        # This is now deferred to reduce response time
        if page == 1:
            logger.info('[API] First page - syncing projects in background...')
            try:
                sync_result = g.db.sync_projects(all_projects)
                metadata_sync_result = g.db.sync_metadata_with_projects(
                    all_projects)
                logger.info(
                    f'[API] Sync result: {sync_result}, Metadata sync: {metadata_sync_result}')
            except Exception as sync_err:
                logger.warning(f'[API] Background sync warning: {sync_err}')
                # Don't fail on sync errors

        # Apply keyword filter if provided
        filtered_projects = all_projects
        if filter_keyword:
            filtered_projects = [
                p for p in all_projects
                if filter_keyword in p.get('title', '').lower() or
                filter_keyword in p.get('description', '').lower()
            ]
            logger.info(
                f'[API] Filtered {len(filtered_projects)} projects by keyword: {filter_keyword}')

        total = len(filtered_projects)

        # Apply pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_projects = filtered_projects[start_idx:end_idx]

        logger.info(
            f'[API] Paginated: {len(paginated_projects)} projects on page {page}')

        # Enrich paginated results with metadata
        enriched_projects = g.db.match_projects_to_metadata_batch(
            paginated_projects)
        metadata_matches = sum(
            1 for p in enriched_projects if p.get('metadata'))

        # Group this page's projects by website (optional, for UI)
        websites_dict = {}
        for proj in enriched_projects:
            title = proj.get('title', 'Unknown')
            website = g.db.extract_website_from_title(title)

            if website not in websites_dict:
                websites_dict[website] = {
                    'website': website,
                    'projects': [],
                    'project_count': 0
                }

            websites_dict[website]['projects'].append(proj)
            websites_dict[website]['project_count'] += 1

        by_website = list(websites_dict.values())

        response_data = {
            'success': True,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'total_pages': (total + limit - 1) // limit,
                'has_more': end_idx < total
            },
            'metadata_matches': metadata_matches,
            'project_count': len(enriched_projects),
            'by_website': by_website,
            'projects': enriched_projects
        }

        logger.info(
            f'[API] ✅ Returning page {page}/{(total + limit - 1) // limit} with {len(enriched_projects)} projects ({metadata_matches} enriched)')
        return jsonify(response_data), 200

    except ValueError as e:
        logger.error(f'[API] Validation error: {str(e)}')
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f'[API] Error fetching projects: {str(e)}')
        return jsonify({'error': 'Failed to fetch projects'}), 500


@app.route('/api/projects/bulk', methods=['GET'])
def get_projects_bulk():
    """
    Fetch ALL projects at once (heavy operation)
    WARNING: This endpoint may take 60-300 seconds for 100+ projects
    Use /api/projects with pagination for fast responses instead

    Returns all projects enriched with metadata and grouped by website
    """
    try:
        api_key = request.args.get('api_key') or os.getenv('PARSEHUB_API_KEY')

        if not api_key:
            logger.error('[API] Missing API key for bulk projects fetch')
            return jsonify({'error': 'Missing API key'}), 400

        logger.info(
            '[API] Fetching ALL projects (bulk operation - may take time)...')
        projects = get_all_projects_with_cache(api_key)

        logger.info(f'[API] Retrieved {len(projects)} projects from cache/API')

        # Persist project list and refresh metadata links
        sync_result = g.db.sync_projects(projects)
        metadata_sync_result = g.db.sync_metadata_with_projects(projects)
        logger.info(
            f'[API] Sync result: {sync_result}, Metadata sync: {metadata_sync_result}')

        # Enrich projects with metadata in batch
        projects = g.db.match_projects_to_metadata_batch(projects)
        metadata_matches = sum(1 for p in projects if p.get('metadata'))
        logger.info(
            f'[API] Matched {metadata_matches}/{len(projects)} projects with metadata')

        # Group projects by website domain
        websites_dict = {}
        for proj in projects:
            title = proj.get('title', 'Unknown')
            website = g.db.extract_website_from_title(title)

            if website not in websites_dict:
                websites_dict[website] = {
                    'website': website,
                    'projects': [],
                    'project_count': 0
                }

            websites_dict[website]['projects'].append(proj)
            websites_dict[website]['project_count'] += 1

        by_website = list(websites_dict.values())

        response_data = {
            'success': True,
            'total': len(projects),
            'metadata_matches': metadata_matches,
            'sync_result': sync_result,
            'metadata_sync_result': metadata_sync_result,
            'by_website': by_website,
            'projects': projects  # Renamed from 'by_project'
        }

        logger.info(
            f'[API] ✅ Bulk fetch complete: {len(by_website)} website groups, {metadata_matches} enriched projects')
        return jsonify(response_data), 200

    except ValueError as e:
        logger.error(f'[API] Validation error: {str(e)}')
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f'[API] Error fetching projects: {str(e)}')
        return jsonify({'error': 'Failed to fetch projects'}), 500


@app.route('/api/projects/sync', methods=['POST'])
def sync_projects():
    """
    Sync projects from ParseHub API to database
    Fetches all projects and stores them in the database
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        api_key = request.json.get('api_key') if request.json else None
        api_key = api_key or os.getenv('PARSEHUB_API_KEY')

        if not api_key:
            return jsonify({'error': 'Missing API key'}), 400

        logger.info('[API] Starting project sync from ParseHub API...')

        # Fetch all projects from API
        projects = get_all_projects_with_cache(api_key)

        if not projects:
            return jsonify({'error': 'Failed to fetch projects from API'}), 500

        # Sync to database
        result = g.db.sync_projects(projects)
        metadata_sync_result = g.db.sync_metadata_with_projects(projects)

        logger.info(f'[API] Project sync complete: {result}')
        logger.info(f'[API] Metadata sync complete: {metadata_sync_result}')

        return jsonify({
            'success': result['success'],
            'message': f"Synced {result.get('total', 0)} projects to database",
            'metadata_sync_result': metadata_sync_result,
            **result
        }), 200

    except Exception as e:
        logger.error(f'[API] Error syncing projects: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/projects/search', methods=['GET'])
def search_projects():
    """
    Search projects from database with optional filtering
    Query parameters: region, country, brand, website, limit, offset, group_by_website
    Returns projects with linked metadata, grouped by website if requested
    """
    try:
        # Get filter parameters
        region = request.args.get('region')
        country = request.args.get('country')
        brand = request.args.get('brand')
        website = request.args.get('website')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        group_by_website = request.args.get(
            'group_by_website', 'true').lower() == 'true'

        # Validate pagination parameters
        if limit < 1 or limit > 1000:
            limit = 100
        if offset < 0:
            offset = 0

        logger.info(
            f'[API] Searching projects - region:{region}, country:{country}, brand:{brand}, website:{website}, group:{group_by_website}')

        # Get projects with filters and website grouping
        result = g.db.get_projects_with_website_grouping(
            limit=limit,
            offset=offset,
            region=region,
            country=country,
            brand=brand,
            website=website
        )

        if group_by_website:
            return jsonify({
                'success': result.get('success', False),
                'projects': result.get('by_website', []),
                'total': result.get('total', 0),
                'grouped_by': 'website',
                'limit': limit,
                'offset': offset
            }), 200 if result.get('success') else 500
        else:
            return jsonify({
                'success': result.get('success', False),
                'projects': result.get('by_project', []),
                'total': result.get('total', 0),
                'grouped_by': 'none',
                'limit': limit,
                'offset': offset
            }), 200 if result.get('success') else 500

    except ValueError as e:
        logger.error(f'[API] Invalid parameters: {str(e)}')
        return jsonify({'error': 'Invalid parameters', 'details': str(e)}), 400
    except Exception as e:
        logger.error(f'[API] Error searching projects: {str(e)}')
        return jsonify({'error': 'Failed to search projects', 'details': str(e)}), 500


@app.route('/api/filters', methods=['GET'])
def get_filters():
    """
    Get all available filter options
    Returns regions, countries, brands, and websites
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        logger.info('[API] Getting filter options...')

        filters = {
            'regions': g.db.get_distinct_metadata_values('region'),
            'countries': g.db.get_distinct_metadata_values('country'),
            'brands': g.db.get_distinct_metadata_values('brand'),
            'websites': g.db.get_distinct_project_websites()
        }

        logger.info(
            f'[API] Filters - Regions: {len(filters["regions"])}, Countries: {len(filters["countries"])}, Brands: {len(filters["brands"])}, Websites: {len(filters["websites"])}')

        return jsonify({
            'success': True,
            'filters': filters
        }), 200

    except Exception as e:
        logger.error(f'[API] Error getting filters: {str(e)}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/projects/<token>', methods=['GET'])
def get_project_details(token: str):
    """
    Get detailed information about a specific project
    Includes project data, associated metadata, and run statistics
    """
    try:
        logger.info(f'[API] Fetching project details: token={token}')

        # Get project from database
        project = g.db.get_project_by_token(token)

        if not project:
            logger.warning(f'[API] Project not found: {token}')
            return jsonify({'error': 'Project not found', 'success': False}), 404

        # Get metadata for this project
        metadata = g.db.get_metadata_by_project_token(token)

        # Get run statistics for this project
        run_stats = None
        if project.get('id'):
            run_stats = g.db.get_project_run_stats(project['id'])

        response_data = {
            'success': True,
            'data': {
                'id': project.get('id'),
                'token': project.get('token'),
                'title': project.get('title'),
                'owner_email': project.get('owner_email'),
                'main_site': project.get('main_site'),
                'created_at': project.get('created_at'),
                'updated_at': project.get('updated_at'),
                'last_run': project.get('last_run'),
                'metadata': metadata,
                'run_stats': run_stats
            }
        }

        logger.info(f'[API] ✅ Project details retrieved: {token}')
        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f'[API] Error getting project details: {str(e)}')
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/api/projects/<token>/run', methods=['POST'])
def run_project(token: str):
    """
    Run a specific project on ParseHub

    NOTE: Will auto-stop when pages_scraped reaches metadata.total_pages

    Request body:
    {
        "pages": 1 (optional, default: 1)
    }

    Returns the run data from ParseHub
    """
    try:
        logger.info(f'[API] Attempting to run project: token={token}')

        # Get the API key
        api_key = request.args.get('api_key') or os.getenv('PARSEHUB_API_KEY')

        if not api_key:
            logger.error('[API] Missing API key for project run')
            return jsonify({'error': 'Missing API key'}), 401

        # Get request parameters
        data = request.get_json() or {}
        pages = max(1, int(data.get('pages', 1)))

        # CHECK: Get metadata to see if we're already at or near total pages
        metadata = g.db.get_metadata_by_project_token(token)

        if metadata:
            total_pages = metadata.get('total_pages')
            pages_scraped = metadata.get('current_page_scraped', 0)

            if total_pages and pages_scraped >= total_pages:
                logger.warning(
                    f'[API] ⚠️ Project {token} already reached target ({pages_scraped}/{total_pages}). Skipping run.')
                return jsonify({
                    'success': False,
                    'error': f'Project already scraped all {total_pages} pages ({pages_scraped}/{total_pages})',
                    'metadata': {
                        'total_pages': total_pages,
                        'pages_scraped': pages_scraped,
                        'status': 'COMPLETE'
                    }
                }), 400

        # Call ParseHub API to run the project
        parsehub_url = f'https://www.parsehub.com/api/v2/projects/{token}/run'

        run_data = {
            'api_key': api_key,
            'pages': pages
        }

        logger.info(
            f'[API] Calling ParseHub API: {parsehub_url} with pages={pages}')

        response = requests.post(parsehub_url, data=run_data, timeout=10)

        if response.status_code != 200:
            error_msg = f'ParseHub API error: {response.status_code} - {response.text}'
            logger.error(f'[API] ❌ {error_msg}')
            return jsonify({'error': error_msg, 'success': False}), response.status_code

        run_info = response.json()
        run_token = run_info.get('run_token')

        logger.info(
            f'[API] ✅ Project run started: {token}, Run token: {run_token}')

        # Schedule auto-stop check in background
        if metadata:
            logger.info(
                f'[API] Will auto-stop when pages_scraped reaches {metadata.get("total_pages")}')

        return jsonify({
            'success': True,
            'run_token': run_token,
            'status': run_info.get('status'),
            'pages': run_info.get('pages'),
            'message': f'Project {token} started successfully',
            'auto_stop': {
                'enabled': bool(metadata and metadata.get('total_pages')),
                'target_pages': metadata.get('total_pages') if metadata else None
            }
        }), 200

    except requests.exceptions.Timeout:
        logger.error(f'[API] Timeout calling ParseHub API for project {token}')
        return jsonify({'error': 'ParseHub API timeout', 'success': False}), 504
    except Exception as e:
        logger.error(f'[API] Error running project {token}: {str(e)}')
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/api/projects/<token>/analytics', methods=['GET'])
def get_project_analytics(token: str):
    """
    Get comprehensive analytics for a specific project

    Returns:
    - overview: total runs, records, pages, progress
    - performance: scraping rate, estimated completion
    - recovery: recovery status and operations
    - runs_history: last 10 runs
    - data_quality: quality metrics
    - timeline: events timeline
    """
    try:
        logger.info(f'[API] Fetching analytics for project: {token}')

        analytics = analytics_service.get_project_analytics(token)

        if analytics is None:
            logger.warning(
                f'[API] Analytics returned None for project {token}')
            analytics = {
                'project_token': token,
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
                    'status': 'no_data',
                    'total_recovery_attempts': 0
                },
                'runs_history': [],
                'data_quality': {
                    'average_completion_percentage': 0,
                    'total_fields': 0
                },
                'timeline': []
            }

        logger.info(f'[API] ✅ Analytics retrieved for project {token}')
        return jsonify(analytics), 200

    except Exception as e:
        logger.error(
            f'[API] Error fetching analytics for project {token}: {str(e)}', exc_info=True)
        return jsonify({
            'error': str(e),
            'project_token': token,
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
                'status': 'error',
                'total_recovery_attempts': 0
            },
            'runs_history': [],
            'data_quality': {
                'average_completion_percentage': 0,
                'total_fields': 0
            },
            'timeline': []
        }), 200


# ========== DATA INGESTION & PRODUCT DATA ==========

@app.route('/api/ingest/<project_token>', methods=['POST'])
def ingest_project_data(project_token: str):
    """
    Trigger data ingestion for a project's completed runs
    Fetches data from ParseHub and stores in database

    Query parameters:
    - days_back: How many days back to look for runs (default: 30)
    """
    try:
        from data_ingestion_service import ParseHubDataIngestor
    except ImportError:
        from data_ingestion_service import ParseHubDataIngestor

    try:
        logger.info(
            f'[API] Starting data ingestion for project: {project_token}')

        # Get days_back parameter
        days_back = request.args.get('days_back', 30, type=int)

        # Get project ID from database
        db = ParseHubDatabase()
        project_id = g.db.get_project_id_by_token(project_token)

        if not project_id:
            logger.error(f'[API] Project not found: {project_token}')
            return jsonify({'error': 'Project not found'}), 404

        # Ingest data
        ingestor = ParseHubDataIngestor()
        result = ingestor.ingest_project_runs(
            project_id, project_token, days_back)

        logger.info(f'[API] ✅ Data ingestion complete: {result}')

        # Get stats
        stats = g.db.get_product_data_stats(project_id)

        return jsonify({
            'success': True,
            'ingestion_result': result,
            'statistics': stats
        }), 200

    except Exception as e:
        logger.error(f'[API] Error ingesting data: {e}')
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/api/products/<int:project_id>', methods=['GET'])
def get_product_data(project_id: int):
    """
    Get product data for a project

    Query parameters:
    - limit: Number of records to return (default: 100, max: 1000)
    - offset: Pagination offset (default: 0)
    """
    try:
        limit = min(int(request.args.get('limit', 100)), 1000)
        offset = int(request.args.get('offset', 0))

        db = ParseHubDatabase()
        products = g.db.get_product_data_by_project(
            project_id, limit=limit, offset=offset)

        return jsonify({
            'success': True,
            'count': len(products),
            'limit': limit,
            'offset': offset,
            'data': products
        }), 200

    except Exception as e:
        logger.error(f'[API] Error fetching product data: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/products/run/<run_token>', methods=['GET'])
def get_product_data_by_run(run_token: str):
    """Get product data for a specific run"""
    try:
        limit = min(int(request.args.get('limit', 1000)), 5000)

        db = ParseHubDatabase()
        products = g.db.get_product_data_by_run(run_token, limit=limit)

        return jsonify({
            'success': True,
            'count': len(products),
            'run_token': run_token,
            'data': products
        }), 200

    except Exception as e:
        logger.error(f'[API] Error fetching product data: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/products/<int:project_id>/stats', methods=['GET'])
def get_product_stats(project_id: int):
    """Get statistics about product data for a project"""
    try:
        db = ParseHubDatabase()
        stats = g.db.get_product_data_stats(project_id)

        return jsonify({
            'success': True,
            'project_id': project_id,
            'statistics': stats
        }), 200

    except Exception as e:
        logger.error(f'[API] Error fetching product stats: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/products/<int:project_id>/export', methods=['GET'])
def export_product_data(project_id: int):
    """Export product data to CSV file"""
    try:
        from flask import send_file

        db = ParseHubDatabase()

        # Generate export file
        output_path = f"product_export_project_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        export_file = g.db.export_product_data_csv(project_id, output_path)

        if not export_file:
            return jsonify({'error': 'No data to export'}), 404

        logger.info(f'[API] Exporting product data: {export_file}')

        return send_file(export_file, as_attachment=True, download_name=export_file)

    except Exception as e:
        logger.error(f'[API] Error exporting product data: {e}')
        return jsonify({'error': str(e)}), 500

# ========== INCREMENTAL SCRAPING ENDPOINTS ==========


@app.route('/api/scraping/check-and-continue', methods=['POST'])
def check_and_continue_scraping():
    """
    Check all projects and automatically continue scraping incomplete ones
    Matches project_id from projects table with metadata
    If scraped pages < total pages, automatically triggers continuation run
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from incremental_scraping_manager import IncrementalScrapingManager

        manager = IncrementalScrapingManager()
        continuation_runs = manager.check_and_match_pages()

        return jsonify({
            'status': 'success',
            'message': f'Scheduled {len(continuation_runs)} continuation runs',
            'continuation_runs': continuation_runs
        }), 200

    except ImportError:
        try:
            from incremental_scraping_manager import IncrementalScrapingManager

            manager = IncrementalScrapingManager()
            continuation_runs = manager.check_and_match_pages()

            return jsonify({
                'status': 'success',
                'message': f'Scheduled {len(continuation_runs)} continuation runs',
                'continuation_runs': continuation_runs
            }), 200
        except Exception as e:
            logger.error(f'Error in check_and_continue_scraping: {e}')
            return jsonify({'error': str(e)}), 500
    except Exception as e:
        logger.error(f'Error in check_and_continue_scraping: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraping/monitor-continuations', methods=['GET'])
def monitor_continuation_runs():
    """
    Monitor running continuation runs and update their status
    """
    if not validate_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from incremental_scraping_manager import IncrementalScrapingManager

        manager = IncrementalScrapingManager()
        manager.monitor_continuation_runs()

        return jsonify({
            'status': 'success',
            'message': 'Monitored continuation runs'
        }), 200

    except ImportError:
        try:
            from incremental_scraping_manager import IncrementalScrapingManager

            manager = IncrementalScrapingManager()
            manager.monitor_continuation_runs()

            return jsonify({
                'status': 'success',
                'message': 'Monitored continuation runs'
            }), 200
        except Exception as e:
            logger.error(f'Error in monitor_continuation_runs: {e}')
            return jsonify({'error': str(e)}), 500
    except Exception as e:
        logger.error(f'Error in monitor_continuation_runs: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraping/project/<int:project_id>/status', methods=['GET'])
def get_scraping_status(project_id: int):
    """
    Get the scraping status of a specific project
    Shows total pages vs pages scraped
    """
    try:
        conn = g.db.connect()
        cursor = conn.cursor()

        # Get metadata for project
        cursor.execute('''
            SELECT m.total_pages, m.current_page_scraped, m.project_name, p.token
            FROM metadata m
            JOIN projects p ON m.project_id = p.id
            WHERE p.id = ?
        ''', (project_id,))

        result = cursor.fetchone()
        conn.close()

        if not result:
            return jsonify({
                'error': 'Project not found in metadata'
            }), 404

        total_pages, current_page_scraped, project_name, token = result

        return jsonify({
            'status': 'success',
            'project_id': project_id,
            'project_name': project_name,
            'project_token': token,
            'total_pages': total_pages,
            'pages_scraped': current_page_scraped,
            'pages_remaining': max(0, total_pages - current_page_scraped),
            'completion_percentage': (current_page_scraped / total_pages * 100) if total_pages > 0 else 0,
            'is_complete': current_page_scraped >= total_pages
        }), 200

    except Exception as e:
        logger.error(f'Error getting scraping status: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/sync/trigger', methods=['POST'])
def trigger_manual_sync():
    """
    Manually trigger a sync of all ParseHub data to database
    """
    try:
        service = get_auto_sync_service()

        if service is None:
            return jsonify({
                'status': 'error',
                'message': 'Auto-sync service not running'
            }), 503

        # Trigger manual sync
        results = service.manual_sync()

        return jsonify({
            'status': 'success',
            'message': 'Manual sync completed',
            'results': results
        }), 200

    except Exception as e:
        logger.error(f'Error triggering manual sync: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/sync/status', methods=['GET'])
def get_sync_status():
    """
    Get status of auto-sync service
    """
    try:
        service = get_auto_sync_service()

        if service is None:
            return jsonify({
                'status': 'stopped',
                'running': False
            }), 200

        return jsonify({
            'status': 'running',
            'running': service.running,
            'sync_interval_minutes': service.sync_interval
        }), 200

    except Exception as e:
        logger.error(f'Error getting sync status: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/scraping/projects/incomplete', methods=['GET'])
def get_incomplete_projects():
    """
    Get list of all projects that have incomplete scraping
    """
    try:
        conn = g.db.connect()
        cursor = conn.cursor()

        # Get projects with incomplete scraping
        cursor.execute('''
            SELECT p.id, p.token, m.project_name, m.total_pages, 
                   m.current_page_scraped, (m.total_pages - m.current_page_scraped) as remaining
            FROM metadata m
            JOIN projects p ON m.project_id = p.id
            WHERE m.total_pages > 0 AND m.current_page_scraped < m.total_pages
            ORDER BY remaining DESC
        ''')

        projects = cursor.fetchall()
        conn.close()

        incomplete_projects = []
        for project_id, token, name, total_pages, pages_scraped, remaining in projects:
            incomplete_projects.append({
                'project_id': project_id,
                'project_token': token,
                'project_name': name,
                'total_pages': total_pages,
                'pages_scraped': pages_scraped,
                'pages_remaining': remaining,
                'completion_percentage': (pages_scraped / total_pages * 100) if total_pages > 0 else 0
            })

        return jsonify({
            'status': 'success',
            'incomplete_count': len(incomplete_projects),
            'projects': incomplete_projects
        }), 200

    except Exception as e:
        logger.error(f'Error getting incomplete projects: {e}')
        return jsonify({'error': str(e)}), 500

# (Health endpoints moved to _health_bp Blueprint above, around line 122)

# ========== ERROR HANDLERS ==========

def _rid() -> str:
    """Return the current request_id (set by before_request), or a fallback."""
    return getattr(g, 'request_id', uuid.uuid4().hex[:12])


@app.errorhandler(404)
def not_found(error):
    rid = _rid()
    logger.warning(f'[{rid}] 404 {request.path}')
    return jsonify({'error': 'Endpoint not found', 'request_id': rid}), 404


@app.errorhandler(500)
def internal_error(error):
    rid = _rid()
    logger.error(f'[{rid}] 500 Internal server error: {error}')
    return jsonify({'error': 'Internal server error', 'request_id': rid}), 500


@app.errorhandler(503)
def service_unavailable(error):
    rid = _rid()
    return jsonify({
        'error': 'Service temporarily unavailable',
        'detail': str(error),
        'request_id': rid,
    }), 503


@app.errorhandler(Exception)
def unhandled_exception(exc):
    """Catch-all: any exception that escapes a route handler returns JSON.
    This prevents Gunicorn from returning a raw 502 to the Next.js proxy.
    DB errors (OperationalError, InterfaceError, pool timeout) all land here.
    """
    rid      = _rid()
    err_type = type(exc).__name__
    err_msg  = str(exc)
    logger.error(f'[{rid}] [unhandled] {err_type}: {err_msg}', exc_info=True)

    # DB-related exception types → 503 (service temporarily unavailable)
    db_error_keywords = (
        'OperationalError', 'InterfaceError', 'DatabaseError',
        'pool', 'connect', 'timeout', 'too many clients', 'FATAL',
    )
    is_db_error = err_type in db_error_keywords or any(
        kw.lower() in err_msg.lower() for kw in db_error_keywords
    )

    status = 503 if is_db_error else 500
    return jsonify({
        'error': 'Database temporarily unavailable. Please retry.' if is_db_error
                 else 'Internal server error.',
        'type': err_type,
        'request_id': rid,
    }), status


if __name__ == '__main__':
    from datetime import datetime

    # In production (Railway), use PORT. Local fallback to BACKEND_PORT or 5000.
    port = int(os.getenv('PORT', os.getenv('BACKEND_PORT', 5000)))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

    logger.info(f'Starting ParseHub API Server on port {port}')

    try:
        app.run(host='0.0.0.0', port=port, debug=debug)
    finally:
        # Stop services on shutdown
        logger.info('Shutting down services...')
        stop_incremental_scraping_scheduler()
        stop_auto_sync_service()
        logger.info('Services stopped')

