from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import requests
import json
from twilio.rest import Client
import os
import sqlite3
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import stat

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID', 'AC9b3eb15fd53562eb95fa147d87144bbb')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN', '752adf8cae239254736c0224d26e3495')
TWILIO_PHONE = os.getenv('TWILIO_PHONE', '+19472148038')
YOUR_PHONE = os.getenv('YOUR_PHONE', '+917810982910')

# Gmail Configuration
GMAIL_USER = os.getenv('GMAIL_USER', 'ghosalushnish@gmail.com')
GMAIL_PASSWORD = os.getenv('GMAIL_PASSWORD', 'ejke fgvu yqcj hjqq')
RECIPIENT_EMAIL = os.getenv('RECIPIENT_EMAIL', 'ushnishghosalgenai@gmail.com')

# Gemini AI Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'AIzaSyAhP_YoIRSRkclMeRJaOQk_5Z4Bh9JAjXo')
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent'

# Database Configuration - Home directory path
DATABASE = os.path.join(os.path.expanduser('~'), 'grievances.db')

def init_db():
    """Initialize the database with required tables and proper permissions"""
    try:
        print(f"Initializing database at: {DATABASE}")
        
        # Ensure the database file exists and is accessible
        if not os.path.exists(DATABASE):
            print(f"Database file not found at {DATABASE}")
            # Create the file if it doesn't exist
            open(DATABASE, 'a').close()
        
        # Check if we can write to the database file
        if not os.access(DATABASE, os.W_OK):
            print(f"Database file is not writable: {DATABASE}")
            # Try to fix permissions
            try:
                os.chmod(DATABASE, 0o666)
                print("Fixed database file permissions")
            except PermissionError:
                print("Could not fix database file permissions")
        
        # Create or connect to database
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        # Create grievances table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS grievances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grievance_type TEXT NOT NULL,
                priority TEXT NOT NULL,
                description TEXT NOT NULL,
                additional_context TEXT,
                submitted_by TEXT NOT NULL,
                date_submitted DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'Open',
                husband_notes TEXT,
                date_resolved DATETIME
            )
        ''')
        
        conn.commit()
        conn.close()
        
        print(f"Database initialized successfully at: {DATABASE}")
            
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise

def get_db_connection():
    """Get database connection with improved error handling"""
    try:
        # Verify database file exists in home directory
        if not os.path.exists(DATABASE):
            raise Exception(f"Database file not found at {DATABASE}")
        
        # Check if database file is writable
        if not os.access(DATABASE, os.R_OK | os.W_OK):
            print(f"Database file permissions issue: {DATABASE}")
            # Try to fix permissions
            try:
                os.chmod(DATABASE, 0o666)
                print("Fixed database file permissions")
            except PermissionError as pe:
                raise Exception(f"Database file is not accessible and permissions cannot be fixed: {pe}")
        
        # Create connection with extended timeout
        conn = sqlite3.connect(DATABASE, timeout=30.0)
        conn.row_factory = sqlite3.Row
        
        # Enable WAL mode for better concurrent access
        try:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;')  # Better performance
            conn.execute('PRAGMA temp_store=memory;')   # Use memory for temp files
        except sqlite3.Error as e:
            print(f"PRAGMA error (non-critical): {e}")
        
        return conn
        
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        raise Exception(f"Database connection failed: {str(e)}")
    except Exception as e:
        print(f"General database error: {e}")
        raise

def add_grievance_to_db(grievance_type, priority, description, additional_context, submitted_by):
    """Add a new grievance to the database with better error handling"""
    conn = None
    try:
        # Check database accessibility before attempting to write
        if os.path.exists(DATABASE):
            db_stat = os.stat(DATABASE)
            print(f"Database file permissions: {oct(db_stat.st_mode)}")
            
            if not os.access(DATABASE, os.W_OK):
                print("Attempting to fix database permissions...")
                try:
                    os.chmod(DATABASE, 0o666)
                except PermissionError:
                    raise Exception("Database file is not writable and permissions cannot be fixed")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Test write capability first
        cursor.execute('BEGIN IMMEDIATE;')
        
        cursor.execute('''
            INSERT INTO grievances (grievance_type, priority, description, additional_context, submitted_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (grievance_type, priority, description, additional_context, submitted_by))
        
        grievance_id = cursor.lastrowid
        conn.commit()
        print(f"Successfully added grievance with ID: {grievance_id}")
        
        return grievance_id
        
    except sqlite3.Error as e:
        print(f"Database insert error: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        raise Exception(f"Database error: {str(e)}")
    except Exception as e:
        print(f"General error in add_grievance_to_db: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        raise
    finally:
        if conn:
            conn.close()

def get_all_grievances():
    """Get all grievances from database"""
    conn = None
    try:
        conn = get_db_connection()
        grievances = conn.execute('''
            SELECT * FROM grievances 
            ORDER BY date_submitted DESC
        ''').fetchall()
        return grievances
    except sqlite3.Error as e:
        print(f"Database select error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def update_grievance_status(grievance_id, status, notes=None):
    """Update grievance status and add husband notes"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('BEGIN IMMEDIATE;')
        
        if status == 'Resolved':
            cursor.execute('''
                UPDATE grievances 
                SET status = ?, husband_notes = ?, date_resolved = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, notes, grievance_id))
        else:
            cursor.execute('''
                UPDATE grievances 
                SET status = ?, husband_notes = ?
                WHERE id = ?
            ''', (status, notes, grievance_id))
        
        conn.commit()
        return True
        
    except sqlite3.Error as e:
        print(f"Database update error: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        raise Exception(f"Database error: {str(e)}")
    finally:
        if conn:
            conn.close()

# Initialize Twilio client
try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except Exception as e:
    twilio_client = None
    print(f"Twilio initialization error: {e}")

# Initialize database
try:
    init_db()
except Exception as e:
    print(f"Failed to initialize database:
