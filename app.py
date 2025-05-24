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
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
import logging

app = Flask(__name__)

# Security: Generate a proper secret key
app.secret_key = os.getenv('SECRET_KEY', secrets.token_urlsafe(32))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration - All sensitive data should come from environment variables
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE = os.getenv('TWILIO_PHONE')
YOUR_PHONE = os.getenv('YOUR_PHONE')

# Gmail Configuration
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_PASSWORD = os.getenv('GMAIL_PASSWORD')  # Should be an app password
RECIPIENT_EMAIL = os.getenv('RECIPIENT_EMAIL')

# Gemini AI Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent'

# Database Configuration
DATABASE = os.path.join(os.getcwd(), os.getenv('DATABASE', 'grievances.db'))

# User credentials (in production, these should be stored securely)
WIFE_USERNAME = os.getenv('WIFE_USERNAME', 'Wifey')
WIFE_PASSWORD_HASH = generate_password_hash(os.getenv('WIFE_PASSWORD', 'default_wife_password'))
HUSBAND_USERNAME = os.getenv('HUSBAND_USERNAME', 'Hubby')
HUSBAND_PASSWORD_HASH = generate_password_hash(os.getenv('HUSBAND_PASSWORD', 'default_husband_password'))

def init_db():
    """Initialize the database with required tables"""
    try:
        # Ensure the database directory exists and is writable
        db_dir = os.path.dirname(os.path.abspath(DATABASE))
        if not os.path.exists(db_dir):
            os.makedirs(db_dir, mode=0o755)
        
        # Check if we can write to the directory
        if not os.access(db_dir, os.W_OK):
            logger.warning(f"No write permission to database directory: {db_dir}")
        
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        # Create grievances table with better structure
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS grievances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grievance_type TEXT NOT NULL,
                priority TEXT NOT NULL CHECK (priority IN ('Low', 'Medium', 'High', 'Critical')),
                description TEXT NOT NULL,
                additional_context TEXT,
                submitted_by TEXT NOT NULL,
                date_submitted DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'Open' CHECK (status IN ('Open', 'In Progress', 'Resolved', 'Closed')),
                husband_notes TEXT,
                date_resolved DATETIME
            )
        ''')
        
        conn.commit()
        conn.close()
        
        # Set appropriate file permissions
        try:
            os.chmod(DATABASE, 0o664)
        except OSError:
            pass
            
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise

def get_db_connection():
    """Get database connection with error handling"""
    try:
        conn = sqlite3.connect(DATABASE, timeout=20.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent access
        conn.execute('PRAGMA journal_mode=WAL;')
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        raise

def add_grievance_to_db(grievance_type, priority, description, additional_context, submitted_by):
    """Add a new grievance to the database"""
    conn = None
    try:
        # Validate inputs
        valid_priorities = ['Low', 'Medium', 'High', 'Critical']
        if priority not in valid_priorities:
            raise ValueError(f"Invalid priority. Must be one of: {valid_priorities}")
        
        if not description.strip():
            raise ValueError("Description cannot be empty")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO grievances (grievance_type, priority, description, additional_context, submitted_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (grievance_type, priority, description.strip(), additional_context.strip() if additional_context else None, submitted_by))
        
        grievance_id = cursor.lastrowid
        conn.commit()
        
        return grievance_id
        
    except sqlite3.Error as e:
        logger.error(f"Database insert error: {e}")
        raise Exception(f"Database error: {str(e)}")
    except ValueError as e:
        logger.error(f"Validation error: {e}")
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
            ORDER BY 
                CASE priority 
                    WHEN 'Critical' THEN 1 
                    WHEN 'High' THEN 2 
                    WHEN 'Medium' THEN 3 
                    WHEN 'Low' THEN 4 
                END,
                date_submitted DESC
        ''').fetchall()
        return grievances
    except sqlite3.Error as e:
        logger.error(f"Database select error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def update_grievance_status(grievance_id, status, notes=None):
    """Update grievance status and add husband notes"""
    conn = None
    try:
        # Validate status
        valid_statuses = ['Open', 'In Progress', 'Resolved', 'Closed']
        if status not in valid_statuses:
            raise ValueError(f"Invalid status. Must be one of: {valid_statuses}")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if grievance exists
        existing = cursor.execute('SELECT id FROM grievances WHERE id = ?', (grievance_id,)).fetchone()
        if not existing:
            raise ValueError("Grievance not found")
        
        if status in ['Resolved', 'Closed']:
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
        
    except (sqlite3.Error, ValueError) as e:
        logger.error(f"Database update error: {e}")
        raise Exception(str(e))
    finally:
        if conn:
            conn.close()

def init_twilio_client():
    """Initialize Twilio client safely"""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN]):
        logger.warning("Twilio credentials not provided - SMS notifications disabled")
        return None
    
    try:
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        logger.error(f"Twilio initialization error: {e}")
        return None

# Initialize Twilio client
twilio_client = init_twilio_client()

def send_gmail_notification(grievance_data):
    """Send Gmail notification about new grievance"""
    if not all([GMAIL_USER, GMAIL_PASSWORD, RECIPIENT_EMAIL]):
        logger.warning("Gmail credentials not configured - email notifications disabled")
        return False
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = f"💕 New Grievance Submitted - Priority: {grievance_data['priority']}"
        
        # Create email body with better formatting
        body = f"""
Hi {HUSBAND_USERNAME}! 💕

{WIFE_USERNAME} has submitted a new grievance that needs your attention:

🏷️ Type: {grievance_data['grievance_type']}
🚨 Priority: {grievance_data['priority']}
📝 Description: {grievance_data['description']}
"""
        
        if grievance_data.get('additional_context'):
            body += f"\n📋 Additional Context: {grievance_data['additional_context']}\n"
        
        body += f"""
📅 Submitted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
👤 Submitted by: {grievance_data['submitted_by']}

Please check your Husband Portal to respond and update the status.

With love,
Your Grievance Management System 💕
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Create SMTP session
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}")
        return False

def send_grievance_notification():
    """Send a simple SMS notification that a grievance has been added"""
    if not twilio_client or not YOUR_PHONE or not TWILIO_PHONE:
        logger.warning("SMS notification not configured")
        return False
    
    try:
        message = twilio_client.messages.create(
            body=f"💕 New grievance submitted by {WIFE_USERNAME}! Check the Husband Portal for details.",
            from_=TWILIO_PHONE,
            to=YOUR_PHONE
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send SMS: {str(e)}")
        return False

@app.route('/')
def index():
    if 'logged_in' in session:
        if session.get('user_type') == 'wife':
            return redirect(url_for('portal'))
        elif session.get('user_type') == 'husband':
            return redirect(url_for('husband_portal'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    
    if not username or not password:
        flash('Please enter both username and password.', 'error')
        return redirect(url_for('index'))
    
    # Check credentials
    if username == WIFE_USERNAME and check_password_hash(WIFE_PASSWORD_HASH, password):
        session['logged_in'] = True
        session['username'] = username
        session['user_type'] = 'wife'
        logger.info(f"Wife user logged in: {username}")
        return redirect(url_for('portal'))
    elif username == HUSBAND_USERNAME and check_password_hash(HUSBAND_PASSWORD_HASH, password):
        session['logged_in'] = True
        session['username'] = username
        session['user_type'] = 'husband'
        logger.info(f"Husband user logged in: {username}")
        return redirect(url_for('husband_portal'))
    else:
        logger.warning(f"Failed login attempt for username: {username}")
        flash('Invalid credentials! Please check your username and password.', 'error')
        return redirect(url_for('index'))

@app.route('/portal')
def portal():
    if 'logged_in' not in session or session.get('user_type') != 'wife':
        return redirect(url_for('index'))
    
    # Get all grievances from database for the wife to see
    grievances = get_all_grievances()
    
    return render_template('portal.html', 
                         username=session.get('username'),
                         grievances=grievances)

@app.route('/husband-portal')
def husband_portal():
    if 'logged_in' not in session or session.get('user_type') != 'husband':
        return redirect(url_for('index'))
    
    # Get all grievances from database
    grievances = get_all_grievances()
    
    return render_template('husband_portal.html', 
                         username=session.get('username'),
                         grievances=grievances)

@app.route('/update_grievance', methods=['POST'])
def update_grievance():
    if 'logged_in' not in session or session.get('user_type') != 'husband':
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        grievance_id = data.get('id')
        status = data.get('status')
        notes = data.get('notes', '').strip()
        
        if not grievance_id:
            return jsonify({'error': 'Grievance ID is required'}), 400
        
        if not status:
            return jsonify({'error': 'Status is required'}), 400
        
        update_grievance_status(grievance_id, status, notes)
        
        logger.info(f"Grievance {grievance_id} updated to status: {status}")
        return jsonify({'success': True, 'message': 'Grievance updated successfully!'})
    
    except Exception as e:
        logger.error(f"Error updating grievance: {str(e)}")
        return jsonify({'error': f'Error updating grievance: {str(e)}'}), 500

@app.route('/chatbot', methods=['POST'])
def chatbot():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    user_message = data.get('message', '').strip()
    
    if not user_message:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    if not GEMINI_API_KEY:
        return jsonify({'error': 'AI service not configured'}), 500
    
    # Prepare the prompt for Gemini to act as a supportive counselor
    system_prompt = """You are a caring and supportive AI counselor helping someone work through their feelings and thoughts rationally. 
    Your goal is to:
    1. Listen empathetically to their concerns
    2. Help them think through situations rationally
    3. Provide gentle guidance and perspective
    4. Encourage healthy communication in relationships
    5. Be warm, understanding, and non-judgmental
    
    Keep responses concise but meaningful, and always end with encouragement or a thoughtful question to help them reflect further."""
    
    try:
        headers = {
            'Content-Type': 'application/json',
        }
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": f"{system_prompt}\n\nUser: {user_message}"
                }]
            }]
        }
        
        response = requests.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if 'candidates' in data and len(data['candidates']) > 0:
                bot_response = data['candidates'][0]['content']['parts'][0]['text']
                return jsonify({'response': bot_response})
            else:
                return jsonify({'error': 'No response from AI service'})
        else:
            logger.error(f"Gemini API error: {response.status_code} - {response.text}")
            return jsonify({'error': 'Sorry, I had trouble connecting to my thoughts. Try again?'})
            
    except requests.exceptions.Timeout:
        return jsonify({'error': 'The AI service is taking too long to respond. Please try again.'})
    except Exception as e:
        logger.error(f"Chatbot error: {str(e)}")
        return jsonify({'error': 'Something went wrong. I\'m here when you\'re ready to try again.'})

@app.route('/submit_grievance', methods=['POST'])
def submit_grievance():
    if 'logged_in' not in session or session.get('user_type') != 'wife':
        return redirect(url_for('portal'))
    
    try:
        # Get form data with validation
        grievance_type = request.form.get('grievance_type', '').strip()
        priority = request.form.get('priority', '').strip()
        description = request.form.get('description', '').strip()
        additional_context = request.form.get('additional_context', '').strip()
        
        # Validate required fields
        if not grievance_type:
            flash('Please select a grievance type.', 'error')
            return redirect(url_for('portal'))
        
        if not priority:
            flash('Please select a priority level.', 'error')
            return redirect(url_for('portal'))
        
        if not description:
            flash('Please provide a description of your grievance.', 'error')
            return redirect(url_for('portal'))
        
        # Add grievance to database
        grievance_id = add_grievance_to_db(
            grievance_type, 
            priority, 
            description, 
            additional_context,
            session.get('username', WIFE_USERNAME)
        )
        
        # Prepare grievance data for notifications
        grievance_data = {
            'grievance_type': grievance_type,
            'priority': priority,
            'description': description,
            'additional_context': additional_context,
            'submitted_by': session.get('username', WIFE_USERNAME)
        }
        
        # Send notifications
        sms_sent = send_grievance_notification()
        email_sent = send_gmail_notification(grievance_data)
        
        # Provide feedback based on what notifications were sent
        if sms_sent and email_sent:
            flash('Your grievance has been submitted! Ushnish has been notified via SMS and email! 💕', 'success')
        elif sms_sent:
            flash('Your grievance has been submitted and Ushnish has been notified via SMS! 💕', 'success')
        elif email_sent:
            flash('Your grievance has been submitted and Ushnish has been notified via email! 💕', 'success')
        else:
            flash('Your grievance has been saved! Ushnish can view it in his portal.', 'success')
        
        logger.info(f"New grievance submitted: ID {grievance_id}, Type: {grievance_type}, Priority: {priority}")
        
    except ValueError as e:
        flash(str(e), 'error')
    except Exception as e:
        logger.error(f"Error submitting grievance: {str(e)}")
        flash('An error occurred while submitting your grievance. Please try again.', 'error')
    
    return redirect(url_for('portal'))

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    username = session.get('username', 'Unknown')
    session.clear()
    logger.info(f"User logged out: {username}")
    
    if request.method == 'POST':
        return '', 204  # No content for sendBeacon
    return redirect(url_for('index'))

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return render_template('500.html'), 500

# Initialize database when the module is imported
try:
    init_db()
    logger.info("Database initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")

if __name__ == '__main__':
    # Security: Don't run with debug=True in production
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '127.0.0.1')  # Changed from 0.0.0.0 for security
    
    logger.info(f"Starting Flask app on {host}:{port}, debug={debug_mode}")
    app.run(host=host, port=port, debug=debug_mode)
