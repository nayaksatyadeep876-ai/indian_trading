import os
import sqlite3
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, Union, Tuple, List
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g, send_file, abort, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import numpy as np
import yfinance as yf
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
import json
import requests
import time
import threading
import math
from statistics import NormalDist
from config import *
from dotenv import load_dotenv
from trading_system import TradingSystem
from risk_manager import RiskManager
from auto_trader import AutoTrader
from indian_trading_system import IndianTradingSystem, IndianAutoTrader
from portfolio_analytics import PortfolioAnalytics
from angel_connection import angel_one_client, initialize_smartapi
from data_injection_service import get_data_injection_service
from security_manager import security_manager, require_auth, require_permission, rate_limit
from notification_system import notification_system, NotificationType, NotificationPriority
from backup_system import backup_system
from datetime import date
from datetime import datetime as dt

# Load environment variables explicitly from project root

try:
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except Exception:
    pass

# Get absolute paths for PythonAnywhere
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
TEMPLATES_FOLDER = os.path.join(BASE_DIR, 'templates')

# Configure Flask app for PythonAnywhere
app = Flask(__name__,
    static_url_path='/static',
    static_folder=STATIC_FOLDER,
    template_folder=TEMPLATES_FOLDER
)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # Use environment variable with fallback
CORS(app)

# Disable caching to always serve the latest templates and static assets
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True
try:
    # Clear Jinja template cache
    app.jinja_env.cache = {}
except Exception:
    pass

# Append file modification time to static asset URLs for cache-busting
@app.context_processor
def override_static_url():
    def static_url(filename: str):
        file_path = os.path.join(STATIC_FOLDER, filename)
        version = int(os.path.getmtime(file_path)) if os.path.exists(file_path) else int(time.time())
        return url_for('static', filename=filename, v=version)
    return dict(static_url=static_url)

# Prevent browsers/proxies from caching HTML responses
@app.after_request
def add_no_cache_headers(response):
    content_type = response.headers.get('Content-Type', '')
    if 'text/html' in content_type:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# Configure SocketIO for PythonAnywhere
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    logger=True,
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25,
    always_connect=True,
    reconnection=True,
    reconnection_attempts=10,
    reconnection_delay=1000,
    reconnection_delay_max=5000,
    manage_session=True  # Enable session management
)

# Configure logging for PythonAnywhere
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'trading_app.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Persistence helpers for autostart/active trades ---
def ensure_persistence_tables():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS active_trades (
                id TEXT PRIMARY KEY,
                symbol TEXT,
                type TEXT,
                entry_price REAL,
                quantity INTEGER,
                entry_time TEXT,
                user_id INTEGER,
                strategy TEXT,
                confidence REAL
            )
        ''')
        conn.commit()
    except Exception as e:
        logger.error(f"Error ensuring persistence tables: {e}")

def set_setting(key: str, value: str):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('INSERT INTO app_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
        conn.commit()
    except Exception as e:
        logger.error(f"Error setting app setting {key}: {e}")

def get_setting(key: str, default: str = None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT value FROM app_settings WHERE key=?', (key,))
        row = cur.fetchone()
        return row['value'] if row else default
    except Exception as e:
        logger.error(f"Error getting app setting {key}: {e}")
        return default
# --- Portfolio/P&L endpoints ---
@app.route('/portfolio/summary')
def portfolio_summary():
    if not session.get('user_id'):
        return jsonify({"error": "Not authenticated"}), 401
    try:
        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT portfolio_value, timestamp FROM portfolio_history
            WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1
        ''', (user_id,))
        row = cursor.fetchone()
        latest_value = float(row['portfolio_value']) if row else float(5000.0)

        # Compute today's P&L from midnight
        cursor.execute('''
            SELECT SUM(profit_loss) AS pnl_sum FROM trades
            WHERE user_id = ? AND DATE(exit_time) = DATE('now', 'localtime')
        ''', (user_id,))
        pnl_row = cursor.fetchone()
        today_pnl = float(pnl_row['pnl_sum']) if pnl_row and pnl_row['pnl_sum'] is not None else 0.0

        return jsonify({
            'portfolio_value': round(latest_value, 2),
            'today_pnl': round(today_pnl, 2)
        })
    except Exception as e:
        logger.error(f"Error fetching portfolio summary: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/portfolio/pnl_by_date')
def portfolio_pnl_by_date():
    if not session.get('user_id'):
        return jsonify({"error": "Not authenticated"}), 401
    try:
        user_id = session['user_id']
        start_date = request.args.get('start')  # YYYY-MM-DD or MM/DD/YYYY
        end_date = request.args.get('end')      # YYYY-MM-DD or MM/DD/YYYY
        if not start_date:
            start_date = date.today().strftime('%Y-%m-%d')
        if not end_date:
            end_date = start_date

        # Normalize MM/DD/YYYY to YYYY-MM-DD if needed
        def norm(d):
            if not d:
                return d
            try:
                if '/' in d:
                    return dt.strptime(d, '%m/%d/%Y').strftime('%Y-%m-%d')
            except Exception:
                pass
            return d
        start_date = norm(start_date)
        end_date = norm(end_date)

        conn = get_db()
        cursor = conn.cursor()
        # Handle rows where exit_time may have been stored as time-only (HH:MM:SS)
        # Interpret time-only as 'today' for the purpose of date filtering
        cursor.execute('''
            SELECT d, SUM(profit_loss) as pnl FROM (
                SELECT 
                    CASE 
                        WHEN LENGTH(exit_time) = 8 AND INSTR(exit_time, ':') > 0 THEN DATE('now','localtime')
                        ELSE DATE(exit_time)
                    END AS d,
                    profit_loss
                FROM trades
                WHERE user_id = ?
            ) t
            WHERE d BETWEEN DATE(?) AND DATE(?)
            GROUP BY d
            ORDER BY d ASC
        ''', (user_id, start_date, end_date))
        rows = cursor.fetchall()
        data = [{'date': r['d'], 'pnl': float(r['pnl']) if r['pnl'] is not None else 0.0} for r in rows]
        total = sum(item['pnl'] for item in data)
        # Breakdown: profit, loss, wins, losses, portfolio balance
        cursor.execute('''
            SELECT 
                SUM(CASE WHEN profit_loss > 0 THEN profit_loss ELSE 0 END) AS total_profit,
                SUM(CASE WHEN profit_loss < 0 THEN -profit_loss ELSE 0 END) AS total_loss,
                SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END) AS losses
            FROM (
                SELECT 
                    CASE 
                        WHEN LENGTH(exit_time) = 8 AND INSTR(exit_time, ':') > 0 THEN DATE('now','localtime')
                        ELSE DATE(exit_time)
                    END AS d,
                    profit_loss
                FROM trades
                WHERE user_id = ?
            ) x
            WHERE d BETWEEN DATE(?) AND DATE(?)
        ''', (user_id, start_date, end_date))
        br = cursor.fetchone() or {}
        total_profit = float(br['total_profit'] or 0.0)
        total_loss = float(br['total_loss'] or 0.0)
        wins = int(br['wins'] or 0)
        losses = int(br['losses'] or 0)
        denominator = total_profit + total_loss
        profit_pct = (total_profit / denominator * 100.0) if denominator > 0 else 0.0
        loss_pct = (total_loss / denominator * 100.0) if denominator > 0 else 0.0
        win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
        # Latest portfolio value
        cursor.execute('''
            SELECT portfolio_value FROM portfolio_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1
        ''', (user_id,))
        prow = cursor.fetchone()
        portfolio_value = float(prow['portfolio_value']) if prow and prow['portfolio_value'] is not None else float(5000.0)

        return jsonify({
            'range_total': round(total, 2),
            'by_date': data,
            'total_profit': round(total_profit, 2),
            'total_loss': round(total_loss, 2),
            'profit_pct': round(profit_pct, 2),
            'loss_pct': round(loss_pct, 2),
            'win_rate': round(win_rate, 2),
            'portfolio_value': round(portfolio_value, 2)
        })
    except Exception as e:
        logger.error(f"Error fetching P&L by date: {e}")
        return jsonify({'error': str(e)}), 500

# AJAX endpoint for filtered trades
@app.route('/api/trades/filtered')
def get_filtered_trades():
    if not session.get('user_id'):
        return jsonify({"error": "Not authenticated"}), 401
    
    try:
        user_id = session['user_id']
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Build query with date filtering
        query = '''
            SELECT symbol, direction, entry_price, exit_price, quantity, status, entry_time, exit_time, profit_loss
            FROM trades 
            WHERE user_id = ?
        '''
        params = [user_id]
        
        if start_date:
            query += ' AND DATE(entry_time) >= ?'
            params.append(start_date)
        
        if end_date:
            query += ' AND DATE(entry_time) <= ?'
            params.append(end_date)
        
        query += ' ORDER BY entry_time DESC LIMIT 50'
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to list of dictionaries
        trades = []
        if rows:
            columns = ['symbol', 'direction', 'entry_price', 'exit_price', 'quantity', 'status', 'entry_time', 'exit_time', 'profit_loss']
            trades = [dict(zip(columns, row)) for row in rows]
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'trades': trades,
            'count': len(trades),
            'start_date': start_date,
            'end_date': end_date
        })
        
    except Exception as e:
        logger.error(f"Error fetching filtered trades: {e}")
        return jsonify({'error': str(e)}), 500

# Closed trades for Indian dashboard
@app.route('/indian_closed_trades')
def indian_closed_trades():
    if not session.get('user_id'):
        return jsonify({"error": "Not authenticated"}), 401
    try:
        user_id = session['user_id']
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT symbol, direction, entry_price, exit_price, quantity, status, entry_time, exit_time, profit_loss
            FROM trades
            WHERE user_id = ? AND status = 'CLOSED'
            ORDER BY datetime(exit_time) DESC
            LIMIT 50
        ''', (user_id,))
        rows = cursor.fetchall()
        # sqlite3.Row -> dict
        results = []
        if rows:
            cols = [d[0] for d in cursor.description]
            for r in rows:
                item = {k: r[idx] for idx, k in enumerate(cols)}
                results.append(item)
        return jsonify({'trades': results, 'count': len(results)})
    except Exception as e:
        logger.error(f"Error fetching indian closed trades: {e}")
        return jsonify({'error': str(e)}), 500

# Seed dummy trades for demo/visual checks
@app.route('/seed_dummy_trades')
def seed_dummy_trades():
    if not session.get('user_id'):
        return jsonify({"error": "Not authenticated"}), 401
    try:
        user_id = session['user_id']
        connection = get_db()
        if connection is None:
            return jsonify({"error": "Database connection failed"}), 500
        cursor = connection.cursor()

        # Simple helper to insert a closed trade
        def insert_trade(symbol, direction, entry_price, exit_price, qty, status, entry_dt, exit_dt, pnl):
            # Skip if an identical closed trade already exists (same symbol, direction, entry/exit times and P&L)
            cursor.execute('''
                SELECT 1 FROM trades WHERE user_id = ? AND symbol = ? AND direction = ?
                AND entry_time = ? AND exit_time = ? AND profit_loss = ? AND status = 'CLOSED' LIMIT 1
            ''', (user_id, symbol, direction, entry_dt, exit_dt, float(pnl)))
            if cursor.fetchone():
                return
            cursor.execute('''
                INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, quantity, status, entry_time, exit_time, profit_loss)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, symbol, direction, float(entry_price), float(exit_price), float(qty), status, entry_dt, exit_dt, float(pnl)))

        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')

        # Example dummy trades
        insert_trade('SENSEX', 'SELL', 65000.0, 64850.0, 1, 'CLOSED', f'{today} 09:45:00', f'{today} 10:30:00', 1500.00)
        insert_trade('HINDUNILVR', 'SELL', 2500.0, 2525.0, 1, 'CLOSED', f'{today} 10:30:00', f'{today} 11:45:00', -625.00)
        insert_trade('BANKNIFTY', 'BUY', 44500.0, 44620.0, 1, 'CLOSED', f'{yesterday} 10:00:00', f'{yesterday} 11:10:00', 1200.00)

        # Update portfolio_history snapshot based on last value
        cursor.execute('''
            SELECT portfolio_value FROM portfolio_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1
        ''', (user_id,))
        row = cursor.fetchone()
        last_val = float(row['portfolio_value']) if row else float(5000.0)
        delta = 1500.00 - 625.00 + 1200.00
        new_val = last_val + delta
        cursor.execute('''
            INSERT INTO portfolio_history (user_id, portfolio_value, timestamp) VALUES (?, ?, ?)
        ''', (user_id, new_val, now.strftime('%Y-%m-%d %H:%M:%S')))

        connection.commit()
        return jsonify({'status': 'ok', 'inserted_trades': 3, 'new_portfolio_value': round(new_val, 2)})
    except Exception as e:
        try:
            connection.rollback()
        except Exception:
            pass
        logger.error(f"Error seeding dummy trades: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/create_default_user')
def create_default_user():
    """Create a default user for testing if none exists"""
    try:
        connection = get_db()
        if connection is None:
            return jsonify({"error": "Database connection failed"}), 500
        
        cursor = connection.cursor()
        
        # Check if any user exists
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        if user_count == 0:
            # Create default user
            default_password = generate_password_hash("admin123")
            cursor.execute("""
                INSERT INTO users (username, password, registered_at, balance, is_premium, demo_end_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                "admin",
                default_password,
                datetime.now().isoformat(),
                100000.00,  # Starting balance
                1,  # Premium user
                (datetime.now() + timedelta(days=30)).isoformat()  # 30 days demo
            ))
            
            # Create initial portfolio snapshot
            cursor.execute("""
                INSERT INTO portfolio_history (user_id, portfolio_value, timestamp)
                VALUES (?, ?, ?)
            """, (1, 100000.00, datetime.now().isoformat()))
            
            connection.commit()
            cursor.close()
            
            return jsonify({
                'status': 'success',
                'message': 'Default user created successfully',
                'username': 'admin',
                'password': 'admin123'
            })
        else:
            return jsonify({
                'status': 'info',
                'message': f'Users already exist ({user_count} users)'
            })
            
    except Exception as e:
        logger.error(f"Error creating default user: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/db_status')
def check_database_status():
    """Check database status and table information"""
    try:
        connection = get_db()
        if connection is None:
            return jsonify({"error": "Database connection failed"}), 500
        
        cursor = connection.cursor()
        
        # Get table information
        tables = ['users', 'trades', 'positions', 'portfolio_history', 'signals']
        table_info = {}
        
        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                table_info[table] = count
            except Exception as e:
                table_info[table] = f"Error: {str(e)}"
        
        cursor.close()
        
        return jsonify({
            'status': 'success',
            'database': 'trading.db',
            'tables': table_info,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error checking database status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/reset_database')
def reset_database():
    """Reset database completely and recreate with sample data"""
    try:
        import os
        
        # Close any existing connections
        if hasattr(g, 'db'):
            g.db.close()
            delattr(g, 'db')
        
        # Delete the database file
        db_path = os.path.join(BASE_DIR, 'trading.db')
        if os.path.exists(db_path):
            os.remove(db_path)
            logger.info("Database file deleted")
        
        # Reinitialize database
        init_db()
        
        return jsonify({
            'status': 'success',
            'message': 'Database reset successfully',
            'default_user': 'admin',
            'default_password': 'admin123'
        })
        
    except Exception as e:
        logger.error(f"Error resetting database: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/cache-stats', methods=['GET'])
def get_cache_stats():
    """Get cache statistics"""
    try:
        from cache_manager import cache_manager
        stats = cache_manager.get_cache_stats()
        return jsonify({
            'status': 'success',
            'cache_stats': stats
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/clear-cache', methods=['POST'])
def clear_cache():
    """Clear all cache entries"""
    try:
        from cache_manager import cache_manager
        result = cache_manager.clear_all()
        return jsonify({
            'status': 'success',
            'message': 'Cache cleared successfully' if result else 'Failed to clear cache'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/cleanup-cache', methods=['POST'])
def cleanup_cache():
    """Clean up expired cache entries"""
    try:
        from cache_manager import cache_manager
        removed_count = cache_manager.cleanup_expired()
        return jsonify({
            'status': 'success',
            'message': f'Cleaned up {removed_count} expired cache entries'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/rate-limit-stats', methods=['GET'])
def get_rate_limit_stats():
    """Get rate limiting statistics"""
    try:
        from rate_limiter import rate_limiter
        stats = rate_limiter.get_stats()
        return jsonify({
            'status': 'success',
            'rate_limit_stats': stats
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/reset-rate-limits', methods=['POST'])
def reset_rate_limits():
    """Reset rate limiting for all services"""
    try:
        from rate_limiter import rate_limiter
        rate_limiter.reset()
        return jsonify({
            'status': 'success',
            'message': 'Rate limits reset successfully'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/performance-metrics', methods=['GET'])
def get_performance_metrics():
    """Get current performance metrics"""
    try:
        from performance_monitor import performance_monitor
        metrics = performance_monitor.get_current_metrics()
        return jsonify({
            'status': 'success',
            'performance_metrics': metrics
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/performance-history', methods=['GET'])
def get_performance_history():
    """Get performance metrics history"""
    try:
        from performance_monitor import performance_monitor
        hours = request.args.get('hours', 24, type=int)
        history = performance_monitor.get_metrics_history(hours)
        return jsonify({
            'status': 'success',
            'performance_history': history
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/performance-summary', methods=['GET'])
def get_performance_summary():
    """Get performance summary"""
    try:
        from performance_monitor import performance_monitor
        summary = performance_monitor.get_performance_summary()
        return jsonify({
            'status': 'success',
            'performance_summary': summary
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/start-performance-monitoring', methods=['POST'])
def start_performance_monitoring():
    """Start performance monitoring"""
    try:
        from performance_monitor import performance_monitor
        interval = request.json.get('interval', 30) if request.json else 30
        performance_monitor.start_monitoring(interval)
        return jsonify({
            'status': 'success',
            'message': f'Performance monitoring started with {interval}s interval'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/stop-performance-monitoring', methods=['POST'])
def stop_performance_monitoring():
    """Stop performance monitoring"""
    try:
        from performance_monitor import performance_monitor
        performance_monitor.stop_monitoring()
        return jsonify({
            'status': 'success',
            'message': 'Performance monitoring stopped'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/portfolio/analytics', methods=['GET'])
def get_portfolio_analytics():
    """Get comprehensive portfolio analytics"""
    try:
        user_id = request.args.get('user_id', 3, type=int)
        days = request.args.get('days', 30, type=int)
        
        # Get portfolio summary
        summary = portfolio_analytics.get_portfolio_summary(user_id)
        
        # Get performance analysis
        performance = portfolio_analytics.get_performance_analysis(user_id, days)
        
        # Get risk analysis
        risk_analysis = portfolio_analytics.get_risk_analysis(user_id)
        
        # Get trade analytics
        trade_analytics = portfolio_analytics.get_trade_analytics(user_id, days)
        
        # Get sector analysis
        sector_analysis = portfolio_analytics.get_sector_analysis(user_id)
        
        return jsonify({
            'summary': summary,
            'performance': performance,
            'risk_analysis': risk_analysis,
            'trade_analytics': trade_analytics,
            'sector_analysis': sector_analysis,
            'generated_at': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/portfolio/performance', methods=['GET'])
def get_portfolio_performance():
    """Get detailed performance analysis"""
    try:
        user_id = request.args.get('user_id', 3, type=int)
        days = request.args.get('days', 30, type=int)
        
        performance = portfolio_analytics.get_performance_analysis(user_id, days)
        
        return jsonify(performance)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/portfolio/risk', methods=['GET'])
def get_portfolio_risk():
    """Get risk analysis"""
    try:
        user_id = request.args.get('user_id', 3, type=int)
        
        risk_analysis = portfolio_analytics.get_risk_analysis(user_id)
        
        return jsonify(risk_analysis)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/portfolio/trades', methods=['GET'])
def get_trade_analytics():
    """Get trade analytics"""
    try:
        user_id = request.args.get('user_id', 3, type=int)
        days = request.args.get('days', 30, type=int)
        
        trade_analytics = portfolio_analytics.get_trade_analytics(user_id, days)
        
        return jsonify(trade_analytics)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/portfolio/sectors', methods=['GET'])
def get_sector_analysis():
    """Get sector analysis"""
    try:
        user_id = request.args.get('user_id', 3, type=int)
        
        sector_analysis = portfolio_analytics.get_sector_analysis(user_id)
        
        return jsonify(sector_analysis)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/angel_one/configure', methods=['POST'])
def configure_angel_one():
    """Configure Angel One API credentials"""
    try:
        data = request.get_json()
        
        api_key = data.get('api_key')
        client_id = data.get('client_id')
        password = data.get('password')
        totp_secret = data.get('totp_secret')
        
        if not all([api_key, client_id, password, totp_secret]):
            return jsonify({'error': 'Missing required credentials'}), 400
        
        # Initialize Angel One client
        global angel_one_client
        # Update environment variables and reinitialize
        os.environ['ANGEL_ONE_API_KEY'] = api_key
        os.environ['ANGEL_ONE_CLIENT_ID'] = client_id
        os.environ['ANGEL_ONE_PASSWORD'] = password
        os.environ['ANGEL_ONE_TOTP_SECRET'] = totp_secret
        angel_one_client.initialize_smartapi()
        
        # Test connection
        if angel_one_client.is_connected:
            # Update all components with new client
            global trading_system, risk_manager, indian_trading_system
            
            trading_system = TradingSystem(angel_one_client)
            risk_manager = RiskManager(angel_one_client=angel_one_client)
            indian_trading_system = IndianTradingSystem(angel_one_client=angel_one_client)
            
            return jsonify({
                'status': 'success',
                'message': 'Angel One API configured successfully',
                'client_id': client_id
            })
        else:
            return jsonify({'error': 'Failed to authenticate with Angel One API'}), 401
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/angel_one/status', methods=['GET'])
def get_angel_one_status():
    """Get Angel One API status"""
    try:
        global angel_one_client
        
        if angel_one_client is None:
            return jsonify({
                'status': 'not_configured',
                'message': 'Angel One API not configured'
            })
        
        # Test connection
        if angel_one_client.is_connected:
            return jsonify({
                'status': 'connected',
                'message': 'Angel One API connected successfully',
                'user': angel_one_client.user_profile['data']['name'] if angel_one_client.user_profile else 'Unknown',
                'exchanges': angel_one_client.user_profile['data']['exchanges'] if angel_one_client.user_profile else []
            })
        else:
            return jsonify({
                'status': 'disconnected',
                'message': 'Angel One API not connected'
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/angel_one/refresh', methods=['POST'])
def refresh_angel_one_session():
    """Force an Angel One session refresh using configured credentials/TOTP"""
    try:
        global angel_one_client
        if angel_one_client is None:
            return jsonify({'error': 'Angel One API not configured'}), 400
        if not hasattr(angel_one_client, 'generate_session'):
            return jsonify({'status': 'mock', 'message': 'Using mock API'}), 200
        ok = angel_one_client.generate_session()
        if ok:
            return jsonify({'status': 'connected', 'message': 'Session refreshed successfully'})
        return jsonify({'status': 'disconnected', 'message': 'Failed to refresh session'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/angel_one/funds', methods=['GET'])
def get_angel_one_funds():
    """Get available funds from Angel One"""
    try:
        global angel_one_client
        
        if angel_one_client is None:
            return jsonify({'error': 'Angel One API not configured'}), 400
        
        funds = angel_one_client.get_funds()
        return jsonify(funds)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/angel_one/holdings', methods=['GET'])
def get_angel_one_holdings():
    """Get holdings from Angel One"""
    try:
        global angel_one_client
        
        if angel_one_client is None:
            return jsonify({'error': 'Angel One API not configured'}), 400
        
        holdings = angel_one_client.get_holdings()
        return jsonify(holdings)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/angel_one/positions', methods=['GET'])
def get_angel_one_positions():
    """Get positions from Angel One"""
    try:
        global angel_one_client
        
        if angel_one_client is None:
            return jsonify({'error': 'Angel One API not configured'}), 400
        
        positions = angel_one_client.get_positions()
        return jsonify(positions)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Trading Mode Management
@app.route('/api/current_mode', methods=['GET'])
def get_current_mode():
    """Get current trading mode"""
    try:
        # Check if user has set a preferred mode in session
        user_mode = session.get('trading_mode', 'demo')
        
        # If user wants live mode, check if Angel One is connected
        if user_mode == 'live':
            global angel_one_client
            if (angel_one_client and 
                hasattr(angel_one_client, 'access_token') and 
                angel_one_client.access_token and 
                angel_one_client.access_token != "mock_token" and
                hasattr(angel_one_client, 'is_connected') and
                angel_one_client.is_connected):
                return jsonify({'mode': 'live'})
            else:
                # Angel One not connected, fallback to demo
                session['trading_mode'] = 'demo'
                return jsonify({'mode': 'demo'})
        else:
            # User prefers demo mode
            return jsonify({'mode': 'demo'})
            
    except Exception as e:
        return jsonify({'mode': 'demo', 'error': str(e)})

@app.route('/api/switch_to_live', methods=['POST'])
def switch_to_live():
    """Switch to live trading mode"""
    try:
        global angel_one_client
        
        # Check if Angel One is properly configured
        if angel_one_client is None:
            return jsonify({'status': 'error', 'error': 'Angel One API not configured'}), 400
        
        # Test connection
        if angel_one_client.is_connected:
            # Store user's preference in session
            session['trading_mode'] = 'live'
            
            return jsonify({
                'status': 'success',
                'message': 'Switched to LIVE trading mode',
                'mode': 'live'
            })
        else:
            return jsonify({'status': 'error', 'error': 'Angel One API not connected. Please check credentials.'}), 400
            
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/switch_to_demo', methods=['POST'])
def switch_to_demo():
    """Switch to demo trading mode"""
    try:
        # Store user's preference in session
        session['trading_mode'] = 'demo'
        
        return jsonify({
            'status': 'success',
            'message': 'Switched to DEMO trading mode',
            'mode': 'demo'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

# Admin Panel API Endpoints
@app.route('/api/admin/system-status', methods=['GET'])
def get_admin_system_status():
    """Get system status for admin panel"""
    try:
        # Get current trading status
        auto_trading_active = hasattr(indian_auto_trader, 'running') and indian_auto_trader.running
        
        # Get portfolio summary
        portfolio_summary = portfolio_analytics.get_portfolio_summary(1)  # Default user ID
        
        # Get performance data
        performance = portfolio_analytics.get_performance_analysis(1, 30)
        
        # Check data feed status
        data_service = get_data_injection_service()
        data_feed_active = len(data_service.update_threads) > 0
        
        # Check trading mode using the same logic as base template
        try:
            # Use the same mode detection as the base template
            from app import get_setting
            trading_mode = 'LIVE' if get_setting('simulation_mode', '0') == '0' else 'DEMO'
        except Exception:
            # Fallback to DEMO mode if there's an error
            trading_mode = 'DEMO'
        
        return jsonify({
            'active_trades': portfolio_summary.get('num_active_positions', 0),
            'total_pnl': portfolio_summary.get('today_pnl', 0.0),
            'win_rate': performance.get('win_rate', 0.0),
            'trading_mode': trading_mode,
            'auto_trading_active': auto_trading_active,
            'data_feed_active': data_feed_active,
            'system_health': 100,  # Placeholder
            'data_quality': 85 if data_feed_active else 0  # Placeholder
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/trading-params', methods=['GET'])
def get_admin_trading_params():
    """Get current trading parameters"""
    try:
        # Get current risk manager settings
        risk_params = {
            'max_concurrent_trades': getattr(risk_manager, 'max_concurrent_trades', 5),
            'risk_per_trade': getattr(risk_manager, 'risk_per_trade', 2.0),
            'stop_loss': getattr(risk_manager, 'stop_loss', 3.0),
            'take_profit': getattr(risk_manager, 'take_profit', 6.0),
            'strategy': 'momentum'  # Default strategy
        }
        
        return jsonify(risk_params)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/update-trading-params', methods=['POST'])
def update_admin_trading_params():
    """Update trading parameters"""
    try:
        data = request.get_json()
        
        # Update risk manager parameters
        if hasattr(risk_manager, 'max_concurrent_trades'):
            risk_manager.max_concurrent_trades = data.get('max_concurrent_trades', 5)
        if hasattr(risk_manager, 'risk_per_trade'):
            risk_manager.risk_per_trade = data.get('risk_per_trade', 2.0)
        if hasattr(risk_manager, 'stop_loss'):
            risk_manager.stop_loss = data.get('stop_loss', 3.0)
        if hasattr(risk_manager, 'take_profit'):
            risk_manager.take_profit = data.get('take_profit', 6.0)
        
        # Update strategy (placeholder for future implementation)
        strategy = data.get('strategy', 'momentum')
        
        return jsonify({
            'status': 'success',
            'message': 'Trading parameters updated successfully'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/start-data-injection', methods=['POST'])
def start_admin_data_injection():
    """Start data injection from external API"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get('data_source') or not data.get('symbols'):
            return jsonify({'status': 'error', 'error': 'Missing required fields'}), 400
        
        # Get data injection service
        data_service = get_data_injection_service()
        
        # Start data injection
        result = data_service.start_data_injection(data)
        
        if result['status'] == 'success':
            return jsonify(result)
        else:
            return jsonify(result), 400
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/stop-data-injection', methods=['POST'])
def stop_admin_data_injection():
    """Stop data injection"""
    try:
        # Get data injection service
        data_service = get_data_injection_service()
        
        # Stop data injection
        result = data_service.stop_data_injection()
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/start-auto-trading', methods=['POST'])
def start_admin_auto_trading():
    """Start auto-trading system"""
    try:
        if hasattr(indian_auto_trader, 'start'):
            indian_auto_trader.start()
            return jsonify({
                'status': 'success',
                'message': 'Auto-trading started successfully'
            })
        else:
            return jsonify({'status': 'error', 'error': 'Auto-trading system not available'}), 400
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/stop-auto-trading', methods=['POST'])
def stop_admin_auto_trading():
    """Stop auto-trading system"""
    try:
        if hasattr(indian_auto_trader, 'stop'):
            indian_auto_trader.stop()
            return jsonify({
                'status': 'success',
                'message': 'Auto-trading stopped successfully'
            })
        else:
            return jsonify({'status': 'error', 'error': 'Auto-trading system not available'}), 400
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/clear-trades', methods=['POST'])
def clear_admin_trades():
    """Clear all trades"""
    try:
        connection = get_db()
        if connection is None:
            return jsonify({'status': 'error', 'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor()
        
        # Clear trades table
        cursor.execute('DELETE FROM trades')
        cursor.execute('DELETE FROM positions')
        cursor.execute('DELETE FROM portfolio_history')
        cursor.execute('DELETE FROM signals')
        
        # Reset user balance
        cursor.execute('UPDATE users SET balance = 100000.00 WHERE id = 1')
        
        # Insert initial portfolio snapshot
        cursor.execute('''
            INSERT INTO portfolio_history (user_id, portfolio_value, timestamp)
            VALUES (?, ?, ?)
        ''', (1, 100000.00, datetime.now().isoformat()))
        
        connection.commit()
        cursor.close()
        
        return jsonify({
            'status': 'success',
            'message': 'All trades cleared successfully'
        })
        
    except Exception as e:
        logger.error(f"Error clearing trades: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/reset-portfolio', methods=['POST'])
def reset_admin_portfolio():
    """Reset portfolio to initial state"""
    try:
        connection = get_db()
        if connection is None:
            return jsonify({'status': 'error', 'error': 'Database connection failed'})
        
        cursor = connection.cursor()
        
        # Clear all trading data
        cursor.execute('DELETE FROM trades')
        cursor.execute('DELETE FROM positions')
        cursor.execute('DELETE FROM portfolio_history')
        cursor.execute('DELETE FROM signals')
        
        # Reset user balance to initial amount
        cursor.execute('UPDATE users SET balance = 100000.00 WHERE id = 1')
        
        # Insert initial portfolio snapshot
        cursor.execute('''
            INSERT INTO portfolio_history (user_id, portfolio_value, timestamp)
            VALUES (?, ?, ?)
        ''', (1, 100000.00, datetime.now().isoformat()))
        
        # Re-seed with sample data
        init_db()
        
        connection.commit()
        cursor.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Portfolio reset successfully'
        })
        
    except Exception as e:
        logger.error(f"Error resetting portfolio: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/export-data', methods=['GET', 'POST'])
def export_admin_data():
    """Export trading data"""
    try:
        if request.method == 'POST':
            # Handle POST request from exportFetchedData
            data = request.get_json()
            data_type = data.get('data_type')
            format_type = data.get('format', 'json')
            start_date = data.get('start_date')
            end_date = data.get('end_date')
            
            # Use the fetched data from the frontend
            export_data = data.get('data', [])
            
            if format_type.lower() == 'json':
                # Create JSON export
                import json
                from io import BytesIO
                
                json_data = json.dumps(export_data, indent=2, default=str)
                filename = f"{data_type}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                
                return json_data, 200, {
                    'Content-Type': 'application/json',
                    'Content-Disposition': f'attachment; filename="{filename}"'
                }
            
            elif format_type.lower() == 'csv':
                # Create CSV export
                import csv
                from io import StringIO
                
                if not export_data:
                    return jsonify({'error': 'No data to export'}), 400
                
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=export_data[0].keys())
                writer.writeheader()
                writer.writerows(export_data)
                
                filename = f"{data_type}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                
                return output.getvalue(), 200, {
                    'Content-Type': 'text/csv',
                    'Content-Disposition': f'attachment; filename="{filename}"'
                }
            
            else:
                return jsonify({'error': 'Unsupported format'}), 400
        
        # Handle GET request (original functionality)
        connection = get_db()
        if connection is None:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor()
        
        # Export trades
        cursor.execute('SELECT * FROM trades')
        trades = cursor.fetchall()
        
        # Export positions
        cursor.execute('SELECT * FROM positions')
        positions = cursor.fetchall()
        
        # Export portfolio history
        cursor.execute('SELECT * FROM portfolio_history')
        portfolio_history = cursor.fetchall()
        
        # Export users
        cursor.execute('SELECT * FROM users')
        users = cursor.fetchall()
        
        cursor.close()
        
        export_data = {
            'trades': trades,
            'positions': positions,
            'portfolio_history': portfolio_history,
            'users': users,
            'export_timestamp': datetime.now().isoformat()
        }
        
        return jsonify(export_data)
        
    except Exception as e:
        logger.error(f"Error exporting data: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/import-data', methods=['POST'])
def import_admin_data():
    """Import trading data"""
    try:
        data = request.get_json()
        
        connection = get_db()
        if connection is None:
            return jsonify({'status': 'error', 'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor()
        
        # Clear existing data
        cursor.execute('DELETE FROM trades')
        cursor.execute('DELETE FROM positions')
        cursor.execute('DELETE FROM portfolio_history')
        cursor.execute('DELETE FROM signals')
        cursor.execute('DELETE FROM users')
        
        # Import trades
        if 'trades' in data:
            for trade in data['trades']:
                cursor.execute('''
                    INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', trade)
        
        # Import positions
        if 'positions' in data:
            for position in data['positions']:
                cursor.execute('''
                    INSERT INTO positions VALUES (?, ?, ?, ?, ?)
                ''', position)
        
        # Import portfolio history
        if 'portfolio_history' in data:
            for entry in data['portfolio_history']:
                cursor.execute('''
                    INSERT INTO portfolio_history VALUES (?, ?, ?)
                ''', entry)
        
        # Import users
        if 'users' in data:
            for user in data['users']:
                cursor.execute('''
                    INSERT INTO users VALUES (?, ?, ?, ?, ?)
                ''', user)
        
        connection.commit()
        cursor.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Data imported successfully'
        })
        
    except Exception as e:
        logger.error(f"Error importing data: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

# Security endpoints
@app.route('/api/admin/security-stats', methods=['GET'])
def get_security_stats():
    """Get security statistics"""
    try:
        stats = security_manager.get_security_stats()
        return jsonify({'status': 'success', 'security_stats': stats})
    except Exception as e:
        logger.error(f"Error getting security stats: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/security-events', methods=['GET'])
def get_security_events():
    """Get recent security events"""
    try:
        limit = request.args.get('limit', 100, type=int)
        events = security_manager.get_security_events(limit)
        return jsonify({'status': 'success', 'security_events': events})
    except Exception as e:
        logger.error(f"Error getting security events: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/grant-permission', methods=['POST'])
def grant_permission():
    """Grant permission to user"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        permission = data.get('permission')
        granted_by = data.get('granted_by')
        
        if not user_id or not permission:
            return jsonify({'status': 'error', 'error': 'user_id and permission are required'}), 400
        
        security_manager.grant_permission(user_id, permission, granted_by)
        return jsonify({'status': 'success', 'message': f'Permission {permission} granted to user {user_id}'})
    except Exception as e:
        logger.error(f"Error granting permission: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/revoke-permission', methods=['POST'])
def revoke_permission():
    """Revoke permission from user"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        permission = data.get('permission')
        
        if not user_id or not permission:
            return jsonify({'status': 'error', 'error': 'user_id and permission are required'}), 400
        
        security_manager.revoke_permission(user_id, permission)
        return jsonify({'status': 'success', 'message': f'Permission {permission} revoked from user {user_id}'})
    except Exception as e:
        logger.error(f"Error revoking permission: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/cleanup-sessions', methods=['POST'])
def cleanup_sessions():
    """Clean up expired sessions"""
    try:
        conn = get_db()
        if conn is None:
            return jsonify({'status': 'error', 'error': 'Database connection failed'}), 500
        
        cursor = conn.cursor()
        
        # Deactivate expired sessions
        cursor.execute('''
            UPDATE user_sessions 
            SET is_active = 0 
            WHERE expires_at < CURRENT_TIMESTAMP AND is_active = 1
        ''')
        
        expired_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success', 
            'message': f'Cleaned up {expired_count} expired sessions'
        })
    except Exception as e:
        logger.error(f"Error cleaning up sessions: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

# Notification endpoints
@app.route('/api/admin/notification-stats', methods=['GET'])
def get_notification_stats():
    """Get notification statistics"""
    try:
        stats = notification_system.get_notification_stats()
        return jsonify({'status': 'success', 'notification_stats': stats})
    except Exception as e:
        logger.error(f"Error getting notification stats: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/send-test-notification', methods=['POST'])
def send_test_notification():
    """Send test notification"""
    try:
        data = request.get_json()
        user_id = data.get('user_id', 1)  # Default to admin user
        notification_type = data.get('type', 'system_alert')
        message = data.get('message', 'This is a test notification')
        
        notification_id = notification_system.create_notification(
            user_id=user_id,
            notification_type=NotificationType(notification_type),
            priority=NotificationPriority.MEDIUM,
            title="Test Notification",
            message=message,
            data={'test': True}
        )
        
        return jsonify({
            'status': 'success', 
            'message': 'Test notification sent',
            'notification_id': notification_id
        })
    except Exception as e:
        logger.error(f"Error sending test notification: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

# Backup endpoints
@app.route('/api/admin/backup-stats', methods=['GET'])
def get_backup_stats():
    """Get backup statistics"""
    try:
        stats = backup_system.get_backup_stats()
        return jsonify({'status': 'success', 'backup_stats': stats})
    except Exception as e:
        logger.error(f"Error getting backup stats: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/backup-history', methods=['GET'])
def get_backup_history():
    """Get backup history"""
    try:
        limit = request.args.get('limit', 50, type=int)
        history = backup_system.get_backup_history(limit)
        return jsonify({'status': 'success', 'backup_history': history})
    except Exception as e:
        logger.error(f"Error getting backup history: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/create-backup', methods=['POST'])
def create_backup():
    """Create backup"""
    try:
        data = request.get_json()
        backup_type = data.get('type', 'full')  # full, database, config
        
        if backup_type == 'full':
            result = backup_system.create_full_backup()
        elif backup_type == 'database':
            result = backup_system.create_database_backup()
        elif backup_type == 'config':
            result = backup_system.create_config_backup()
        else:
            return jsonify({'status': 'error', 'error': 'Invalid backup type'}), 400
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/cleanup-backups', methods=['POST'])
def cleanup_backups():
    """Clean up old backups"""
    try:
        result = backup_system.cleanup_old_backups()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error cleaning up backups: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/admin/fetch-data', methods=['POST'])
def fetch_admin_data():
    """Fetch data based on admin selection"""
    try:
        data = request.get_json()
        data_type = data.get('data_type')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        limit = data.get('limit', 100)
        additional_filters = data.get('additional_filters', {})
        
        if not data_type:
            return jsonify({
                'status': 'error',
                'error': 'Data type is required'
            }), 400
        
        # Initialize database connection
        conn = sqlite3.connect('trading.db')
        cursor = conn.cursor()
        
        result_data = []
        record_count = 0
        
        # Fetch data based on type
        if data_type == 'market_data':
            # Check if market_data table exists, if not create it
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='market_data'")
            if not cursor.fetchone():
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS market_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        open_price REAL,
                        high_price REAL,
                        low_price REAL,
                        close_price REAL,
                        volume INTEGER,
                        change_percent REAL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
            
            query = "SELECT * FROM market_data WHERE 1=1"
            params = []
            
            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)
            
            query += " ORDER BY timestamp DESC"
            if limit > 0:
                query += " LIMIT ?"
                params.append(limit)
            
            cursor.execute(query, params)
            result_data = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
            record_count = len(result_data)
            
        elif data_type == 'trading_signals':
            # Check if trading_signals table exists, if not create it
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trading_signals'")
            if not cursor.fetchone():
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trading_signals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        signal_type TEXT NOT NULL,
                        confidence REAL,
                        price REAL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        status TEXT DEFAULT 'active'
                    )
                ''')
                conn.commit()
            
            query = "SELECT * FROM trading_signals WHERE 1=1"
            params = []
            
            if start_date:
                query += " AND created_at >= ?"
                params.append(start_date)
            if end_date:
                query += " AND created_at <= ?"
                params.append(end_date)
            
            query += " ORDER BY created_at DESC"
            if limit > 0:
                query += " LIMIT ?"
                params.append(limit)
            
            cursor.execute(query, params)
            result_data = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
            record_count = len(result_data)
            
        elif data_type == 'user_data':
            # Check if users table exists, if not create it
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            if not cursor.fetchone():
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        balance REAL DEFAULT 10000.0,
                        is_premium BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMP
                    )
                ''')
                # Insert a default admin user
                cursor.execute('''
                    INSERT OR IGNORE INTO users (username, email, password_hash, balance, is_premium)
                    VALUES ('admin', 'admin@example.com', 'hashed_password', 100000.0, TRUE)
                ''')
                conn.commit()
            
            query = "SELECT id, username, password, balance, is_premium, registered_at, last_login FROM users WHERE 1=1"
            params = []
            
            if additional_filters.get('user_id'):
                query += " AND id = ?"
                params.append(additional_filters['user_id'])
            if additional_filters.get('is_premium') is not None:
                query += " AND is_premium = ?"
                params.append(additional_filters['is_premium'])
            
            query += " ORDER BY registered_at DESC"
            if limit > 0:
                query += " LIMIT ?"
                params.append(limit)
            
            cursor.execute(query, params)
            result_data = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
            record_count = len(result_data)
            
        elif data_type == 'trade_history':
            # Check if trades table exists, if not create it
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
            if not cursor.fetchone():
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        price REAL NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        status TEXT DEFAULT 'pending',
                        pnl REAL DEFAULT 0.0,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                ''')
                conn.commit()
            
            query = "SELECT * FROM trades WHERE 1=1"
            params = []
            
            if start_date:
                query += " AND entry_time >= ?"
                params.append(start_date)
            if end_date:
                query += " AND entry_time <= ?"
                params.append(end_date)
            if additional_filters.get('user_id'):
                query += " AND user_id = ?"
                params.append(additional_filters['user_id'])
            if additional_filters.get('status'):
                query += " AND status = ?"
                params.append(additional_filters['status'])
            
            query += " ORDER BY entry_time DESC"
            if limit > 0:
                query += " LIMIT ?"
                params.append(limit)
            
            cursor.execute(query, params)
            result_data = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
            record_count = len(result_data)
            
        elif data_type == 'portfolio_data':
            # Check if portfolio table exists, if not create it
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio'")
            if not cursor.fetchone():
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS portfolio (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        symbol TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        average_price REAL NOT NULL,
                        current_price REAL,
                        total_value REAL,
                        pnl REAL DEFAULT 0.0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                ''')
                conn.commit()
            
            query = "SELECT * FROM portfolio WHERE 1=1"
            params = []
            
            if additional_filters.get('user_id'):
                query += " AND user_id = ?"
                params.append(additional_filters['user_id'])
            
            query += " ORDER BY updated_at DESC"
            if limit > 0:
                query += " LIMIT ?"
                params.append(limit)
            
            cursor.execute(query, params)
            result_data = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
            record_count = len(result_data)
            
        elif data_type == 'system_logs':
            # For system logs, we'll return a mock structure since we don't have a logs table
            result_data = [
                {
                    'timestamp': '2025-01-06 10:30:00',
                    'level': 'INFO',
                    'message': 'System started successfully',
                    'module': 'app'
                },
                {
                    'timestamp': '2025-01-06 10:31:00',
                    'level': 'INFO',
                    'message': 'Database connection established',
                    'module': 'database'
                },
                {
                    'timestamp': '2025-01-06 10:32:00',
                    'level': 'WARNING',
                    'message': 'API rate limit approaching',
                    'module': 'api'
                }
            ]
            record_count = len(result_data)
            
        elif data_type == 'performance_metrics':
            # Calculate performance metrics
            cursor.execute("SELECT COUNT(*) as total_trades FROM trades")
            total_trades = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) as winning_trades FROM trades WHERE pnl > 0")
            winning_trades = cursor.fetchone()[0]
            
            cursor.execute("SELECT SUM(pnl) as total_pnl FROM trades")
            total_pnl = cursor.fetchone()[0] or 0
            
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            result_data = [{
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': total_trades - winning_trades,
                'total_pnl': total_pnl,
                'win_rate': round(win_rate, 2),
                'calculated_at': datetime.now().isoformat()
            }]
            record_count = 1
            
        elif data_type == 'security_events':
            # Mock security events data
            result_data = [
                {
                    'timestamp': '2025-01-06 09:00:00',
                    'event_type': 'LOGIN_SUCCESS',
                    'user_id': 1,
                    'ip_address': '192.168.1.100',
                    'severity': 'INFO'
                },
                {
                    'timestamp': '2025-01-06 09:15:00',
                    'event_type': 'API_RATE_LIMIT',
                    'user_id': 1,
                    'ip_address': '192.168.1.100',
                    'severity': 'WARNING'
                }
            ]
            record_count = len(result_data)
            
        elif data_type == 'api_status':
            # Get API status information
            result_data = [{
                'api_name': 'Angel One API',
                'status': 'connected' if hasattr(app, 'angel_one_client') and app.angel_one_client.is_connected else 'disconnected',
                'last_check': datetime.now().isoformat(),
                'response_time': '150ms'
            }]
            record_count = 1
            
        elif data_type == 'trading_parameters':
            # Get trading parameters
            result_data = [{
                'max_concurrent_trades': 5,
                'risk_per_trade': 2.0,
                'stop_loss': 3.0,
                'take_profit': 6.0,
                'strategy': 'momentum',
                'updated_at': datetime.now().isoformat()
            }]
            record_count = 1
            
        elif data_type == 'all_data':
            # Fetch all data types with error handling
            all_data = {}
            
            # Helper function to safely get count
            def safe_count(table_name):
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    return cursor.fetchone()[0]
                except:
                    return 0
            
            # Market data
            all_data['market_data_count'] = safe_count('market_data')
            
            # Trades
            all_data['trades_count'] = safe_count('trades')
            
            # Users
            all_data['users_count'] = safe_count('users')
            
            # Portfolio
            all_data['portfolio_count'] = safe_count('portfolio')
            
            # Trading signals
            all_data['trading_signals_count'] = safe_count('trading_signals')
            
            # Add summary info
            all_data['total_records'] = sum([
                all_data['market_data_count'],
                all_data['trades_count'],
                all_data['users_count'],
                all_data['portfolio_count'],
                all_data['trading_signals_count']
            ])
            all_data['exported_at'] = datetime.now().isoformat()
            
            result_data = [all_data]
            record_count = 1
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'data': result_data,
            'record_count': record_count,
            'data_type': data_type,
            'filters_applied': {
                'start_date': start_date,
                'end_date': end_date,
                'limit': limit,
                'additional_filters': additional_filters
            }
        })
        
    except Exception as e:
        logger.error(f"Error fetching admin data: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e),
            'data_type': data.get('data_type', 'unknown') if 'data' in locals() else 'unknown'
        }), 500

@app.route('/api/market-status', methods=['GET'])
def get_market_status():
    """Get current market status"""
    try:
        market_open = is_indian_market_open()
        
        # Get current IST time
        from datetime import datetime
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        return jsonify({
            'market_open': market_open,
            'current_time': now.strftime('%H:%M:%S'),
            'current_date': now.strftime('%Y-%m-%d'),
            'timezone': 'Asia/Kolkata',
            'market_hours': {
                'open': '09:15',
                'close': '15:30'
            }
        })
    except Exception as e:
        logger.error(f"Error getting market status: {str(e)}")
        return jsonify({'error': str(e)}), 500

# OTC/Forex removed

# Store active subscriptions
active_subscriptions = {}

# Initialize Angel One API (mock disabled)
# Initialize Angel One API using the working connection
print("🔄 Initializing Angel One API...")
try:
    # Initialize the connection
    success = initialize_smartapi()
    if success:
        print("✅ Angel One API initialized successfully")
        print(f"✅ Connected as: {angel_one_client.user_profile['data']['name']}")
        print(f"✅ Available exchanges: {angel_one_client.user_profile['data']['exchanges']}")
    else:
        print("❌ Failed to initialize Angel One API")
except Exception as e:
    print(f"❌ Error initializing Angel One API: {e}")

# --- Angel One API endpoints ---
@app.route('/api/angel_one/status', methods=['GET'])
def angel_one_status():
    try:
        if angel_one_client is None:
            return jsonify({
                'status': 'not_configured',
                'message': 'Angel One Not Configured'
            })
        
        if not angel_one_client.is_connected:
            return jsonify({
                'status': 'disconnected',
                'message': 'Angel One API not connected'
            })
        
        return jsonify({
            'status': 'connected',
            'message': 'Angel One API is connected',
            'user': angel_one_client.user_profile['data']['name'] if angel_one_client.user_profile else 'Unknown',
            'exchanges': angel_one_client.user_profile['data']['exchanges'] if angel_one_client.user_profile else []
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/angel_one/configure', methods=['POST'])
def angel_one_configure():
    global angel_one_client
    try:
        payload = request.get_json(force=True, silent=True) or {}
        api_key = payload.get('api_key')
        client_id = payload.get('client_id') or payload.get('client_code')
        password = payload.get('password')
        totp_secret = payload.get('totp_secret')

        if not all([api_key, client_id, password, totp_secret]):
            return jsonify({'status': 'error', 'error': 'Missing required credentials'}), 400

        # Update environment variables
        os.environ['ANGEL_ONE_API_KEY'] = api_key
        os.environ['ANGEL_ONE_CLIENT_ID'] = client_id
        os.environ['ANGEL_ONE_PASSWORD'] = password
        os.environ['ANGEL_ONE_TOTP_SECRET'] = totp_secret
        
        # Reinitialize connection
        success = angel_one_client.initialize_smartapi()
        if not success:
            return jsonify({'status': 'error', 'error': 'Failed to generate Angel One session'}), 500
        
        return jsonify({
            'status': 'success', 
            'message': 'Angel One API configured successfully',
            'user': angel_one_client.user_profile['data']['name'] if angel_one_client.user_profile else 'Unknown'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/angel_one/refresh', methods=['POST'])
def angel_one_refresh():
    try:
        if angel_one_client is None:
            return jsonify({'status': 'error', 'error': 'Angel One not configured'}), 400
        ok = angel_one_client.generate_session()
        if not ok:
            return jsonify({'status': 'error', 'error': 'Failed to refresh session'}), 500
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/angel_one/funds', methods=['GET'])
def angel_one_funds():
    try:
        if angel_one_client is None or not angel_one_client.is_connected:
            return jsonify({'status': 'error', 'error': 'Not connected'}), 400
        funds = angel_one_client.get_funds()
        return jsonify({'status': 'success', 'data': funds})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/angel_one/holdings', methods=['GET'])
def angel_one_holdings():
    try:
        if angel_one_client is None or not angel_one_client.is_connected:
            return jsonify({'status': 'error', 'error': 'Not connected'}), 400
        holdings = angel_one_client.get_holdings()
        return jsonify({'status': 'success', 'data': holdings})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/angel_one/positions', methods=['GET'])
def angel_one_positions():
    try:
        if angel_one_client is None or not angel_one_client.is_connected:
            return jsonify({'status': 'error', 'error': 'Not connected'}), 400
        positions = angel_one_client.get_positions()
        return jsonify({'status': 'success', 'data': positions})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/angel_one/symbols/search', methods=['GET'])
def search_angel_one_symbols_api():
    """Search for symbols in Angel One scrip master data"""
    try:
        query = request.args.get('q', '')
        limit = int(request.args.get('limit', 20))
        
        if not query:
            return jsonify({'status': 'error', 'message': 'Query parameter is required'}), 400
        
        results = search_angel_one_symbols(query, limit)
        return jsonify({'status': 'success', 'data': results})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/angel_one/scrip-master/refresh', methods=['POST'])
def refresh_scrip_master():
    """Refresh Angel One scrip master data"""
    try:
        global symbol_map, angel_one_symbols
        
        # Reload scrip master data
        angel_one_symbols = load_angel_one_scrip_master()
        
        # Update symbol map
        symbol_map.update(angel_one_symbols)
        
        return jsonify({
            'status': 'success', 
            'message': f'Scrip master refreshed with {len(angel_one_symbols)} instruments'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/angel_one/scrip-master/stats', methods=['GET'])
def get_scrip_master_stats():
    """Get Angel One scrip master statistics"""
    try:
        stats = {
            'total_symbols': len(symbol_map),
            'angel_one_symbols': len(angel_one_symbols),
            'last_updated': datetime.now().isoformat()
        }
        return jsonify({'status': 'success', 'data': stats})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

# Initialize components with Angel One integration
trading_system = TradingSystem(angel_one_client)
risk_manager = RiskManager(angel_one_client=angel_one_client)
auto_trader = AutoTrader(trading_system, risk_manager)

# Initialize Indian trading system
indian_trading_system = IndianTradingSystem(angel_one_client=angel_one_client)
indian_auto_trader = IndianAutoTrader(indian_trading_system, user_id=1, default_quantity=1, initial_balance=5000.0)

# Initialize portfolio analytics
portfolio_analytics = PortfolioAnalytics()

# Market status check function
def is_indian_market_open():
    """Check if Indian market is currently open"""
    try:
        from datetime import datetime
        import pytz
        
        # Get current IST time
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        # Check if it's a weekday (Monday to Friday)
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False
            
        # Check market hours (9:15 AM to 3:30 PM IST)
        current_time = now.time()
        market_open = datetime.strptime('09:15', '%H:%M').time()
        market_close = datetime.strptime('15:30', '%H:%M').time()
        
        return market_open <= current_time <= market_close
        
    except Exception as e:
        logger.error(f"Error checking market hours: {str(e)}")
        return False

# websocket_handler = WebSocketHandler(socketio)

# OTC/Forex removed

# --- Database helpers ---
def get_db():
    """Create a connection to the SQLite database"""
    if not hasattr(g, 'db'):
        try:
            logger.info("Attempting to connect to SQLite database")
            
            # Use SQLite instead of MySQL for simplicity
            db_path = os.path.join(BASE_DIR, 'trading.db')
            g.db = sqlite3.connect(db_path, check_same_thread=False)
            g.db.row_factory = sqlite3.Row  # Enable dictionary-like access
            
            # Test the connection with a simple query
            try:
                cursor = g.db.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
                logger.info("Successfully connected to SQLite database")
                return g.db
            except Exception as e:
                logger.error(f"Database connection test failed: {str(e)}")
                if hasattr(g.db, 'close'):
                    g.db.close()
                flash("Database connection error. Please try again later.", "error")
                return None
                
        except Exception as e:
            logger.error(f"Error connecting to SQLite database: {str(e)}", exc_info=True)
            flash("Database connection error. Please try again later.", "error")
            return None
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, 'db', None)
    if db is not None:
        try:
            db.close()
            logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error closing database connection: {e}")

def init_db():
    """Initialize the database with required tables"""
    try:
        import sqlite3
        db_path = os.path.join(BASE_DIR, 'trading.db')
        connection = sqlite3.connect(db_path)
        
        cursor = connection.cursor()
        
        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                last_login TEXT,
                balance REAL DEFAULT 10000.00,
                is_premium BOOLEAN DEFAULT 0,
                demo_end_time TEXT
            )
        """)

        # Create signals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                confidence REAL NOT NULL,
                time TEXT NOT NULL,
                created_at TEXT NOT NULL,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                result INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Create trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                profit_loss REAL,
                stop_loss REAL,
                take_profit REAL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Create positions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                average_price REAL NOT NULL,
                last_updated TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                UNIQUE(user_id, symbol)
            )
        """)

        # Create portfolio_history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                portfolio_value REAL NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Create a default user if none exists
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        if user_count == 0:
            # Create default user
            default_password = generate_password_hash("admin123")
            cursor.execute("""
                INSERT INTO users (username, password, registered_at, balance, is_premium, demo_end_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                "admin",
                default_password,
                datetime.now().isoformat(),
                100000.00,  # Starting balance
                1,  # Premium user
                (datetime.now() + timedelta(days=30)).isoformat()  # 30 days demo
            ))
            
            # Create initial portfolio snapshot
            cursor.execute("""
                INSERT INTO portfolio_history (user_id, portfolio_value, timestamp)
                VALUES (?, ?, ?)
            """, (1, 100000.00, datetime.now().isoformat()))
            
            # Create portfolio history over the last 30 days
            for i in range(30):
                date = datetime.now() - timedelta(days=i)
                # Simulate some portfolio value changes
                value_change = random.uniform(-2000, 3000)
                portfolio_value = 100000.00 + value_change
                cursor.execute("""
                    INSERT INTO portfolio_history (user_id, portfolio_value, timestamp)
                    VALUES (?, ?, ?)
                """, (1, max(portfolio_value, 50000), date.isoformat()))
            
            logger.info("Default user 'admin' created with password 'admin123'")
            
            # Create some sample trades for demonstration
            sample_trades = [
                ("NIFTY50", "BUY", 19500.0, 19750.0, 100, "CLOSED", 2500.0),
                ("BANKNIFTY", "SELL", 44500.0, 44200.0, 50, "CLOSED", 1500.0),
                ("RELIANCE", "BUY", 2500.0, 2525.0, 200, "CLOSED", 500.0),
                ("TCS", "SELL", 3800.0, 3750.0, 150, "CLOSED", 750.0),
                ("INFY", "BUY", 1500.0, 1480.0, 300, "CLOSED", -600.0)
            ]
            
            for symbol, direction, entry_price, exit_price, quantity, status, pnl in sample_trades:
                cursor.execute("""
                    INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, quantity, status, entry_time, exit_time, profit_loss)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    1, symbol, direction, entry_price, exit_price, quantity, status,
                    (datetime.now() - timedelta(days=5)).isoformat(),
                    (datetime.now() - timedelta(days=4)).isoformat(),
                    pnl
                ))
            
            logger.info("Sample trades created for demonstration")
            
            # Create some sample positions
            sample_positions = [
                ("NIFTY50", 50, 19800.0),
                ("BANKNIFTY", 25, 44300.0),
                ("RELIANCE", 100, 2530.0)
            ]
            
            for symbol, quantity, avg_price in sample_positions:
                cursor.execute("""
                    INSERT INTO positions (user_id, symbol, quantity, average_price, last_updated)
                    VALUES (?, ?, ?, ?, ?)
                """, (1, symbol, quantity, avg_price, datetime.now().isoformat()))
            
            logger.info("Sample positions created for demonstration")
            
            # Create some sample signals
            sample_signals = [
                ("NIFTY50", "BUY", 0.85, 19500.0, 19400.0, 19700.0),
                ("BANKNIFTY", "SELL", 0.78, 44500.0, 44800.0, 44200.0),
                ("RELIANCE", "BUY", 0.92, 2500.0, 2480.0, 2550.0)
            ]
            
            for symbol, direction, confidence, entry_price, stop_loss, take_profit in sample_signals:
                cursor.execute("""
                    INSERT INTO signals (user_id, pair, direction, confidence, time, created_at, entry_price, stop_loss, take_profit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    1, symbol, direction, confidence, 
                    (datetime.now() - timedelta(hours=2)).isoformat(),
                    datetime.now().isoformat(),
                    entry_price, stop_loss, take_profit
                ))
            
            logger.info("Sample signals created for demonstration")
        
        connection.commit()
        logger.info("Database tables created successfully")

    except Exception as e:
        logger.error(f"Error creating database tables: {e}")
        raise
    finally:
        if 'connection' in locals():
            cursor.close()
            connection.close()
            logger.info("Database connection closed")

# Initialize database on startup
def init_app():
    with app.app_context():
        try:
            init_db()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

# Initialize the application
init_app()

# --- User helpers ---
def get_user_by_username(username):
    """Get user by username from SQLite database"""
    try:
        connection = get_db()
        if connection is None:
            return None
            
        cursor = connection.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        cursor.close()
        return user
    except Exception as e:
        logger.error(f"Error getting user by username: {e}")
        return None

def get_user_by_id(user_id):
    """Get user by ID from SQLite database"""
    try:
        connection = get_db()
        if connection is None:
            return None
            
        cursor = connection.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        cursor.close()
        return user
    except Exception as e:
        logger.error(f"Error getting user by ID: {e}")
        return None

def create_user(username, password):
    """Create a new user in SQLite database"""
    try:
        connection = get_db()
        if connection is None:
            return False
            
        cursor = connection.cursor()
        hashed = generate_password_hash(password)
        cursor.execute(
            'INSERT INTO users (username, password, registered_at) VALUES (?, ?, ?)',
            (username, hashed, datetime.now().isoformat())
        )
        connection.commit()
        cursor.close()
        return True
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return False

def update_last_login(user_id):
    """Update user's last login time in SQLite database"""
    try:
        connection = get_db()
        if connection is None:
            return False
            
        cursor = connection.cursor()
        cursor.execute(
            'UPDATE users SET last_login = ? WHERE id = ?',
            (datetime.now().isoformat(), user_id)
        )
        connection.commit()
        cursor.close()
        return True
    except Exception as e:
        logger.error(f"Error updating last login: {e}")
        return False

def verify_user(username, password):
    """Verify user credentials against SQLite database"""
    try:
        connection = get_db()
        if connection is None:
            return None
            
        cursor = connection.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        cursor.close()
        
        if user and check_password_hash(user['password'], password):  # Use dictionary-like access
            return user
        return None
    except Exception as e:
        logger.error(f"Error verifying user: {e}")
        return None

def update_password_hash(user_id, password):
    """Update user's password hash in SQLite database"""
    try:
        connection = get_db()
        if connection is None:
            return False
            
        cursor = connection.cursor()
        hashed = generate_password_hash(password)
        cursor.execute(
            'UPDATE users SET password = ? WHERE id = ?',
            (hashed, user_id)
        )
        connection.commit()
        cursor.close()
        return True
    except Exception as e:
        logger.error(f"Error updating password: {e}")
        return False

# --- Signal helpers ---
def save_signal(user_id, time, pair, direction, entry_price=None, stop_loss=None, take_profit=None, confidence=None):
    """Save trading signal with additional parameters"""
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('''
            INSERT INTO signals (
                user_id, time, pair, direction, confidence, created_at,
                entry_price, stop_loss, take_profit, result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id, time, pair, direction, confidence or 0.0, datetime.now().isoformat(),
            entry_price, stop_loss, take_profit, None
        ))
        db.commit()
        cursor.close()
        return True
    except Exception as e:
        logger.error(f"Error saving signal: {str(e)}")
        return False

def get_signals_for_user(user_id, limit=20):
    """Get trading signals for user with all details"""
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('''
            SELECT
                time, pair, direction, confidence, created_at,
                entry_price, stop_loss, take_profit, result
            FROM signals
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (user_id, limit))
        signals = cursor.fetchall()
        cursor.close()

        # Convert to list of dictionaries
        columns = ['time', 'pair', 'direction', 'confidence', 'created_at', 'entry_price', 'stop_loss', 'take_profit', 'result']
        return [dict(zip(columns, signal)) for signal in signals]
    except Exception as e:
        logger.error(f"Error retrieving signals: {str(e)}")
        return []

def get_signal_stats(user_id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT COUNT(*) FROM signals WHERE user_id = ?', (user_id,))
        total = cursor.fetchone()[0]
        
        cursor.execute('SELECT pair, COUNT(*) as count FROM signals WHERE user_id = ? GROUP BY pair', (user_id,))
        by_pair = cursor.fetchall()
        
        cursor.execute('SELECT direction, COUNT(*) as count FROM signals WHERE user_id = ? GROUP BY direction', (user_id,))
        by_direction = cursor.fetchall()
        
        cursor.close()
        return total, by_pair, by_direction
    except Exception as e:
        logger.error(f"Error getting signal stats: {str(e)}")
        return 0, [], []

# --- App logic ---
pairs = ["EURAUD", "USDCHF", "USDBRL", "AUDUSD", "GBPCAD", "EURCAD", "NZDUSD", "USDPKR", "EURUSD", "USDCAD", "AUDCHF", "GBPUSD", "EURGBP"]
brokers = ["Quotex", "Pocket Option", "Binolla", "IQ Option", "Bullex", "Exnova"]
otc_pairs = ["EURAUD_OTC", "USDCHF_OTC", "USDBRL_OTC", "AUDUSD_OTC", "GBPCAD_OTC", "EURCAD_OTC", "NZDUSD_OTC", "USDPKR_OTC", "EURUSD_OTC", "USDCAD_OTC", "AUDCHF_OTC", "GBPUSD_OTC", "EURGBP_OTC"]
otc_brokers = ["Quotex", "Pocket Option", "Binolla", "IQ Option", "Bullex", "Exnova"]

# Initialize price cache
price_cache = {}

# Angel One Scrip Master Data Loader
def load_angel_one_scrip_master():
    """Load Angel One scrip master data from official API"""
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        scrip_data = response.json()
        
        # Create symbol mapping from scrip master data
        symbol_map = {}
        for instrument in scrip_data:
            symbol = instrument.get('symbol', '')
            token = instrument.get('token', '')
            name = instrument.get('name', '')
            exchange = instrument.get('exch_seg', '')
            
            if symbol and token:
                # Use symbol as key, token as value
                symbol_map[symbol] = token
                
                # Also add name as key for better search
                if name and name != symbol:
                    symbol_map[name] = token
        
        logger.info(f"Loaded {len(symbol_map)} instruments from Angel One scrip master")
        return symbol_map
        
    except Exception as e:
        logger.error(f"Failed to load Angel One scrip master: {e}")
        return {}

# Load Angel One scrip master data
angel_one_symbols = load_angel_one_scrip_master()

# Angel One Official Symbol Mapping (Based on Scrip Master)
# Token codes from: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
symbol_map = {
    # Major Indices (NSE)
    "NIFTY50": "99926000",  # NIFTY 50
    "BANKNIFTY": "99926009",  # BANKNIFTY
    "FINNIFTY": "99926037",  # FINNIFTY
    "NIFTY100": "99926012",  # NIFTY 100
    "NIFTY200": "99926033",  # NIFTY 200
    "NIFTY500": "99926004",  # NIFTY 500
    "NIFTYNEXT50": "99926013",  # NIFTY NEXT 50
    "NIFTYMIDCAP50": "99926014",  # NIFTY MIDCAP 50
    "NIFTYMIDCAP100": "99926011",  # NIFTY MIDCAP 100
    "NIFTYSMLCAP100": "99926032",  # NIFTY SMALLCAP 100
    
    # Sector Indices (NSE)
    "NIFTYIT": "99926008",  # NIFTY IT
    "NIFTYREALTY": "99926018",  # NIFTY REALTY
    "NIFTYINFRA": "99926019",  # NIFTY INFRA
    "NIFTYENERGY": "99926020",  # NIFTY ENERGY
    "NIFTYFMCG": "99926021",  # NIFTY FMCG
    "NIFTYMNC": "99926022",  # NIFTY MNC
    "NIFTYPHARMA": "99926023",  # NIFTY PHARMA
    "NIFTYPSUBANK": "99926025",  # NIFTY PSU BANK
    "NIFTYSERVSECTOR": "99926026",  # NIFTY SERV SECTOR
    "NIFTYAUTO": "99926029",  # NIFTY AUTO
    "NIFTYMETAL": "99926030",  # NIFTY METAL
    "NIFTYMEDIA": "99926031",  # NIFTY MEDIA
    "NIFTYCOMMODITIES": "99926035",  # NIFTY COMMODITIES
    "NIFTYCONSUMPTION": "99926036",  # NIFTY CONSUMPTION
    
    # Volatility Index
    "INDIAVIX": "99926017",  # INDIA VIX
    
    # Popular Stocks (NSE) - Using Angel One tokens
    "RELIANCE": "2885633",  # RELIANCE
    "TCS": "2953217",  # TCS
    "HDFCBANK": "341249",  # HDFC BANK
    "INFY": "408065",  # INFOSYS
    "ICICIBANK": "1270529",  # ICICI BANK
    "HINDUNILVR": "356865",  # HINDUNILVR
    "SBIN": "779521",  # STATE BANK OF INDIA
    "BHARTIARTL": "2714625",  # BHARTI AIRTEL
    "KOTAKBANK": "492033",  # KOTAK MAHINDRA BANK
    "BAJFINANCE": "81153",  # BAJAJ FINANCE
    "LT": "2939649",  # LARSEN & TOUBRO
    "ITC": "424961",  # ITC
    "ASIANPAINT": "60417",  # ASIAN PAINTS
    "MARUTI": "2815745",  # MARUTI SUZUKI
    "WIPRO": "969473",  # WIPRO
    "NESTLEIND": "4598529",  # NESTLE INDIA
    "ULTRACEMCO": "2952193",  # ULTRATECH CEMENT
    "TITAN": "897537",  # TITAN COMPANY
    "POWERGRID": "3834369",  # POWER GRID CORP
    "NTPC": "2977281",  # NTPC
    "COALINDIA": "5215745",  # COAL INDIA
    "ONGC": "633601",  # ONGC
    "TECHM": "3465729",  # TECH MAHINDRA
    "SUNPHARMA": "857857",  # SUN PHARMA
    "DRREDDY": "225537",  # DR REDDY'S LAB
    "CIPLA": "2889473",  # CIPLA
    "AXISBANK": "1510401",  # AXIS BANK
    "INDUSINDBK": "1346049",  # INDUSIND BANK
    "HCLTECH": "1850625",  # HCL TECHNOLOGIES
    "TATAMOTORS": "884737",  # TATA MOTORS
    "TATASTEEL": "895745",  # TATA STEEL
    "GRASIM": "315393",  # GRASIM INDUSTRIES
    "ADANIPORTS": "3861249",  # ADANI PORTS
    "ADANIENT": "3860481",  # ADANI ENTERPRISES
    "ADANIGREEN": "3861505",  # ADANI GREEN ENERGY
    "ADANITRANS": "3861633",  # ADANI TRANSMISSION
    "ADANIPOWER": "3861377",  # ADANI POWER
    "ADANITOTAL": "3861761",  # ADANI TOTAL GAS
    "ADANIWILMAR": "3861889",  # ADANI WILMAR
    "ADANIONE": "3862017",  # ADANI ONE
    "JSWSTEEL": "1343489",  # JSW STEEL
    "BAJAJFINSV": "81153",  # BAJAJ FINSERV
    "BAJAJHLDNG": "81153",  # BAJAJ HOLDINGS
    "DIVISLAB": "2889473",  # DIVI'S LAB
    "EICHERMOT": "2815745",  # EICHER MOTORS
    "HEROMOTOCO": "356865",  # HERO MOTOCORP
    "M&M": "2815745",  # MAHINDRA & MAHINDRA
    "PIDILITIND": "4598529",  # PIDILITE INDUSTRIES
    "SHREECEM": "2952193",  # SHREE CEMENT
    "UPL": "897537",  # UPL
    "VEDL": "3834369",  # VEDANTA
    "ZEEL": "2977281",  # ZEE ENTERTAINMENT
    "BPCL": "5215745",  # BHARAT PETROLEUM
    "IOC": "633601",  # INDIAN OIL CORP
    "GAIL": "3465729",  # GAIL
    "PETRONET": "857857",  # PETRONET LNG
    "CONCOR": "225537",  # CONTAINER CORP
    "BHEL": "2889473",  # BHEL
    "SAIL": "1510401",  # SAIL
    "HINDALCO": "1346049",  # HINDALCO
    "JINDALSTEL": "1850625",  # JINDAL STEEL
    "TATACHEM": "884737",  # TATA CHEMICALS
    "TATACONSUM": "895745",  # TATA CONSUMER
    "TATAPOWER": "315393",  # TATA POWER
    "TATAELXSI": "3861249",  # TATA ELXSI
    "TATACOMM": "3860481",  # TATA COMMUNICATIONS
    "TATAMETALI": "3861505",  # TATA METALIKS
    "TATASPONGE": "3861633",  # TATA SPONGE
    "TATAINVEST": "3861377",  # TATA INVESTMENT
    "TATACOFFEE": "3861761",  # TATA COFFEE
    "TATAGLOBAL": "3861889"   # TATA GLOBAL
}

# Merge with dynamically loaded Angel One symbols
symbol_map.update(angel_one_symbols)

# Function to search for symbols in Angel One data
def search_angel_one_symbols(query, limit=20):
    """Search for symbols in Angel One scrip master data"""
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        scrip_data = response.json()
        results = []
        
        query_lower = query.lower()
        for instrument in scrip_data:
            symbol = instrument.get('symbol', '')
            name = instrument.get('name', '')
            token = instrument.get('token', '')
            exchange = instrument.get('exch_seg', '')
            
            if (query_lower in symbol.lower() or 
                query_lower in name.lower()) and token:
                results.append({
                    'symbol': symbol,
                    'name': name,
                    'token': token,
                    'exchange': exchange
                })
                
                if len(results) >= limit:
                    break
        
        return results
        
    except Exception as e:
        logger.error(f"Failed to search Angel One symbols: {e}")
        return []

# Broker configurations
broker_payouts = {
    # Forex brokers
    "Quotex": 0.85,
    "Pocket Option": 0.80,
    "Binolla": 0.78,
    "IQ Option": 0.82,
    "Bullex": 0.75,
    "Exnova": 0.77,
    
    # Indian brokers
    "Zerodha": 0.75,
    "Upstox": 0.78,
    "Angel One": 0.77,
    "Groww": 0.76,
    "ICICI Direct": 0.75,
    "HDFC Securities": 0.74
}

def get_forex_rate(pair, return_source=False):
    """Get real-time forex rate with support for premium API features."""
    return get_cached_realtime_forex(pair, return_source)

def black_scholes_call_put(S, K, T, r, sigma, option_type="call"):
    """
    Calculate option price using Black-Scholes model with NormalDist
    """
    d1 = (math.log(S/K) + (r + sigma**2/2)*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma*math.sqrt(T)

    # Use NormalDist for CDF calculations
    normal = NormalDist()
    if option_type.lower() == "call":
        price = S*normal.cdf(d1) - K*math.exp(-r*T)*normal.cdf(d2)
    else:  # put
        price = K*math.exp(-r*T)*normal.cdf(-d2) - S*normal.cdf(-d1)

    return price

DEMO_UNLOCK_PASSWORD = 'Indiandemo2021'
DEMO_TIMEOUT_MINUTES = 1440

@app.before_request
def demo_lockout():
    if 'user_id' in session:
        try:
            db = get_db()
            if db is None:
                logger.error("Database connection failed in demo_lockout")
                session.clear()
                return redirect(url_for('login'))
                
            cursor = db.cursor()
            cursor.execute('SELECT demo_end_time FROM users WHERE id = ?', (session['user_id'],))
            result = cursor.fetchone()
            cursor.close()
            
            if result and result[0]:  # demo_end_time is at index 0
                # SQLite returns strings, parse them
                demo_end_time = result[0]
                if isinstance(demo_end_time, str):
                    try:
                        # Parse the ISO format string
                        demo_end_datetime = datetime.fromisoformat(demo_end_time)
                        
                        if datetime.now() > demo_end_datetime:
                            logger.info(f"Demo period expired for user {session['user_id']}")
                            # Instead of immediately locking out, just log it
                            # The get_demo_time function will handle resetting it
                            pass
                    except ValueError as e:
                        logger.error(f"Invalid demo_end_time format: {str(e)}")
                        # Invalid date format, reset it
                        demo_end_time = datetime.now() + timedelta(days=30)
                        cursor = db.cursor()
                        cursor.execute('UPDATE users SET demo_end_time = ? WHERE id = ?', 
                                     (demo_end_time.isoformat(), session['user_id']))
                        db.commit()
                        cursor.close()
            else:
                # Set demo end time if not set
                demo_end_time = datetime.now() + timedelta(days=30)
                cursor = db.cursor()
                cursor.execute('UPDATE users SET demo_end_time = ? WHERE id = ?', 
                             (demo_end_time.isoformat(), session['user_id']))
                db.commit()
                cursor.close()
        except Exception as e:
            logger.error(f"Error in demo_lockout: {str(e)}", exc_info=True)
            session.clear()
            return redirect(url_for('login'))

@app.route('/lock', methods=['GET'])
def lock():
    return render_template('lock.html')

@app.route('/unlock', methods=['POST'])
def unlock():
    password = request.form.get('password')
    if password == DEMO_UNLOCK_PASSWORD:
        if 'user_id' in session:
            db = get_db()
            demo_end_time = (datetime.now() + timedelta(hours=24)).isoformat()
            cursor = db.cursor()
            cursor.execute('UPDATE users SET demo_end_time = ? WHERE id = ?',
                          (demo_end_time, session['user_id']))
            db.commit()
            cursor.close()
            session['demo_end_time'] = demo_end_time
        session['locked'] = False
        return redirect(url_for('dashboard'))
    else:
        flash('Incorrect password. Please try again.', 'error')
        return render_template('lock.html')

@app.route('/get_demo_time')
def get_demo_time():
    """Get remaining demo time"""
    try:
        logger.info(f"get_demo_time called. Session user_id: {session.get('user_id')}")
        
        if 'user_id' in session:
            user = get_user_by_id(session['user_id'])
            logger.info(f"User found: {user}")
            
            if user and not user['is_premium']:  # Use dictionary-like access for sqlite3.Row
                demo_end_time = user['demo_end_time']  # Use dictionary-like access
                logger.info(f"Demo end time from DB: {demo_end_time}")
                
                if not demo_end_time:
                    # Set demo end time if not set (24 hours from now)
                    demo_end_time = (datetime.now() + timedelta(hours=24)).isoformat()
                    db = get_db()
                    cursor = db.cursor()
                    cursor.execute('UPDATE users SET demo_end_time = ? WHERE id = ?',
                                  (demo_end_time, session['user_id']))
                    db.commit()
                    cursor.close()
                    session['demo_end_time'] = demo_end_time
                    logger.info(f"Set new demo end time: {demo_end_time}")
                else:
                    # Check if demo has expired
                    try:
                        demo_end_datetime = datetime.fromisoformat(demo_end_time)
                        if datetime.now() > demo_end_datetime:
                            # Demo expired, reset it for another 24 hours
                            demo_end_time = (datetime.now() + timedelta(hours=24)).isoformat()
                            db = get_db()
                            cursor = db.cursor()
                            cursor.execute('UPDATE users SET demo_end_time = ? WHERE id = ?',
                                          (demo_end_time, session['user_id']))
                            db.commit()
                            cursor.close()
                            session['demo_end_time'] = demo_end_time
                            logger.info(f"Demo time reset for user {session['user_id']}: {demo_end_time}")
                    except ValueError as e:
                        logger.error(f"Error parsing demo_end_time: {str(e)}")
                        # Invalid date format, reset it
                        demo_end_time = (datetime.now() + timedelta(hours=24)).isoformat()
                        db = get_db()
                        cursor = db.cursor()
                        cursor.execute('UPDATE users SET demo_end_time = ? WHERE id = ?',
                                      (demo_end_time, session['user_id']))
                        db.commit()
                        cursor.close()
                        session['demo_end_time'] = demo_end_time

                # Calculate remaining time
                remaining_time = datetime.fromisoformat(demo_end_time) - datetime.now()
                if remaining_time.total_seconds() < 0:
                    remaining_time = timedelta(0)

                logger.info(f"Remaining time: {remaining_time}")
                return jsonify({
                    'time_left': str(remaining_time),
                    'status': 'success'
                })
            else:
                logger.info(f"User is premium or not found: {user}")

        logger.warning("No active demo session found")
        return jsonify({
            'time_left': '00:00:00',
            'status': 'error',
            'message': 'No active demo session'
        }), 404

    except Exception as e:
        logger.error(f"Error getting demo time: {str(e)}", exc_info=True)
        return jsonify({
            'time_left': '00:00:00',
            'status': 'error',
            'message': str(e)
        }), 500

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        if not username or not password:
            flash("Please fill in all fields", "error")
            return redirect(url_for("register"))
            
        # Check if username already exists
        existing_user = get_user_by_username(username)
        if existing_user:
            flash("Username already exists", "error")
            return redirect(url_for("register"))
            
        # Create new user
        if create_user(username, password):
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))
        else:
            flash("Error creating user. Please try again.", "error")
            return redirect(url_for("register"))
            
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            # First check if database is accessible
            db = get_db()
            if db is None:
                flash("Database connection error. Please try again later.", "error")
                return render_template("login.html")

            username = request.form.get("username")
            password = request.form.get("password")

            if not username or not password:
                flash("Please fill in all fields", "error")
                return render_template("login.html")

            user = get_user_by_username(username)
            if not user:
                flash("Invalid username or password", "error")
                return render_template("login.html")

            try:
                # user is a sqlite3.Row object, use dictionary-like access
                if check_password_hash(user['password'], password):
                    session['user_id'] = user['id']  # user id
                    update_last_login(user['id'])
                    logger.info(f"User {user['id']} logged in successfully")
                    return redirect(url_for("dashboard"))
            except ValueError as e:
                logger.error(f"Password verification error: {str(e)}")
                flash("An error occurred during login. Please try again.", "error")
                return render_template("login.html")

            flash("Invalid username or password", "error")
            return render_template("login.html")
        except Exception as e:
            logger.error(f"Login error: {str(e)}", exc_info=True)
            flash("An unexpected error occurred. Please try again later.", "error")
            return render_template("login.html")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user = get_user_by_id(session["user_id"])
    if request.method == "POST":
        new_password = request.form["new_password"]
        if new_password:
            db = get_db()
            cursor = db.cursor()
            cursor.execute('UPDATE users SET password = ? WHERE id = ?', (generate_password_hash(new_password), user[0]))  # id is at index 0
            db.commit()
            cursor.close()
            flash("Password updated successfully.", "success")
    return render_template("profile.html", user=user)

@app.route("/dashboard")
def dashboard():
    logger.info("Accessing dashboard route")
    if 'user_id' not in session:
        logger.warning("No user_id in session, redirecting to login")
        return redirect(url_for('login'))
        
    try:
        logger.info(f"Attempting to connect to database for user_id: {session['user_id']}")
        connection = get_db()
        if connection is None:
            logger.error("Failed to establish database connection")
            session.clear()  # Clear the session since we can't access the database
            flash("Database connection error. Please try again later.", "error")
            return redirect(url_for('login'))
            
        cursor = connection.cursor()
        logger.info("Database cursor created successfully")
        
        # Get user data
        try:
            logger.info("Fetching user data")
            cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
            user_raw = cursor.fetchone()
            logger.info(f"User data fetched: {user_raw is not None}")
            
            if not user_raw:
                logger.warning(f"No user found for id: {session['user_id']}")
                session.clear()
                flash("User not found", "error")
                return redirect(url_for('login'))
            
            # Convert user data to dictionary for template
            user = {
                'id': user_raw[0],
                'username': user_raw[1],
                'password': user_raw[2],
                'registered_at': user_raw[3],
                'last_login': user_raw[4],
                'balance': user_raw[5],
                'is_premium': user_raw[6],
                'demo_end_time': user_raw[7]
            }
        except Exception as e:
            logger.error(f"Error fetching user data: {str(e)}")
            flash("Error loading user data", "error")
            return redirect(url_for('login'))
            
        # Initialize empty lists for data
        signals = []
        trades = []
        positions = []
        portfolio_history = []
        
        try:
            # Get user's portfolio history for chart
            logger.info("Fetching portfolio history")
            cursor.execute('''
                SELECT portfolio_value, timestamp FROM portfolio_history 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 30
            ''', (session['user_id'],))
            portfolio_raw = cursor.fetchall()
            logger.info(f"Fetched {len(portfolio_raw)} portfolio history records")
            
            # Convert to list of dictionaries for template
            if portfolio_raw:
                portfolio_history = [
                    {
                        'portfolio_value': float(row[0]),
                        'timestamp': row[1]
                    } for row in portfolio_raw
                ]
                logger.info(f"Created portfolio history with {len(portfolio_history)} records")
            else:
                # If no portfolio history exists, create some sample data
                logger.info("No portfolio history found, creating sample data")
                from datetime import datetime, timedelta
                base_value = float(user.get('balance', 100000))
                for i in range(7):  # Last 7 days
                    date = datetime.now() - timedelta(days=i)
                    value = base_value + (i * 1000) + (i % 2 * 500)  # Sample progression
                    portfolio_history.append({
                        'portfolio_value': value,
                        'timestamp': date.strftime('%Y-%m-%d %H:%M:%S')
                    })
                portfolio_history.reverse()  # Oldest first
                logger.info(f"Created sample portfolio history with {len(portfolio_history)} records")
        except Exception as e:
            logger.error(f"Error fetching portfolio history: {str(e)}")
            flash("Error loading portfolio history", "error")
            
        try:
            # Get user's signals
            logger.info("Fetching user signals")
            cursor.execute('''
                SELECT * FROM signals 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT 10
            ''', (session['user_id'],))
            signals_raw = cursor.fetchall()
            logger.info(f"Fetched {len(signals_raw)} signals")
            
            # Convert to list of dictionaries for template
            if signals_raw:
                columns = ['id', 'user_id', 'pair', 'direction', 'confidence', 'time', 'created_at', 'entry_price', 'stop_loss', 'take_profit', 'result']
                signals = [dict(zip(columns, signal)) for signal in signals_raw]
        except Exception as e:
            logger.error(f"Error fetching signals: {str(e)}")
            flash("Error loading trading signals", "error")
            
        try:
            # Get user's trades with date filtering
            logger.info("Fetching user trades")
            
            # Get date filters from request args
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            
            # Build query with date filtering
            query = '''
                SELECT * FROM trades 
                WHERE user_id = ?
            '''
            params = [session['user_id']]
            
            if start_date:
                query += ' AND DATE(entry_time) >= ?'
                params.append(start_date)
            
            if end_date:
                query += ' AND DATE(entry_time) <= ?'
                params.append(end_date)
            
            query += ' ORDER BY entry_time DESC LIMIT 50'
            
            cursor.execute(query, params)
            trades_raw = cursor.fetchall()
            logger.info(f"Fetched {len(trades_raw)} trades with date filter: {start_date} to {end_date}")
            
            # Convert to list of dictionaries for template
            if trades_raw:
                columns = ['id', 'user_id', 'symbol', 'direction', 'entry_price', 'exit_price', 'quantity', 'status', 'entry_time', 'exit_time', 'profit_loss', 'stop_loss', 'take_profit']
                trades = [dict(zip(columns, trade)) for trade in trades_raw]
        except Exception as e:
            logger.error(f"Error fetching trades: {str(e)}")
            flash("Error loading trade history", "error")
            
        try:
            # Get user's positions
            logger.info("Fetching user positions")
            cursor.execute('''
                SELECT * FROM positions 
                WHERE user_id = ?
            ''', (session['user_id'],))
            positions_raw = cursor.fetchall()
            logger.info(f"Fetched {len(positions_raw)} positions")
            
            # Convert to list of dictionaries for template
            if positions_raw:
                columns = ['id', 'user_id', 'symbol', 'quantity', 'average_price', 'last_updated']
                positions = [dict(zip(columns, position)) for position in positions_raw]
        except Exception as e:
            logger.error(f"Error fetching positions: {str(e)}")
            flash("Error loading current positions", "error")
            
        try:
            # Get user's portfolio history
            logger.info("Fetching portfolio history")
            cursor.execute('''
                SELECT * FROM portfolio_history 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 30
            ''', (session['user_id'],))
            portfolio_history_raw = cursor.fetchall()
            logger.info(f"Fetched {len(portfolio_history_raw)} portfolio history entries")
            
            # Convert to list of dictionaries for template
            if portfolio_history_raw:
                columns = ['id', 'user_id', 'portfolio_value', 'timestamp']
                portfolio_history = [dict(zip(columns, entry)) for entry in portfolio_history_raw]
        except Exception as e:
            logger.error(f"Error fetching portfolio history: {str(e)}")
            flash("Error loading portfolio history", "error")
            
        cursor.close()
        logger.info("Database cursor closed")
        
        logger.info("Rendering dashboard template")
        logger.info(f"Portfolio history data being passed: {portfolio_history}")
        logger.info(f"Portfolio history type: {type(portfolio_history)}")
        logger.info(f"Portfolio history length: {len(portfolio_history) if portfolio_history else 'None'}")
        
        # Get user registration date for default start date
        user_registration_date = user.get('registered_at', '2025-09-06') if user else '2025-09-06'
        if user_registration_date:
            user_registration_date = user_registration_date.split(' ')[0]  # Extract date part only
        
        return render_template(
            "dashboard.html",
            user=user,
            signals=signals,
            trades=trades,
            positions=positions,
            portfolio_history=portfolio_history,
            user_registration_date=user_registration_date,
            start_date=start_date,
            end_date=end_date
        )
        
    except Exception as e:
        logger.error(f"Unexpected error in dashboard: {str(e)}", exc_info=True)
        flash("An error occurred while loading the dashboard", "error")
        return redirect(url_for('login'))

@app.route("/", methods=["GET", "POST"])
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("indian_market"))

@app.route("/otc", methods=["GET", "POST"])
def otc_market():
    return abort(404)

@app.route("/download_otc")
def download_otc():
    return abort(404)

    # Get signals for the user
    signals = get_signals_for_user(session["user_id"])

    # Create PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []

    # Add company logo
    try:
        logo_path = os.path.join(STATIC_FOLDER, "images", "logo1.png")
        if os.path.exists(logo_path):
            img = Image(logo_path, width=200, height=50)
            elements.append(img)
    except Exception as e:
        logger.error(f"Error loading logo: {str(e)}")

    # Add title with styling
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        textColor=colors.HexColor('#00e6d0'),
        alignment=TA_CENTER
    )
    elements.append(Paragraph("KishanX Trading Signals", title_style))

    # Add report details
    details_style = ParagraphStyle(
        'DetailsStyle',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.HexColor('#666666'),
        alignment=TA_CENTER
    )
    elements.append(Paragraph(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", details_style))
    elements.append(Spacer(1, 20))

    # Add market overview section
    overview_style = ParagraphStyle(
        'OverviewStyle',
        parent=styles['Heading2'],
        fontSize=16,
        spaceAfter=10,
        textColor=colors.HexColor('#333333')
    )
    elements.append(Paragraph("Market Overview", overview_style))
    elements.append(Paragraph("OTC Market Trading Signals", styles['Normal']))
    elements.append(Spacer(1, 20))

    # Add signals table with enhanced styling
    if signals:
        # Table header with more columns
        data = [['Time', 'Pair', 'Direction', 'Entry Price', 'Stop Loss', 'Take Profit', 'Confidence', 'Status']]

        # Add signals with calculated levels
        for signal in signals:
            if signal['pair'].endswith('_OTC'):  # Only include OTC pairs
                try:
                    # Calculate entry, stop loss and take profit levels
                    entry_price = signal.get('entry_price', 'N/A')
                    stop_loss = signal.get('stop_loss', 'N/A')
                    take_profit = signal.get('take_profit', 'N/A')
                    confidence = signal.get('confidence', 0.0)
                    status = "Won" if signal.get('result') == 1 else "Lost" if signal.get('result') == 0 else "Pending"

                    # Format numerical values
                    entry_price_str = f"{float(entry_price):.5f}" if isinstance(entry_price, (int, float)) else str(entry_price)
                    stop_loss_str = f"{float(stop_loss):.5f}" if isinstance(stop_loss, (int, float)) else str(stop_loss)
                    take_profit_str = f"{float(take_profit):.5f}" if isinstance(take_profit, (int, float)) else str(take_profit)
                    confidence_str = f"{float(confidence):.1f}%"

                    data.append([
                        signal['time'],
                        signal['pair'].replace('_OTC', ''),
                        signal['direction'],
                        entry_price_str,
                        stop_loss_str,
                        take_profit_str,
                        confidence_str,
                        status
                    ])
                except Exception as e:
                    logger.error(f"Error processing signal: {str(e)}")
                    continue

        # Create table with enhanced styling
        if len(data) > 1:  # Only create table if we have data
            table = Table(data, colWidths=[80, 80, 80, 80, 80, 80, 80, 80])
            table.setStyle(TableStyle([
                # Header styling
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a1a')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#00e6d0')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),

                # Body styling
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#2a2a2a')),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.white),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#333333')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

                # Alternating row colors
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#2a2a2a'), colors.HexColor('#333333')])
            ]))
            elements.append(table)
        else:
            elements.append(Paragraph("No OTC signals available", styles['Normal']))
    else:
        elements.append(Paragraph("No signals available", styles['Normal']))

    # Add trading guidelines
    elements.append(Spacer(1, 30))
    guidelines_style = ParagraphStyle(
        'GuidelinesStyle',
        parent=styles['Heading2'],
        fontSize=16,
        spaceAfter=10,
        textColor=colors.HexColor('#333333')
    )
    elements.append(Paragraph("Trading Guidelines", guidelines_style))

    guidelines = [
        "• Always use proper risk management",
        "• Set stop-loss and take-profit levels before entering trades",
        "• Monitor market conditions before executing trades",
        "• Keep track of your trading performance",
        "• Follow the signals strictly as provided"
    ]

    for guideline in guidelines:
        elements.append(Paragraph(guideline, styles['Normal']))

    # Add disclaimer and company information
    elements.append(Spacer(1, 30))
    footer_style = ParagraphStyle(
        'FooterStyle',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#666666'),
        alignment=TA_CENTER
    )

    # Add digital signature
    signature_style = ParagraphStyle(
        'SignatureStyle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#00e6d0'),
        alignment=TA_CENTER
    )
    elements.append(Paragraph("Digitally Signed by KishanX Trading System", signature_style))
    elements.append(Paragraph("Signature Hash: " + hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:16], signature_style))

    # Add company information
    company_info = [
        "KishanX Trading Signals",
        "Email: support@kishanx.com",
        "Website: www.kishanx.com",
        "Disclaimer: This is a demo version. Trading signals are for educational purposes only."
    ]

    for info in company_info:
        elements.append(Paragraph(info, footer_style))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"kishanx_trading_signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    )

@app.route("/download_indian")
def download_indian():
    if "indian_signals" not in session:
        return redirect(url_for("indian_market"))
    signals = session["indian_signals"]
    from io import BytesIO
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 40, "KishanX Indian Signals Report")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 60, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    table_data = [["Time", "Pair", "Direction", "Call Price", "Put Price"]]
    for s in signals:
        # Forex removed; skip live pricing to avoid dependency
        S = None
        K = S
        T = 1/365
        r = 0.01
        sigma = 0.2
        call_price = black_scholes_call_put(S, K, T, r, sigma, option_type="call") if S else "N/A"
        put_price = black_scholes_call_put(S, K, T, r, sigma, option_type="put") if S else "N/A"
        table_data.append([s["time"], s["pair"], s["direction"], f"{call_price:.6f}" if S else "N/A", f"{put_price:.6f}" if S else "N/A"])
    x = 40
    y = height - 100
    row_height = 18
    col_widths = [60, 70, 70, 100, 100]
    c.setFont("Helvetica-Bold", 11)
    for col, header in enumerate(table_data[0]):
        c.drawString(x + sum(col_widths[:col]), y, header)
    c.setFont("Helvetica", 10)
    y -= row_height
    for row in table_data[1:]:
        for col, cell in enumerate(row):
            c.drawString(x + sum(col_widths[:col]), y, str(cell))
        y -= row_height
        if y < 60:
            c.showPage()
            y = height - 60
    c.save()
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name="kishan_indian_signals.pdf")

def calculate_technical_indicators(data):
    """Calculate technical indicators for the given data"""
    try:
        # Convert data to pandas DataFrame if it's not already
        if not isinstance(data, pd.DataFrame):
            df = pd.DataFrame(data)
        else:
            df = data

        # Ensure we have the required columns
        required_columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_columns:
            if col not in df.columns:
                logger.error(f"Missing required column: {col}")
                return {
                    'sma': [],
                    'ema': [],
                    'macd': [],
                    'macd_signal': [],
                    'rsi': [],
                    'bollinger_upper': [],
                    'bollinger_lower': [],
                    'cci': [],
                    'volume_ratio': []
                }

        # Calculate indicators using pandas
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()

        # MACD
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

        # RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # Bollinger Bands
        df['BB_middle'] = df['Close'].rolling(window=20).mean()
        df['BB_std'] = df['Close'].rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + (df['BB_std'] * 2)
        df['BB_lower'] = df['BB_middle'] - (df['BB_std'] * 2)

        # CCI (Commodity Channel Index)
        tp = (df['High'] + df['Low'] + df['Close']) / 3
        df['CCI'] = (tp - tp.rolling(window=20).mean()) / (0.015 * tp.rolling(window=20).std())

        # Volume Analysis
        df['Volume_SMA'] = df['Volume'].rolling(window=20).mean()
        df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA']

        # Fill NaN values with 0
        df = df.fillna(0)

        return {
            'sma': df['SMA_20'].tolist(),
            'ema': df['EMA_20'].tolist(),
            'macd': df['MACD'].tolist(),
            'macd_signal': df['Signal'].tolist(),
            'rsi': df['RSI'].tolist(),
            'bollinger_upper': df['BB_upper'].tolist(),
            'bollinger_lower': df['BB_lower'].tolist(),
            'cci': df['CCI'].tolist(),
            'volume_ratio': df['Volume_Ratio'].tolist()
        }
    except Exception as e:
        logger.error(f"Error calculating indicators: {str(e)}")
        # Return empty lists for all indicators in case of error
        return {
            'sma': [],
            'ema': [],
            'macd': [],
            'macd_signal': [],
            'rsi': [],
            'bollinger_upper': [],
            'bollinger_lower': [],
            'cci': [],
            'volume_ratio': []
        }

def get_historical_data(symbol, period=None, interval=None):
    """Fetch historical market data and calculate technical indicators"""
    try:
        # Clean up the symbol
        symbol = symbol.replace('/', '')
        logger.info(f"Processing symbol: {symbol}")

        # Get Yahoo Finance symbol
        yahoo_symbol = symbol_map.get(symbol, symbol)
        if not yahoo_symbol:
            logger.error(f"Invalid symbol: {symbol}")
            return {
                'historical': None,
                'realtime': None,
                'error': f"Invalid symbol: {symbol}"
            }
        logger.info(f"Using Yahoo Finance symbol: {yahoo_symbol}")

        # For Indian markets, use .NS suffix, for forex use =X suffix
        if symbol in ["NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", 
                      "NIFTYREALTY", "NIFTYPVTBANK", "NIFTYPSUBANK", "NIFTYFIN", "NIFTYMEDIA",
                      "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", 
                      "SBIN", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE"]:
            # Indian market symbols - use the mapped symbol directly (already has correct suffix)
            logger.info(f"Using Indian market symbol: {yahoo_symbol}")
        elif not yahoo_symbol.endswith('=X'):
            # Forex pairs - add =X suffix if not already present
            yahoo_symbol = f"{yahoo_symbol}=X"
            logger.info(f"Updated Yahoo Finance symbol for forex: {yahoo_symbol}")

        # Try to get data from Yahoo Finance
        df = None
        error_msg = []

        try:
            logger.info(f"Attempting to fetch data for {yahoo_symbol}")
            df = yf.download(yahoo_symbol, period=period or '1mo', interval=interval or '1d', progress=False)
            if df.empty:
                error_msg.append("No data available from Yahoo Finance")
                logger.warning("No data available from Yahoo Finance")
        except Exception as e:
            error_msg.append(f"Error fetching from Yahoo Finance: {str(e)}")
            logger.error(f"Error fetching from Yahoo Finance: {str(e)}")

        # If Yahoo Finance fails, try to get data from Alpha Vantage (only for forex pairs)
        if (df is None or df.empty) and symbol not in ["NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", 
                                                       "NIFTYREALTY", "NIFTYPVTBANK", "NIFTYPSUBANK", "NIFTYFIN", "NIFTYMEDIA",
                                                       "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", 
                                                       "SBIN", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE"]:
            try:
                logger.info(f"Attempting to fetch data from Alpha Vantage for {symbol}")
                # Get real-time rate
                rate, source = get_cached_realtime_forex(symbol, return_source=True)
                if rate is not None:
                    # Create a simple DataFrame with the current rate
                    now = datetime.now()
                    dates = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(30)]
                    df = pd.DataFrame({
                        'Open': [rate] * 30,
                        'High': [rate * 1.001] * 30,  # Slight variation
                        'Low': [rate * 0.999] * 30,   # Slight variation
                        'Close': [rate] * 30,
                        'Volume': [1000] * 30
                    }, index=pd.to_datetime(dates))
                    logger.info("Created fallback data from Alpha Vantage rate")
            except Exception as e:
                error_msg.append(f"Error fetching from Alpha Vantage: {str(e)}")
                logger.error(f"Error fetching from Alpha Vantage: {str(e)}")

        if df is None or df.empty:
            error_message = f"No data available for {symbol}: {'; '.join(error_msg)}"
            logger.error(error_message)
            return {
                'historical': None,
                'realtime': None,
                'error': error_message
            }

        # Check for required columns
        required_columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_columns:
            if col not in df.columns:
                logger.error(f"Missing required column: {col}")
                return {
                    'historical': None,
                    'realtime': None,
                    'error': f"Missing required column: {col}"
                }

        # Calculate technical indicators
        try:
            logger.info("Calculating technical indicators")
            df['SMA20'] = df['Close'].rolling(window=20).mean()
            df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
            df['RSI'] = calculate_rsi(df['Close'])
            df['MACD'], df['Signal'] = calculate_macd(df['Close'])
            logger.info("Technical indicators calculated successfully")
        except Exception as e:
            logger.error(f"Error calculating technical indicators: {str(e)}")
            return {
                'historical': None,
                'realtime': None,
                'error': f"Error calculating indicators: {str(e)}"
            }

        # Format data for response
        try:
            dates = df.index.strftime('%Y-%m-%d %H:%M' if interval and 'm' in interval else '%Y-%m-%d').tolist()
            logger.info(f"Formatted {len(dates)} dates")

            # Convert NaN values to None (null in JSON)
            def convert_nan_to_none(obj):
                if isinstance(obj, float) and np.isnan(obj):
                    return None
                elif isinstance(obj, list):
                    return [convert_nan_to_none(item) for item in obj]
                elif isinstance(obj, dict):
                    return {key: convert_nan_to_none(value) for key, value in obj.items()}
                return obj

            response_data = {
                'historical': {
                    'dates': dates,
                    'prices': {
                        'open': df['Open'].tolist(),
                        'high': df['High'].tolist(),
                        'low': df['Low'].tolist(),
                        'close': df['Close'].tolist(),
                        'volume': df['Volume'].tolist()
                    },
                    'indicators': {
                        'sma': df['SMA20'].tolist(),
                        'ema': df['EMA20'].tolist(),
                        'rsi': df['RSI'].tolist(),
                        'macd': df['MACD'].tolist(),
                        'macd_signal': df['Signal'].tolist()
                    }
                }
            }
            # Convert NaN values to None before returning
            response_data = convert_nan_to_none(response_data)
            logger.info("Successfully formatted response data")
            return response_data

        except Exception as e:
            logger.error(f"Error formatting response data: {str(e)}")
            return {
                'historical': None,
                'realtime': None,
                'error': f"Error formatting data: {str(e)}"
            }

    except Exception as e:
        logger.error(f"Unexpected error in get_historical_data for {symbol}: {str(e)}")
        return {
            'historical': None,
            'realtime': None,
            'error': str(e)
        }

def calculate_rsi(prices, period=14):
    """Calculate RSI for a price series"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD for a price series"""
    exp1 = prices.ewm(span=fast, adjust=False).mean()
    exp2 = prices.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

@app.route("/market_data/<symbol>")
def market_data(symbol):
    """API endpoint to get market data for a symbol"""
    if 'user_id' not in session:
        logger.warning("Unauthenticated request to market_data endpoint")
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        timeframe = request.args.get('timeframe', '1mo')
        logger.info(f"Fetching data for {symbol} with timeframe {timeframe}")

        # Clean up the symbol
        clean_symbol = symbol.replace('/', '')
        logger.info(f"Cleaned symbol: {clean_symbol}")

        data = get_historical_data(clean_symbol, period=timeframe)
        logger.info(f"Received data from get_historical_data: {data is not None}")

        if not data:
            logger.error("No data returned from get_historical_data")
            return jsonify({'error': 'No data available'}), 404

        if data.get('error'):
            logger.error(f"Error in data: {data['error']}")
            return jsonify({'error': data['error']}), 500

        if not data.get('historical'):
            logger.error("No historical data in response")
            return jsonify({'error': 'Incomplete data received'}), 500

        logger.info("Successfully returning market data")
        return jsonify(data)

    except Exception as e:
        logger.error(f"Error in market_data endpoint: {str(e)}")
        return jsonify({'error': str(e)}), 500

def get_trading_signals(symbol: str) -> Dict:
    """Get trading signals for a symbol"""
    try:
        # Since trading_system is commented out, return sample data
        # TODO: Re-enable when trading_system is properly imported
        return {
            'type': 'NEUTRAL',
            'confidence': 50.0,
            'timestamp': datetime.now().isoformat(),
            'note': 'Trading system temporarily disabled'
        }
    except Exception as e:
        logger.error(f"Error getting trading signals for {symbol}: {str(e)}")
        return {
            'type': 'NEUTRAL',
            'confidence': 0,
            'timestamp': datetime.now().isoformat()
        }

@app.route('/market')
def market_dashboard():
    symbols = load_symbols()
    return render_template(
        'market_dashboard.html',
        subscribed_symbols=[{'symbol': s} for s in symbols],
        signals={}
    )

# Add WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection with authentication"""
    if 'user_id' not in session:
        logger.warning("Unauthenticated WebSocket connection attempt")
        return False

    try:
        user = get_user_by_id(session['user_id'])
        if not user:
            logger.warning(f"Invalid user_id in session: {session['user_id']}")
            return False

        logger.info(f"User {user['username']} connected via WebSocket")
        return True

    except Exception as e:
        logger.error(f"Error in WebSocket connection: {str(e)}")
        return False

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info(f"Client disconnected: {request.sid}")
    # Remove any active subscriptions for this client
    for pair in list(active_subscriptions.keys()):
        if request.sid in active_subscriptions[pair]:
            active_subscriptions[pair].remove(request.sid)
        if not active_subscriptions[pair]:
            del active_subscriptions[pair]

@socketio.on('subscribe')
def handle_subscribe(data):
    """Handle subscription requests with improved error handling"""
    try:
        if 'user_id' not in session:
            logger.warning("Unauthenticated subscription attempt")
            return False

        pair = data.get('pair')
        if not pair:
            logger.warning("No pair provided for subscription")
            return False

        # Clean up the pair
        pair = pair.replace('/', '')

        # Add to active subscriptions
        user_id = session['user_id']
        if user_id not in active_subscriptions:
            active_subscriptions[user_id] = set()
        active_subscriptions[user_id].add(pair)

        # Get initial data
        try:
            if pair.endswith('_OTC'):
                # Handle OTC pairs
                if otc_handler:
                    price, source = otc_handler.get_realtime_price(pair, return_source=True)
                    if price is not None:
                        emit('price_update', {
                            'rate': float(price),
                            'source': source,
                            'type': 'otc_update',
                            'pair': pair,
                            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
            else:
                # Handle regular forex pairs
                price_data = get_cached_realtime_forex(pair, return_source=True)
                if price_data:
                    if isinstance(price_data, tuple):
                        price, source = price_data
                    else:
                        price = price_data
                        source = 'Real-time'

                    # Calculate option prices
                    volatility = 0.20
                    expiry = 1/365.0
                    risk_free_rate = 0.01

                    try:
                        call_price = black_scholes_call_put(price, price, expiry, risk_free_rate, volatility, "call")
                        put_price = black_scholes_call_put(price, price, expiry, risk_free_rate, volatility, "put")
                    except Exception as e:
                        logger.error(f"Error calculating option prices: {str(e)}")
                        call_price = None
                        put_price = None

                    emit('price_update', {
                        'rate': float(price),
                        'source': source,
                        'type': 'forex_update',
                        'pair': pair,
                        'call_price': call_price,
                        'put_price': put_price,
                        'volatility': volatility,
                        'expiry': expiry,
                        'risk_free_rate': risk_free_rate,
                        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })
        except Exception as e:
            logger.error(f"Error getting initial price data for {pair}: {str(e)}")
            emit('price_update', {
                'error': str(e),
                'pair': pair,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

        # Start sending updates
        def send_updates():
            while user_id in active_subscriptions and pair in active_subscriptions[user_id]:
                try:
                    if pair.endswith('_OTC'):
                        # Handle OTC pairs
                        if otc_handler:
                            price, source = otc_handler.get_realtime_price(pair, return_source=True)
                            if price is not None:
                                emit('price_update', {
                                    'rate': float(price),
                                    'source': source,
                                    'type': 'otc_update',
                                    'pair': pair,
                                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                })
                    else:
                        # Handle regular forex pairs
                        price_data = get_cached_realtime_forex(pair, return_source=True)
                        if price_data:
                            if isinstance(price_data, tuple):
                                price, source = price_data
                            else:
                                price = price_data
                                source = 'Real-time'

                            # Calculate option prices
                            volatility = 0.20
                            expiry = 1/365.0
                            risk_free_rate = 0.01

                            try:
                                call_price = black_scholes_call_put(price, price, expiry, risk_free_rate, volatility, "call")
                                put_price = black_scholes_call_put(price, price, expiry, risk_free_rate, volatility, "put")
                            except Exception as e:
                                logger.error(f"Error calculating option prices: {str(e)}")
                                call_price = None
                                put_price = None

                            emit('price_update', {
                                'rate': float(price),
                                'source': source,
                                'type': 'forex_update',
                                'pair': pair,
                                'call_price': call_price,
                                'put_price': put_price,
                                'volatility': volatility,
                                'expiry': expiry,
                                'risk_free_rate': risk_free_rate,
                                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            })

                except Exception as e:
                    logger.error(f"Error sending price update for {pair}: {str(e)}")
                    emit('price_update', {
                        'error': str(e),
                        'pair': pair,
                        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })

                time.sleep(1)  # Update every second

        # Start update thread
        threading.Thread(target=send_updates, daemon=True).start()
        logger.info(f"Started price updates for {pair} for user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Error handling subscription: {str(e)}")
        return False

@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    """Handle unsubscribe requests"""
    try:
        if 'user_id' not in session:
            logger.warning("Unauthenticated unsubscribe attempt")
            return False

        pair = data.get('pair')
        if not pair:
            logger.warning("No pair provided for unsubscribe")
            return False

        # Clean up the pair
        pair = pair.replace('/', '')

        # Remove from active subscriptions
        user_id = session['user_id']
        if user_id in active_subscriptions:
            active_subscriptions[user_id].discard(pair)
            logger.info(f"Unsubscribed {user_id} from {pair}")
            return True

        return False

    except Exception as e:
        logger.error(f"Error handling unsubscription: {str(e)}")
        return False

def send_price_updates(pair):
    """Send price updates to subscribed clients"""
    try:
        # Get all users subscribed to this pair
        subscribed_users = set()
        for user_id, subscriptions in active_subscriptions.items():
            if pair in subscriptions:
                subscribed_users.add(user_id)

        if not subscribed_users:
            return

        # Get latest price data
        if '_OTC' in pair:
            # Handle OTC pairs
            if otc_handler is None:
                logger.error("OTC handler not available - check API key configuration")
                return
            price_data = otc_handler.get_realtime_price(pair, return_source=True)
        else:
            # Handle regular forex pairs
            try:
                # Remove '/' from pair name if present (e.g., "EUR/USD" -> "EURUSD")
                clean_pair = pair.replace('/', '')
                price_data = get_cached_realtime_forex(clean_pair, return_source=True)
                logger.info(f"Forex price data for {clean_pair}: {price_data}")
            except Exception as e:
                logger.error(f"Error getting forex rate for {pair}: {str(e)}")
                return

        if isinstance(price_data, tuple):
            rate, source = price_data
        else:
            rate = price_data
            source = "API"

        if rate is not None:
            # Calculate option prices
            volatility = 0.20
            expiry = 1/365.0
            risk_free_rate = 0.01

            try:
                call_price = black_scholes_call_put(rate, rate, expiry, risk_free_rate, volatility, option_type="call")
                put_price = black_scholes_call_put(rate, rate, expiry, risk_free_rate, volatility, option_type="put")
            except Exception as e:
                logger.error(f"Error calculating option prices: {str(e)}")
                call_price = None
                put_price = None

            # Prepare update data
            update_data = {
                'data': {
                    'rate': float(rate),
                    'source': source,
                    'type': 'forex_update',
                    'pair': pair,
                    'call_price': call_price,
                    'put_price': put_price,
                    'volatility': volatility,
                    'expiry': expiry,
                    'risk_free_rate': risk_free_rate,
                    'timestamp': datetime.now().isoformat()
                }
            }

            logger.info(f"Sending price update for {pair}: {update_data}")

            # Emit update to all subscribed users
            for user_id in subscribed_users:
                try:
                    emit('price_update', update_data, room=user_id)
                except Exception as e:
                    logger.error(f"Error sending update to user {user_id}: {str(e)}")
                    # Remove problematic subscription
                    if user_id in active_subscriptions:
                        active_subscriptions[user_id].discard(pair)

    except Exception as e:
        logger.error(f"Error in send_price_updates: {str(e)}")

def start_price_update_thread():
    """Start background thread for price updates"""
    def update_loop():
        last_update = {}  # Track last update time for each pair
        while True:
            try:
                # Update all active subscriptions
                current_time = time.time()
                for pair in list(active_subscriptions.keys()):
                    if not active_subscriptions[pair]:
                        continue

                    # Check if we need to update this pair
                    if pair in last_update:
                        time_since_last_update = current_time - last_update[pair]
                        if time_since_last_update < 1.0:  # Update every second
                            continue

                    # Get and send price update
                    send_price_updates(pair)
                    last_update[pair] = current_time

                # Sleep for a short time to prevent high CPU usage
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error in update loop: {str(e)}")
                time.sleep(1)

    thread = threading.Thread(target=update_loop, daemon=True)
    thread.start()
    logger.info("Price update thread started")

# Start the update thread when the app starts
@app.before_first_request
def before_first_request():
    start_price_update_thread()
    # Ensure tables and autostart
    ensure_persistence_tables()
    try:
        prior = get_setting('indian_auto_trader_running', '0')
        if prior == '1':
            logger.info('Autostarting Indian auto-trader (previously running)')
            try:
                indian_auto_trader.start()
            except Exception as e:
                logger.error(f'Autostart failed: {e}')
            # Rehydrate active trades from DB (if any)
            try:
                import time as _t
                from datetime import datetime as _dt
                conn = get_db()
                cur = conn.cursor()
                cur.execute('SELECT id, symbol, type, entry_price, quantity, entry_time, user_id, strategy, confidence FROM active_trades')
                rows = cur.fetchall()
                conn.close()
                for r in rows:
                    trade_id = r['id']
                    entry_price = float(r['entry_price'] or 0)
                    direction = r['type']
                    # simple defaults for monitoring
                    if direction == 'BUY':
                        target_price = entry_price * 1.02
                        stop_loss = entry_price * 0.98
                    else:
                        target_price = entry_price * 0.98
                        stop_loss = entry_price * 1.02
                    try:
                        entry_epoch = _t.mktime(_dt.strptime(r['entry_time'], '%Y-%m-%d %H:%M:%S').timetuple()) if r['entry_time'] else _t.time()
                    except Exception:
                        entry_epoch = _t.time()
                    indian_auto_trader.active_trades[trade_id] = {
                        'id': trade_id,
                        'symbol': r['symbol'],
                        'type': direction,
                        'entry_price': entry_price,
                        'target_price': target_price,
                        'stop_loss': stop_loss,
                        'quantity': int(r['quantity'] or 1),
                        'strategy': r['strategy'],
                        'confidence': float(r['confidence'] or 0),
                        'timestamp': _dt.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'entry_epoch': entry_epoch,
                        'entry_time': r['entry_time'],
                        'status': 'ACTIVE'
                    }
                if rows:
                    logger.info(f'Rehydrated {len(rows)} active trade(s) from DB')
            except Exception as e:
                logger.error(f'Failed to rehydrate active trades: {e}')
    except Exception as e:
        logger.error(f'Autostart check error: {e}')

@app.route('/favicon.ico')
def favicon():
    try:
        return send_from_directory(
            os.path.join(app.root_path, 'static'),
            'favicon.ico',
            mimetype='image/vnd.microsoft.icon'
        )
    except Exception as e:
        logger.error(f"Error serving favicon: {e}")
        return '', 204  # Return no content if favicon is not found

# --- Add these two routes for health check and test page ---
@app.route('/test')
def test():
    return render_template('test.html')
# -----------------------------------------------------------

# Simulation control endpoints (temporary)
@app.route('/simulation/on')
def simulation_on():
    return jsonify({"message": "Simulation endpoints are disabled"}), 403

@app.route('/simulation/off')
def simulation_off():
    return jsonify({"message": "Simulation endpoints are disabled"}), 403

# Admin seed endpoint to insert today's mock closed trades and update portfolio
@app.route('/admin/seed_sim', methods=['POST', 'GET'])
def admin_seed_sim():
    return jsonify({"message": "Seeding is disabled"}), 403

@app.route("/start_indian_auto_trading")
def start_indian_auto_trading():
    """Start Indian auto-trading system"""
    if not session.get("user_id"):
        return jsonify({"error": "Not authenticated"}), 401
        
    try:
        enable_autotrade = os.getenv('INDIAN_AUTOTRADE_ENABLED', '0') == '1'
        if enable_autotrade:
            indian_auto_trader.start()
            logger.info("Indian auto-trading system started successfully")
        else:
            logger.info("Indian auto-trading is disabled by INDIAN_AUTOTRADE_ENABLED=0")
        set_setting('indian_auto_trader_running', '1')
        return jsonify({"message": "Indian auto-trading started successfully", "status": "running"})
    except Exception as e:
        logger.error(f"Error starting Indian auto-trading: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/stop_indian_auto_trading")
def stop_indian_auto_trading():
    """Stop Indian auto-trading system"""
    if not session.get("user_id"):
        return jsonify({"error": "Not authenticated"}), 401
        
    try:
        indian_auto_trader.stop()
        set_setting('indian_auto_trader_running', '0')
        return jsonify({"message": "Indian auto-trading stopped successfully", "status": "stopped"})
    except Exception as e:
        logger.error(f"Error stopping Indian auto-trading: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/indian_status")
def get_indian_status():
    """Get Indian auto-trading system status"""
    if not session.get("user_id"):
        return jsonify({"error": "Not authenticated"}), 401
        
    try:
        active_trades = indian_auto_trader.get_active_trades()
        performance = indian_auto_trader.get_performance_summary()
        total_pnl = getattr(indian_auto_trader, 'total_pnl', 0.0)
        win_rate = 0.0
        if hasattr(indian_auto_trader, 'trade_history') and len(indian_auto_trader.trade_history) > 0:
            wins = sum(1 for t in indian_auto_trader.trade_history if t.get('pnl_amount', 0) > 0)
            win_rate = (wins / len(indian_auto_trader.trade_history)) * 100.0

        # Get actual status from performance summary
        actual_status = performance.get('status', 'stopped').lower()
        
        return jsonify({
            "status": actual_status,
            "active_trades": len(active_trades),
            "performance": performance,
            "total_pnl": round(float(total_pnl), 2),
            "win_rate": round(float(win_rate), 2),
            "simulation": get_setting('simulation_mode', '0') == '1'
        })
        
    except Exception as e:
        logger.error(f"Error getting Indian status: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/indian_signals")
def get_indian_signals():
    """Get current Indian trading signals"""
    if not session.get("user_id"):
        return jsonify({"error": "Not authenticated"}), 401
        
    try:
        signals = indian_trading_system.get_all_signals()
        signal_data = []
        
        for signal in signals:
            signal_data.append({
                'symbol': signal.symbol,
                'signal_type': signal.signal_type,
                'confidence': signal.confidence,
                'entry_price': signal.entry_price,
                'target_price': signal.target_price,
                'stop_loss': signal.stop_loss,
                'strategy': signal.strategy,
                'risk_reward_ratio': signal.risk_reward_ratio,
                'market_sentiment': signal.market_sentiment,
                'volume_analysis': signal.volume_analysis,
                'volatility': signal.volatility,
                'timestamp': signal.timestamp.isoformat()
            })
            
        return jsonify({"signals": signal_data, "count": len(signal_data)})
        
    except Exception as e:
        logger.error(f"Error getting Indian signals: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/indian/enhanced_signals")
def get_enhanced_signals():
    """Get enhanced trading signals using comprehensive market data"""
    try:
        if not session.get("user_id"):
            return jsonify({"error": "Not authenticated"}), 401
        
        global angel_one_client
        
        if not angel_one_client or not angel_one_client.is_connected:
            return jsonify({"error": "Angel One not connected"}), 500
        
        # Get comprehensive market data for multiple symbols
        symbols_to_fetch = {
            "NSE": [
                "3045",    # SBIN
                "99992000", # BANKNIFTY
                "11536",    # INFY
                "2885",     # RELIANCE
                "2951",     # TCS
                "1333",     # HDFCBANK
                "496",      # ICICIBANK
                "319",      # BHARTIARTL
                "1922",     # KOTAKBANK
                "317"       # BAJFINANCE
            ]
        }
        
        logger.info("Fetching comprehensive market data for enhanced signals...")
        market_data = angel_one_client.fetch_market_data_direct(symbols_to_fetch)
        
        if not market_data or not market_data.get('status'):
            return jsonify({"error": "Failed to fetch market data"}), 500
        
        # Process signals using enhanced logic
        enhanced_signals = []
        
        for token_data in market_data['data']['fetched']:
            try:
                # Map token to symbol name
                symbol_token = token_data.get('symboltoken')
                symbol_name = None
                for name, token in {
                    'SBIN': '3045', 'BANKNIFTY': '99992000', 'INFY': '11536',
                    'RELIANCE': '2885', 'TCS': '2951', 'HDFCBANK': '1333',
                    'ICICIBANK': '496', 'BHARTIARTL': '319', 'KOTAKBANK': '1922',
                    'BAJFINANCE': '317'
                }.items():
                    if token == symbol_token:
                        symbol_name = name
                        break
                
                if not symbol_name:
                    continue
                
                # Generate enhanced signal using the comprehensive data
                signal_analysis = angel_one_client.generate_enhanced_signal(token_data)
                
                # Only return high-confidence signals for profitability
                if signal_analysis['signal'] in ['BUY', 'SELL'] and signal_analysis['confidence'] > 0.75:
                    # Calculate enhanced risk-reward
                    base_risk = 0.02
                    volatility_adjustment = 1.0 + signal_analysis['volatility_score'] * 2
                    confidence_adjustment = 1.0 + (signal_analysis['confidence'] - 0.75) * 0.5
                    final_risk = max(0.01, min(0.05, base_risk * volatility_adjustment * confidence_adjustment))
                    
                    entry_price = signal_analysis['entry_price']
                    if signal_analysis['signal'] == 'BUY':
                        stop_loss = entry_price * (1 - final_risk)
                        take_profit = entry_price * (1 + final_risk * 2.5)
                    else:
                        stop_loss = entry_price * (1 + final_risk)
                        take_profit = entry_price * (1 - final_risk * 2.5)
                    
                    enhanced_signals.append({
                        "symbol": symbol_name,
                        "signal_type": signal_analysis['signal'],
                        "confidence": round(signal_analysis['confidence'], 2),
                        "entry_price": round(entry_price, 2),
                        "stop_loss": round(stop_loss, 2),
                        "take_profit": round(take_profit, 2),
                        "risk_reward_ratio": 2.5,
                        "volume_score": round(signal_analysis['volume_score'], 2),
                        "volatility_score": round(signal_analysis['volatility_score'], 2),
                        "trend_strength": round(signal_analysis['trend_strength'], 2),
                        "momentum_score": round(signal_analysis['momentum_score'], 2),
                        "position_in_52w_range": round(signal_analysis['position_in_52w_range'], 2),
                        "change_percent": round(signal_analysis['change_percent'], 2),
                        "volume": signal_analysis['volume'],
                        "52w_high": round(signal_analysis['52w_high'], 2),
                        "52w_low": round(signal_analysis['52w_low'], 2),
                        "timestamp": signal_analysis['timestamp']
                    })
                
            except Exception as e:
                logger.error(f"Error processing enhanced signal for token {token_data.get('symboltoken')}: {e}")
                continue
        
        return jsonify({
            "status": "success",
            "signals": enhanced_signals,
            "total_signals": len(enhanced_signals),
            "data_source": "angel_one_comprehensive",
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error getting enhanced signals: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/indian/market_data_bulk")
def get_market_data_bulk():
    """Get comprehensive market data for multiple symbols"""
    try:
        if not session.get("user_id"):
            return jsonify({"error": "Not authenticated"}), 401
        
        global angel_one_client
        
        if not angel_one_client or not angel_one_client.is_connected:
            return jsonify({"error": "Angel One not connected"}), 500
        
        # Get symbols from request or use default
        symbols_param = request.args.get('symbols', '')
        if symbols_param:
            # Parse symbols from request
            symbol_names = symbols_param.split(',')
            symbol_tokens = []
            for name in symbol_names:
                token = {
                    'SBIN': '3045', 'BANKNIFTY': '99992000', 'INFY': '11536',
                    'RELIANCE': '2885', 'TCS': '2951', 'HDFCBANK': '1333',
                    'ICICIBANK': '496', 'BHARTIARTL': '319', 'KOTAKBANK': '1922',
                    'BAJFINANCE': '317', 'HINDUNILVR': '1330', 'NIFTY50': '26000',
                    'SENSEX': '1', 'FINNIFTY': '26037', 'MIDCPNIFTY': '26017'
                }.get(name.strip().upper())
                if token:
                    symbol_tokens.append(token)
        else:
            # Use default symbols
            symbol_tokens = ["3045", "99992000", "11536", "2885", "2951"]
        
        if not symbol_tokens:
            return jsonify({"error": "No valid symbols provided"}), 400
        
        symbols_to_fetch = {"NSE": symbol_tokens}
        
        logger.info(f"Fetching comprehensive market data for {len(symbol_tokens)} symbols...")
        market_data = angel_one_client.fetch_market_data_direct(symbols_to_fetch)
        
        if market_data and market_data.get('status') and market_data.get('message') == 'SUCCESS':
            # Format the data for frontend consumption
            formatted_data = {
                "status": "success",
                "message": "Market data fetched successfully",
                "data": {
                    "fetched": [],
                    "unfetched": market_data['data'].get('unfetched', [])
                },
                "timestamp": datetime.now().isoformat()
            }
            
            # Process fetched data
            for token_data in market_data['data']['fetched']:
                formatted_data["data"]["fetched"].append({
                    "symbol": token_data.get('tradingSymbol'),
                    "exchange": token_data.get('exchange'),
                    "ltp": float(token_data.get('ltp', 0)),
                    "change": float(token_data.get('netChange', 0)),
                    "change_percent": float(token_data.get('percentChange', 0)),
                    "open": float(token_data.get('open', 0)),
                    "high": float(token_data.get('high', 0)),
                    "low": float(token_data.get('low', 0)),
                    "close": float(token_data.get('close', 0)),
                    "volume": int(token_data.get('tradeVolume', 0)),
                    "52w_high": float(token_data.get('52WeekHigh', 0)),
                    "52w_low": float(token_data.get('52WeekLow', 0))
                })
            
            return jsonify(formatted_data)
        else:
            error_msg = market_data.get('message', 'Unknown error') if market_data else 'No response'
            return jsonify({
                "status": "error",
                "message": f"API Error: {error_msg}",
                "data": None
            }), 500
            
    except Exception as e:
        logger.error(f"Error in bulk market data fetch: {e}")
        return jsonify({
            "status": "error",
            "message": str(e),
            "data": None
        }), 500

@app.route("/api/indian/enhanced_auto_trade_status")
def get_enhanced_auto_trade_status():
    """Get enhanced auto-trading status and performance"""
    try:
        if not session.get("user_id"):
            return jsonify({"error": "Not authenticated"}), 401
        
        global auto_trader
        
        if auto_trader:
            status = auto_trader.get_enhanced_trade_status()
            return jsonify(status)
        else:
            return jsonify({
                "status": "error",
                "message": "Auto trader not initialized",
                "auto_trading_active": False,
                "timestamp": datetime.now().isoformat()
            }), 500
        
    except Exception as e:
        logger.error(f"Error getting enhanced auto-trade status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/indian/start_auto_trading", methods=["POST"])
def start_enhanced_auto_trading():
    """Start enhanced auto-trading"""
    try:
        if not session.get("user_id"):
            return jsonify({"error": "Not authenticated"}), 401
        
        global auto_trader
        
        if auto_trader:
            auto_trader.start()
            return jsonify({
                "status": "success",
                "message": "Enhanced auto-trading started successfully",
                "auto_trading_active": True,
                "timestamp": datetime.now().isoformat()
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Auto trader not initialized",
                "auto_trading_active": False,
                "timestamp": datetime.now().isoformat()
            }), 500
        
    except Exception as e:
        logger.error(f"Error starting enhanced auto-trading: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/indian/stop_auto_trading", methods=["POST"])
def stop_enhanced_auto_trading():
    """Stop enhanced auto-trading"""
    try:
        if not session.get("user_id"):
            return jsonify({"error": "Not authenticated"}), 401
        
        global auto_trader
        
        if auto_trader:
            auto_trader.stop()
            return jsonify({
                "status": "success",
                "message": "Enhanced auto-trading stopped successfully",
                "auto_trading_active": False,
                "timestamp": datetime.now().isoformat()
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Auto trader not initialized",
                "auto_trading_active": False,
                "timestamp": datetime.now().isoformat()
            }), 500
        
    except Exception as e:
        logger.error(f"Error stopping enhanced auto-trading: {e}")
        return jsonify({"error": str(e)}), 500

# Test endpoints for enhanced dashboard (no authentication required)
@app.route("/test_enhanced_signals", methods=["POST"])
def test_enhanced_signals():
    """Test enhanced signals generation"""
    try:
        global angel_one_client
        
        if angel_one_client and angel_one_client.is_connected:
            # Test signal generation
            symbols = ["SBIN", "BANKNIFTY", "INFY", "RELIANCE"]
            signals = []
            
            for symbol in symbols:
                try:
                    signal = angel_one_client.generate_enhanced_signal(symbol)
                    if signal and signal.get('confidence', 0) > 0.5:  # Lower threshold for testing
                        signals.append(signal)
                except Exception as e:
                    logger.warning(f"Failed to generate signal for {symbol}: {e}")
            
            return jsonify({
                "status": "success",
                "signals": signals,
                "count": len(signals),
                "message": f"Generated {len(signals)} test signals"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Angel One not connected",
                "signals": []
            }), 500
            
    except Exception as e:
        logger.error(f"Error testing enhanced signals: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test_market_data", methods=["POST"])
def test_market_data():
    """Test comprehensive market data fetching"""
    try:
        global angel_one_client
        
        if angel_one_client and angel_one_client.is_connected:
            # Test market data fetching
            symbols = {"NSE": ["3045", "99992000", "11536", "2885"]}  # SBIN, BANKNIFTY, INFY, RELIANCE
            market_data = angel_one_client.fetch_market_data_direct(symbols)
            
            if market_data and market_data.get('data', {}).get('fetched'):
                return jsonify({
                    "status": "success",
                    "market_data": market_data['data']['fetched'],
                    "count": len(market_data['data']['fetched']),
                    "message": f"Fetched data for {len(market_data['data']['fetched'])} symbols"
                })
            else:
                return jsonify({
                    "status": "success",
                    "market_data": [],
                    "count": 0,
                    "message": "No market data available (market may be closed)"
                })
        else:
            return jsonify({
                "status": "error",
                "message": "Angel One not connected",
                "market_data": []
            }), 500
            
    except Exception as e:
        logger.error(f"Error testing market data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test_performance", methods=["POST"])
def test_performance():
    """Test performance metrics"""
    try:
        global auto_trader, angel_one_client
        
        status = {
            "status": "success",
            "angel_one_connected": angel_one_client.is_connected if angel_one_client else False,
            "enhanced_trading_active": False,
            "active_trades": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "message": "Performance test completed"
        }
        
        if auto_trader:
            try:
                enhanced_status = auto_trader.get_enhanced_trade_status()
                status.update(enhanced_status)
            except Exception as e:
                logger.warning(f"Could not get enhanced status: {e}")
        
        return jsonify(status)
        
    except Exception as e:
        logger.error(f"Error testing performance: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test_system_status", methods=["POST"])
def test_system_status():
    """Test system status"""
    try:
        global auto_trader, angel_one_client
        
        status = {
            "status": "success",
            "angel_one_connected": angel_one_client.is_connected if angel_one_client else False,
            "enhanced_trading_active": False,
            "active_trades": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "message": "System status test completed"
        }
        
        if auto_trader:
            try:
                enhanced_status = auto_trader.get_enhanced_trade_status()
                status.update(enhanced_status)
            except Exception as e:
                logger.warning(f"Could not get enhanced status: {e}")
        
        return jsonify(status)
        
    except Exception as e:
        logger.error(f"Error testing system status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test_start_auto_trading", methods=["POST"])
def test_start_auto_trading():
    """Test start auto-trading"""
    try:
        global auto_trader
        
        if auto_trader:
            auto_trader.start()
            return jsonify({
                "status": "success",
                "message": "Auto-trading started successfully (test mode)"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Auto trader not initialized"
            }), 500
        
    except Exception as e:
        logger.error(f"Error testing start auto-trading: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test_stop_auto_trading", methods=["POST"])
def test_stop_auto_trading():
    """Test stop auto-trading"""
    try:
        global auto_trader
        
        if auto_trader:
            auto_trader.stop()
            return jsonify({
                "status": "success",
                "message": "Auto-trading stopped successfully (test mode)"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Auto trader not initialized"
            }), 500
        
    except Exception as e:
        logger.error(f"Error testing stop auto-trading: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test_direct_api", methods=["POST"])
def test_direct_api():
    """Test direct API methods"""
    try:
        global angel_one_client, auto_trader
        
        results = {
            "status": "success",
            "angel_one_connected": angel_one_client.is_connected if angel_one_client else False,
            "enhanced_trading_active": False,
            "market_data_available": False,
            "signals_count": 0,
            "test_status": "completed"
        }
        
        # Test Angel One connection
        if angel_one_client and angel_one_client.is_connected:
            results["angel_one_connected"] = True
            
            # Test market data
            try:
                symbols = {"NSE": ["3045"]}  # Test with SBIN
                market_data = angel_one_client.fetch_market_data_direct(symbols)
                if market_data and market_data.get('data', {}).get('fetched'):
                    results["market_data_available"] = True
            except Exception as e:
                logger.warning(f"Market data test failed: {e}")
            
            # Test signal generation
            try:
                signal = angel_one_client.generate_enhanced_signal("SBIN")
                if signal:
                    results["signals_count"] = 1
            except Exception as e:
                logger.warning(f"Signal generation test failed: {e}")
        
        # Test auto-trader status
        if auto_trader:
            try:
                enhanced_status = auto_trader.get_enhanced_trade_status()
                results["enhanced_trading_active"] = enhanced_status.get("enhanced_trading_active", False)
            except Exception as e:
                logger.warning(f"Auto-trader status test failed: {e}")
        
        return jsonify(results)
        
    except Exception as e:
        logger.error(f"Error testing direct API: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/indian/market_data/<pair>")
def get_indian_market_data(pair):
    """Get market data for Indian trading pair"""
    if not session.get("user_id"):
        return jsonify({"error": "Not authenticated"}), 401
        
    try:
        logger.info(f"Fetching market data for pair: {pair}")
        # Get historical data for the pair
        data = indian_trading_system.get_indian_market_data(pair, period='1d', interval='1d')
        logger.info(f"Data received: {data is not None and not data.empty if data is not None else 'None'}")
        
        if data is None or data.empty:
            logger.warning(f"No data available for {pair}")
            return jsonify({
                "historical": {"dates": [], "prices": {"open": [], "high": [], "low": [], "close": [], "volume": []}},
                "current_price": 0.0,
                "pair": pair,
                "hint": "No data yet from Angel One. Try again in a moment or change interval."
            })
        
        # Determine data source
        data_source = data.attrs.get('data_source', 'angel_one') if hasattr(data, 'attrs') else 'angel_one'
        
        # Convert to chart-friendly format with proper type conversion
        chart_data = {
            "historical": {
                "dates": data.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
                "prices": {
                    "open": [float(x) for x in data['Open'].tolist()],
                    "high": [float(x) for x in data['High'].tolist()],
                    "low": [float(x) for x in data['Low'].tolist()],
                    "close": [float(x) for x in data['Close'].tolist()],
                    "volume": [int(x) for x in data['Volume'].tolist()]
                }
            },
            "current_price": float(data['Close'].iloc[-1]) if not data.empty else 0.0,
            "pair": pair,
            "data_source": data_source
        }
        
        return jsonify(chart_data)
        
    except Exception as e:
        logger.error(f"Error getting Indian market data for {pair}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/indian/realtime_data/<pair>")
def get_indian_realtime_data(pair):
    """Get real-time data for Indian trading pair using Angel One API"""
    try:
        global angel_one_client
        
        # Note: We'll provide mock data if not connected, so we don't return error here
        
        # Get symbol token from mapping
        symbol_token = symbol_map.get(pair)
        if not symbol_token:
            return jsonify({"error": f"Symbol {pair} not found in mapping"}), 404
        
        # Determine exchange based on symbol
        exchange = "NSE"
        if pair in ["SENSEX", "BANKEX"]:
            exchange = "BSE"
        
        logger.info(f"Processing {pair} with symbol_token: {symbol_token}, exchange: {exchange}")
        
        # Get live data with error handling
        live_data = None
        live_price = 19500.0  # Default mock price
        
        try:
            if angel_one_client and angel_one_client.is_connected:
                live_data = angel_one_client.get_live_data(symbol_token, exchange)
                logger.info(f"Live data result: {live_data}")
                if live_data and live_data.get('data'):
                    live_price = live_data.get('data', {}).get('ltp', 19500.0)
                else:
                    logger.warning("No live data received, using mock data")
                    live_data = {"data": {"ltp": live_price}}
            else:
                logger.warning("Angel One not connected, providing mock data")
                live_data = {"data": {"ltp": live_price}}
        except Exception as e:
            logger.error(f"Error getting live data: {e}")
            live_data = {"data": {"ltp": live_price}}
        
        # Get detailed quote data with error handling
        quote_data = {}
        try:
            if angel_one_client and angel_one_client.is_connected:
                quote_data = angel_one_client.get_quote_data(symbol_token, exchange) or {}
        except Exception as e:
            logger.error(f"Error getting quote data: {e}")
            quote_data = {}
        
        # Prepare response
        response_data = {
            "pair": pair,
            "symbol_token": symbol_token,
            "exchange": exchange,
            "live_price": live_price,
            "timestamp": datetime.now().isoformat(),
            "quote": quote_data.get('data', {}) if quote_data else {},
            "data_source": "live" if (live_data and live_data.get('data', {}).get('ltp', 0) > 0) else "mock"
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error getting real-time data for {pair}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/nifty50")
def get_nifty50_data():
    """Dedicated API endpoint for NIFTY50 data"""
    try:
        global angel_one_client
        
        if not angel_one_client or not angel_one_client.is_connected:
            return jsonify({"error": "Angel One API not connected"}), 400
        
        # Get NIFTY50 symbol token
        symbol_token = symbol_map.get("NIFTY50")
        if not symbol_token:
            return jsonify({"error": "NIFTY50 symbol not found in mapping"}), 404
        
        # Get live data
        live_data = angel_one_client.get_live_data(symbol_token, "NSE")
        
        # For now, provide mock data if Angel One is not connected
        if not angel_one_client or not angel_one_client.is_connected:
            logger.warning("Angel One not connected, providing mock data for NIFTY50")
            live_price = 19500.0  # Mock NIFTY50 price
            live_data = {"data": {"ltp": live_price}}
        elif not live_data:
            logger.warning("Live data failed for NIFTY50, trying historical data fallback")
            try:
                # Get historical data for the last day
                from datetime import datetime, timedelta
                to_date = datetime.now().strftime('%d-%m-%Y')
                from_date = (datetime.now() - timedelta(days=1)).strftime('%d-%m-%Y')
                
                historical_data = angel_one_client.get_historical_data(
                    symbol_token, "NSE", "ONE_DAY", from_date, to_date
                )
                
                if historical_data and isinstance(historical_data, dict) and historical_data.get('data'):
                    # Use the last close price as live price
                    last_candle = historical_data['data'][-1] if historical_data['data'] else None
                    if last_candle:
                        live_price = last_candle.get('close', 0.0)
                    else:
                        live_price = 0.0
                else:
                    live_price = 0.0
            except Exception as e:
                logger.error(f"Historical data fallback failed: {e}")
                live_price = 0.0
        else:
            live_price = live_data.get('data', {}).get('ltp', 0.0) if live_data else 0.0
        
        # Get detailed quote data
        quote_data = angel_one_client.get_quote_data(symbol_token, "NSE")
        
        # Prepare response
        response_data = {
            "pair": "NIFTY50",
            "symbol_token": symbol_token,
            "exchange": "NSE",
            "live_price": live_price,
            "timestamp": datetime.now().isoformat(),
            "quote": quote_data.get('data', {}) if quote_data else {},
            "data_source": "live" if (live_data and live_data.get('data', {}).get('ltp', 0) > 0) else "mock",
            "status": "success"
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error getting NIFTY50 data: {str(e)}")
        return jsonify({"error": str(e), "status": "error"}), 500

@app.route("/test/indian/market_data/<pair>")
def test_indian_market_data(pair):
    """Public test endpoint for Indian market data - no authentication required"""
    try:
        # Get historical data for the pair with small retry/backoff on empty
        attempts = 0
        data = None
        while attempts < 2:
            data = indian_trading_system.get_indian_market_data(pair, period='1d', interval='1d')
            if data is not None and not data.empty:
                break
            time.sleep(0.6)  # brief backoff to avoid rate-limit
            attempts += 1
        
        if data is None or data.empty:
            return jsonify({
                "historical": {"dates": [], "prices": {"open": [], "high": [], "low": [], "close": [], "volume": []}},
                "current_price": 0.0,
                "pair": pair,
                "data_points": 0,
                "message": "No data from Angel One (rate limit or symbol)."
            })
        
        # Convert to chart-friendly format with proper type conversion
        chart_data = {
            "historical": {
                "dates": data.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
                "prices": {
                    "open": [float(x) for x in data['Open'].tolist()],
                    "high": [float(x) for x in data['High'].tolist()],
                    "low": [float(x) for x in data['Low'].tolist()],
                    "close": [float(x) for x in data['Close'].tolist()],
                    "volume": [int(x) for x in data['Volume'].tolist()]
                }
            },
            "current_price": float(data['Close'].iloc[-1]) if not data.empty else 0.0,
            "pair": pair,
            "data_points": int(len(data)),
            "date_range": {
                "start": data.index[0].strftime('%Y-%m-%d %H:%M:%S'),
                "end": data.index[-1].strftime('%Y-%m-%d %H:%M:%S')
            },
            "message": "✅ Data retrieved successfully! This is a public test endpoint."
        }
        
        return jsonify(chart_data)
        
    except Exception as e:
        logger.error(f"Error getting Indian market data for {pair}: {str(e)}")
        return jsonify({
            "error": str(e),
            "pair": pair,
            "message": "❌ Error occurred while fetching data"
        }), 500

@app.route("/test/indian/pairs")
def test_indian_pairs():
    """Public test endpoint to list all available Indian trading pairs"""
    try:
        # Get the list of pairs from the Indian trading system
        pairs = [
            "NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY",
            "NIFTYREALTY", "NIFTYPVTBANK", "NIFTYPSUBANK", "NIFTYFIN", "NIFTYMEDIA",
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
            "SBIN", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE"
        ]
        
        return jsonify({
            "available_pairs": pairs,
            "total_pairs": len(pairs),
            "message": "✅ Available Indian trading pairs. Use /test/indian/market_data/<pair> to get data for a specific pair.",
            "example_urls": [
                "http://localhost:5000/test/indian/market_data/NIFTY50",
                "http://localhost:5000/test/indian/market_data/BANKNIFTY",
                "http://localhost:5000/test/indian/market_data/RELIANCE"
            ]
        })
        
    except Exception as e:
        logger.error(f"Error getting Indian pairs: {str(e)}")
        return jsonify({
            "error": str(e),
            "message": "❌ Error occurred while fetching pairs list"
        }), 500

@app.route("/test/indian/debug/<pair>")
def test_indian_debug(pair):
    """Debug endpoint to see raw data from Indian trading system"""
    try:
        # Get raw data
        data = indian_trading_system.get_indian_market_data(pair, period='1d', interval='1d')
        
        if data is None:
            return jsonify({
                "error": "Data is None",
                "pair": pair
            }), 404
            
        if data.empty:
            return jsonify({
                "error": "Data is empty",
                "pair": pair
            }), 404
        
        # Return raw data info with proper type conversion
        return jsonify({
            "pair": pair,
            "data_type": str(type(data)),
            "data_shape": [int(x) for x in data.shape],
            "data_columns": data.columns.tolist(),
            "data_index_type": str(type(data.index)),
            "data_index_name": data.index.name,
            "first_few_rows": {
                str(k): {
                    str(col): float(v) if isinstance(v, (int, float)) else str(v)
                    for col, v in row.items()
                }
                for k, row in data.head(3).to_dict('index').items()
            },
            "message": "✅ Raw data retrieved successfully"
        })
        
    except Exception as e:
        logger.error(f"Error in debug endpoint for {pair}: {str(e)}")
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc(),
            "pair": pair
        }), 500

@app.route('/angel_one_config')
def angel_one_config():
    return render_template('angel_one_config.html')

@app.route('/admin')
def admin():
    """Render simple admin page for user management"""
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    # Get all users
    conn = get_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, balance, is_premium FROM users")
        users = cursor.fetchall()
        conn.close()
    else:
        users = []
    
    return render_template('admin.html', users=users)

@app.route('/admin_panel')
def admin_panel():
    """Render advanced admin panel page"""
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    return render_template('admin_panel.html')

@app.route("/enhanced_dashboard")
def enhanced_dashboard():
    """Enhanced Auto-Trading Dashboard"""
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    return render_template("enhanced_dashboard.html")

@app.route("/indian", methods=["GET", "POST"])
def indian_market():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    pairs = [
        "NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY",
        "NIFTYREALTY", "NIFTYPVTBANK", "NIFTYPSUBANK", "NIFTYFIN", "NIFTYMEDIA",
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "SBIN", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE"
    ]
    brokers = ["Angel One", "Zerodha", "Upstox", "Groww", "ICICI Direct", "HDFC Securities"]

    # Get selected pair and broker from query parameters or form data
    selected_pair = request.args.get('pair') or request.form.get('pair')
    selected_broker = request.args.get('broker') or request.form.get('broker') or brokers[0]

    # Initialize variables with default values
    current_rate = None
    data_source = None
    call_price = None
    put_price = None
    volatility = None
    expiry = None
    risk_free_rate = None
    payout = None
    signals = None

    # Only fetch data if a valid pair is selected
    if selected_pair and selected_pair != "Select Pair":
        try:
            logger.info(f"Fetching data for selected pair: {selected_pair}")
            # Get historical data for the selected pair
            data = get_historical_data(selected_pair)
            logger.info(f"Historical data result: {data}")
            
            if data and data.get('historical'):
                historical_data = data['historical']
                current_rate = historical_data['prices']['close'][-1] if historical_data['prices']['close'] else None
                data_source = 'Yahoo Finance'
                logger.info(f"Using Yahoo Finance data. Current rate: {current_rate}")

                if current_rate:
                    # Calculate option prices
                    S = current_rate  # Current price
                    K = current_rate  # Strike price (at-the-money)
                    T = 5/365  # Time to expiry (5 days)
                    r = 0.05  # Risk-free rate (5%)
                    sigma = 0.20  # Volatility (20%)

                    call_price = black_scholes_call_put(S, K, T, r, sigma, "call")
                    put_price = black_scholes_call_put(S, K, T, r, sigma, "put")

                    volatility = sigma
                    expiry = T
                    risk_free_rate = r

                    # Set payout based on broker
                    payout = broker_payouts.get(selected_broker, 0.75)

                    # Get signals if they exist
                    signals = get_signals_for_user(session["user_id"])
                    
                    # Generate technical indicators for the chart
                    chart_data = generate_indian_market_indicators(current_rate, selected_pair)
            else:
                # Provide fallback data for Indian markets
                logger.info(f"Providing fallback data for {selected_pair}")
                if selected_pair in ["NIFTY50", "BANKNIFTY", "SENSEX"]:
                    # Sample data for major indices
                    sample_data = {
                        "NIFTY50": 19500.0,
                        "BANKNIFTY": 44500.0,
                        "SENSEX": 65000.0
                    }
                    current_rate = sample_data.get(selected_pair, 10000.0)
                    data_source = 'Sample Data (Market Closed)'
                    
                    # Calculate option prices with sample data
                    S = current_rate
                    K = current_rate
                    T = 5/365
                    r = 0.05
                    sigma = 0.20

                    call_price = black_scholes_call_put(S, K, T, r, sigma, "call")
                    put_price = black_scholes_call_put(S, K, T, r, sigma, "put")

                    volatility = sigma
                    expiry = T
                    risk_free_rate = r
                    payout = broker_payouts.get(selected_broker, 0.75)
                    
                    # Generate technical indicators for the chart
                    chart_data = generate_indian_market_indicators(current_rate, selected_pair)
                    
                    # Get signals
                    signals = get_signals_for_user(session["user_id"])
                    
                    logger.info(f"Fallback data set - Rate: {current_rate}, Call: {call_price}, Put: {put_price}")
                    flash(f"Using sample data for {selected_pair}. Real-time data will be available during market hours.", "info")
        except Exception as e:
            logger.error(f"Error fetching Indian market data: {str(e)}")
            flash("Error fetching market data. Please try again.", "error")
    
    # Ensure chart_data is always available
    if 'chart_data' not in locals() and current_rate:
        chart_data = generate_indian_market_indicators(current_rate, selected_pair or "NIFTY50")
    elif 'chart_data' not in locals():
        # Default chart data if no rate available
        chart_data = generate_indian_market_indicators(19500.0, "NIFTY50")

    logger.info(f"Final data being sent to template - Rate: {current_rate}, Source: {data_source}, Call: {call_price}, Put: {put_price}")
    return render_template(
        "indian.html",
        pairs=pairs,
        brokers=brokers,
        selected_pair=selected_pair,
        selected_broker=selected_broker,
        current_rate=current_rate,
        data_source=data_source,
        call_price=call_price,
        put_price=put_price,
        volatility=volatility,
        expiry=expiry,
        risk_free_rate=risk_free_rate,
        payout=payout,
        signals=signals,
        chart_data=chart_data if 'chart_data' in locals() else None
    )

@app.route("/api/price/<pair>")
def api_price(pair):
    """API endpoint for getting real-time price data"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        broker = request.args.get('broker', 'Quotex')
        logger.info(f"Fetching price for {pair} with broker {broker}")

        # Define broker payouts
        broker_payouts = {
            # Forex brokers
            "Quotex": 0.85,
            "Pocket Option": 0.80,
            "Binolla": 0.78,
            "IQ Option": 0.82,
            "Bullex": 0.75,
            "Exnova": 0.77,
            
            # Indian brokers
            "Zerodha": 0.75,
            "Upstox": 0.78,
            "Angel One": 0.77,
            "Groww": 0.76,
            "ICICI Direct": 0.75,
            "HDFC Securities": 0.74
        }

        # Define pricing parameters
        volatility = 0.20  # 20% volatility
        expiry = 1/365.0  # 1 day expiry
        risk_free_rate = 0.01  # 1% risk-free rate

        current_rate = None
        data_source = None

        # Get current rate and source
        if '_OTC' in pair:
            if otc_handler is None:
                logger.error("OTC handler not initialized")
                return jsonify({
                    'error': 'OTC service not available',
                    'details': 'The OTC price service is not properly initialized.'
                }), 503

            try:
                logger.info(f"Fetching OTC price for {pair}")
                price_data = otc_handler.get_realtime_price(pair, return_source=True)
                logger.info(f"Received OTC price data: {price_data}")

                if isinstance(price_data, tuple):
                    current_rate, data_source = price_data
                else:
                    current_rate = price_data
                    data_source = "OTC Data"

                if current_rate is None:
                    logger.warning(f"OTC price not available, trying fallback for {pair}")
                    # Try fallback to regular forex rate
                    base_pair = pair.replace('_OTC', '')
                    price_data = get_forex_rate(base_pair, return_source=True)
                    logger.info(f"Fallback forex data: {price_data}")

                    if isinstance(price_data, tuple):
                        current_rate, data_source = price_data
                        data_source = f"Fallback: {data_source}"
                    else:
                        current_rate = price_data
                        data_source = "Fallback API"
            except Exception as e:
                logger.error(f"Error fetching OTC price: {str(e)}")
                return jsonify({
                    'error': 'Failed to fetch OTC price',
                    'details': str(e)
                }), 500
        elif pair in ["NIFTY50", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", 
                      "NIFTYREALTY", "NIFTYPVTBANK", "NIFTYPSUBANK", "NIFTYFIN", "NIFTYMEDIA",
                      "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", 
                      "SBIN", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE"]:
            # Handle Indian market indices and stocks
            logger.info(f"Fetching Indian market data for {pair}")
            
            # Try to get historical data first
            try:
                data = get_historical_data(pair)
                if data and data.get('historical'):
                    historical_data = data['historical']
                    current_rate = historical_data['prices']['close'][-1] if historical_data['prices']['close'] else None
                    data_source = 'Yahoo Finance'
                    logger.info(f"Using Yahoo Finance data for {pair}: {current_rate}")
                else:
                    # Provide fallback data for Indian markets
                    logger.info(f"Providing fallback data for {pair}")
                    sample_data = {
                        "NIFTY50": 19500.0,
                        "BANKNIFTY": 44500.0,
                        "SENSEX": 65000.0,
                        "FINNIFTY": 20000.0,
                        "MIDCPNIFTY": 12000.0,
                        "NIFTYREALTY": 450.0,
                        "NIFTYPVTBANK": 45000.0,
                        "NIFTYPSUBANK": 18000.0,
                        "NIFTYFIN": 20000.0,
                        "NIFTYMEDIA": 2500.0,
                        "RELIANCE": 2500.0,
                        "TCS": 3800.0,
                        "HDFCBANK": 1600.0,
                        "INFY": 1500.0,
                        "ICICIBANK": 950.0,
                        "HINDUNILVR": 2500.0,
                        "SBIN": 650.0,
                        "BHARTIARTL": 950.0,
                        "KOTAKBANK": 1800.0,
                        "BAJFINANCE": 7500.0
                    }
                    current_rate = sample_data.get(pair, 10000.0)
                    data_source = 'Sample Data (Market Closed)'
                    logger.info(f"Fallback data set for {pair}: {current_rate}")
            except Exception as e:
                logger.error(f"Error fetching Indian market data: {str(e)}")
                # Still provide fallback data
                sample_data = {
                    "NIFTY50": 19500.0,
                    "BANKNIFTY": 44500.0,
                    "SENSEX": 65000.0
                }
                current_rate = sample_data.get(pair, 10000.0)
                data_source = 'Sample Data (Error Fallback)'
                logger.info(f"Error fallback data set for {pair}: {current_rate}")
        else:
            try:
                logger.info(f"Fetching forex rate for {pair}")
                price_data = get_forex_rate(pair, return_source=True)
                logger.info(f"Received forex data: {price_data}")

                if isinstance(price_data, tuple):
                    current_rate, data_source = price_data
                else:
                    current_rate = price_data
                    data_source = "Forex API"
            except Exception as e:
                logger.error(f"Error fetching forex rate: {str(e)}")
                return jsonify({
                    'error': 'Failed to fetch forex rate',
                    'details': str(e)
                }), 500

        if current_rate is None:
            logger.error(f"No price data available for {pair}")
            return jsonify({
                'error': 'Price data unavailable',
                'details': 'Could not fetch price from any available source'
            }), 503

        # Calculate option prices
        try:
            logger.info(f"Calculating option prices for rate: {current_rate}")
            call_price = black_scholes_call_put(current_rate, current_rate, expiry, risk_free_rate, volatility, option_type="call")
            put_price = black_scholes_call_put(current_rate, current_rate, expiry, risk_free_rate, volatility, option_type="put")

            response_data = {
                'rate': current_rate,
                'source': data_source,
                'call_price': call_price,
                'put_price': put_price,
                'payout': broker_payouts.get(broker, 0.75),
                'volatility': volatility,
                'expiry': expiry,
                'risk_free_rate': risk_free_rate
            }
            logger.info(f"Sending response: {response_data}")
            return jsonify(response_data)

        except Exception as e:
            logger.error(f"Error calculating option prices: {str(e)}")
            return jsonify({
                'error': 'Option calculation error',
                'details': str(e)
            }), 500

    except Exception as e:
        logger.error(f"Unexpected error in api_price: {str(e)}")
        return jsonify({
            'error': 'Server error',
            'details': str(e)
        }), 500

@app.route("/check_session")
def check_session():
    """Check if user is authenticated"""
    if 'user_id' not in session:
        return jsonify({'authenticated': False}), 401
    return jsonify({'authenticated': True})

@app.route("/subscription")
def subscription():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user = get_user_by_id(session["user_id"])
    return render_template("subscription.html", user=user)

@app.route("/legal")
def legal():
    return render_template('legal.html')

@app.route("/terms")
def terms():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template('terms.html')

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if "user_id" not in session:
        return redirect(url_for("login"))
        
    if request.method == "POST":
        try:
            name = request.form.get('name')
            email = request.form.get('email')
            subject = request.form.get('subject')
            message = request.form.get('message')
            
            # Here you can add your email sending logic later
            # For now, we'll just return a success response
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error processing contact form: {str(e)}")
            return jsonify({"success": False})
    
    return render_template('contact.html')

@app.route("/download_forex")
def download_forex():
    """Download Forex signals as PDF with professional branding"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Get signals for the user
    signals = get_signals_for_user(session["user_id"])

    # Create PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []

    # Add company logo
    try:
        logo_path = os.path.join(STATIC_FOLDER, "images", "logo1.png")
        if os.path.exists(logo_path):
            img = Image(logo_path, width=200, height=50)
            elements.append(img)
    except Exception as e:
        logger.error(f"Error loading logo: {str(e)}")

    # Add title with styling
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        textColor=colors.HexColor('#00e6d0'),
        alignment=TA_CENTER
    )
    elements.append(Paragraph("KishanX Forex Trading Signals", title_style))

    # Add report details
    details_style = ParagraphStyle(
        'DetailsStyle',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.HexColor('#666666'),
        alignment=TA_CENTER
    )
    elements.append(Paragraph(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", details_style))
    elements.append(Spacer(1, 20))

    # Add market overview section
    overview_style = ParagraphStyle(
        'OverviewStyle',
        parent=styles['Heading2'],
        fontSize=16,
        spaceAfter=10,
        textColor=colors.HexColor('#333333')
    )
    elements.append(Paragraph("Market Overview", overview_style))
    elements.append(Paragraph("Forex Market Trading Signals", styles['Normal']))
    elements.append(Spacer(1, 20))

    # Add signals table with enhanced styling
    if signals:
        # Table header with more columns
        data = [['Time', 'Pair', 'Direction', 'Entry Price', 'Stop Loss', 'Take Profit', 'Confidence', 'Status']]

        # Add signals with calculated levels
        for signal in signals:
            if not signal['pair'].endswith('_OTC'):  # Only include Forex pairs
                try:
                    # Calculate entry, stop loss and take profit levels
                    entry_price = signal.get('entry_price', 'N/A')
                    stop_loss = signal.get('stop_loss', 'N/A')
                    take_profit = signal.get('take_profit', 'N/A')
                    confidence = signal.get('confidence', 0.0)
                    status = "Won" if signal.get('result') == 1 else "Lost" if signal.get('result') == 0 else "Pending"

                    # Format numerical values
                    entry_price_str = f"{float(entry_price):.5f}" if isinstance(entry_price, (int, float)) else str(entry_price)
                    stop_loss_str = f"{float(stop_loss):.5f}" if isinstance(stop_loss, (int, float)) else str(stop_loss)
                    take_profit_str = f"{float(take_profit):.5f}" if isinstance(take_profit, (int, float)) else str(take_profit)
                    confidence_str = f"{float(confidence):.1f}%"

                    data.append([
                        signal['time'],
                        signal['pair'],
                        signal['direction'],
                        entry_price_str,
                        stop_loss_str,
                        take_profit_str,
                        confidence_str,
                        status
                    ])
                except Exception as e:
                    logger.error(f"Error processing signal: {str(e)}")
                    continue

        # Create table with enhanced styling
        if len(data) > 1:  # Only create table if we have data
            table = Table(data, colWidths=[80, 80, 80, 80, 80, 80, 80, 80])
            table.setStyle(TableStyle([
                # Header styling
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a1a')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#00e6d0')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),

                # Body styling
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#2a2a2a')),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.white),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#333333')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

                # Alternating row colors
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#2a2a2a'), colors.HexColor('#333333')])
            ]))
            elements.append(table)
        else:
            elements.append(Paragraph("No Forex signals available", styles['Normal']))
    else:
        elements.append(Paragraph("No signals available", styles['Normal']))

    # Add trading guidelines
    elements.append(Spacer(1, 30))
    guidelines_style = ParagraphStyle(
        'GuidelinesStyle',
        parent=styles['Heading2'],
        fontSize=16,
        spaceAfter=10,
        textColor=colors.HexColor('#333333')
    )
    elements.append(Paragraph("Trading Guidelines", guidelines_style))

    guidelines = [
        "• Always use proper risk management",
        "• Set stop-loss and take-profit levels before entering trades",
        "• Monitor market conditions before executing trades",
        "• Keep track of your trading performance",
        "• Follow the signals strictly as provided"
    ]

    for guideline in guidelines:
        elements.append(Paragraph(guideline, styles['Normal']))

    # Add disclaimer and company information
    elements.append(Spacer(1, 30))
    footer_style = ParagraphStyle(
        'FooterStyle',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#666666'),
        alignment=TA_CENTER
    )

    # Add digital signature
    signature_style = ParagraphStyle(
        'SignatureStyle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#00e6d0'),
        alignment=TA_CENTER
    )
    elements.append(Paragraph("Digitally Signed by KishanX Trading System", signature_style))
    elements.append(Paragraph("Signature Hash: " + hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:16], signature_style))

    # Add company information
    company_info = [
        "Kishan X Trading Signals",
        "Email: support@kishanx.com",
        "Website: www.kishanx.com",
    ]

    for info in company_info:
        elements.append(Paragraph(info, footer_style))

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"kishanx_forex_signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    )

@app.route("/api/check_auth")
def check_auth():
    """Check if user is authenticated"""
    if 'user_id' not in session:
        return jsonify({'authenticated': False}), 401
    return jsonify({'authenticated': True})

def generate_indian_market_indicators(base_price, pair_name):
    """Generate realistic technical indicators for Indian markets"""
    try:
        # Generate 100 data points for realistic chart
        num_points = 100
        prices = []
        
        # Start with base price and add realistic variations
        current_price = base_price
        for i in range(num_points):
            # Add realistic price movements (small daily variations)
            variation = random.uniform(-0.02, 0.02)  # ±2% daily variation
            current_price = current_price * (1 + variation)
            prices.append(current_price)
        
        # Calculate technical indicators
        prices_array = np.array(prices)
        
        # RSI (14-period)
        rsi_values = []
        for i in range(14, len(prices_array)):
            gains = np.where(np.diff(prices_array[i-14:i+1]) > 0, np.diff(prices_array[i-14:i+1]), 0)
            losses = np.where(np.diff(prices_array[i-14:i+1]) < 0, -np.diff(prices_array[i-14:i+1]), 0)
            
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            
            if avg_loss == 0:
                rsi = 100
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            rsi_values.append(rsi)
        
        # Pad RSI to match price length
        rsi_values = [50] * 14 + rsi_values  # Start with neutral RSI
        
        # MACD (12, 26, 9)
        ema12 = calculate_ema(prices_array, 12)
        ema26 = calculate_ema(prices_array, 26)
        macd_line = ema12 - ema26
        signal_line = calculate_ema(macd_line, 9)
        
        # Bollinger Bands (20-period, 2 standard deviations)
        bb_period = 20
        bb_values = []
        bb_upper = []
        bb_lower = []
        
        for i in range(bb_period, len(prices_array)):
            window = prices_array[i-bb_period:i]
            sma = np.mean(window)
            std = np.std(window)
            bb_values.append(sma)
            bb_upper.append(sma + (2 * std))
            bb_lower.append(sma - (2 * std))
        
        # Pad Bollinger Bands
        bb_values = [prices_array[0]] * bb_period + bb_values
        bb_upper = [prices_array[0]] * bb_period + bb_upper
        bb_lower = [prices_array[0]] * bb_period + bb_lower
        
        # Stochastic RSI (14-period)
        stoch_rsi = []
        for i in range(14, len(rsi_values)):
            rsi_window = rsi_values[i-14:i+1]
            highest = max(rsi_window)
            lowest = min(rsi_window)
            if highest == lowest:
                stoch = 50
            else:
                stoch = ((rsi_values[i] - lowest) / (highest - lowest)) * 100
            stoch_rsi.append(stoch)
        
        # Pad Stochastic RSI
        stoch_rsi = [50] * 14 + stoch_rsi
        
        # Generate timestamps
        timestamps = []
        base_time = datetime.now() - timedelta(days=num_points)
        for i in range(num_points):
            timestamp = base_time + timedelta(days=i)
            timestamps.append(timestamp.strftime('%H:%M'))
        
        return {
            'prices': prices,
            'timestamps': timestamps,
            'indicators': {
                'rsi': rsi_values,
                'macd': macd_line.tolist(),
                'macd_signal': signal_line.tolist(),
                'bollinger_upper': bb_upper,
                'bollinger_lower': bb_lower,
                'bollinger_middle': bb_values,
                'stoch_rsi': stoch_rsi
            }
        }
    except Exception as e:
        logger.error(f"Error generating indicators: {str(e)}")
        return None

def calculate_ema(data, period):
    """Calculate Exponential Moving Average"""
    alpha = 2 / (period + 1)
    ema = [data[0]]
    for i in range(1, len(data)):
        ema.append(alpha * data[i] + (1 - alpha) * ema[i-1])
    return np.array(ema)

if __name__ == '__main__':
    auto_trader.start()
    
    # Start Indian auto-trading system
    try:
        enable_autotrade = os.getenv('INDIAN_AUTOTRADE_ENABLED', '0') == '1'
        if enable_autotrade:
            indian_auto_trader.start()
            logger.info("Indian auto-trading system started successfully")
        else:
            logger.info("Indian auto-trading is disabled by INDIAN_AUTOTRADE_ENABLED=0")
        logger.info("Indian auto-trading system started successfully")
    except Exception as e:
        logger.error(f"Failed to start Indian auto-trading: {str(e)}")
    
    # Start performance monitoring
    try:
        from performance_monitor import performance_monitor
        performance_monitor.start_monitoring(interval=60)  # Monitor every minute
        logger.info("Performance monitoring started")
    except Exception as e:
        logger.error(f"Failed to start performance monitoring: {str(e)}")
    
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)


