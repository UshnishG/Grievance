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

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE = os.getenv('TWILIO_PHONE')
YOUR_PHONE = os.getenv('YOUR_PHONE')

# Gmail Configuration
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_PASSWORD = os.getenv('GMAIL_PASSWORD')
RECIPIENT_EMAIL = os.getenv('RECIPIENT_EMAIL')

# Gemini AI Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent'

# Database Configuration
DATABASE = os.path.join(os.getenv('DATABASE', 'grievances.db'))

def init_db():
    """Initialize the database with required tables"""
    # Ensure the database directory exists and is writable
    db_dir = os.path.dirname(os.path.abspath(DATABASE))
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
    
    # Check if we can write to the directory
    if not os.access(db_dir, os.W_OK):
        print(f"Warning: No write permission to database directory: {db_dir}")
    
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
    
    # Set file permissions to be readable and writable
    try:
        os.chmod(DATABASE, 0o666)
    except:
        pass

def get_db_connection():
    """Get database connection with error handling"""
    try:
        conn = sqlite3.connect(DATABASE, timeout=20.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent access
        conn.execute('PRAGMA journal_mode=WAL;')
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        raise

def add_grievance_to_db(grievance_type, priority, description, additional_context, submitted_by):
    """Add a new grievance to the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO grievances (grievance_type, priority, description, additional_context, submitted_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (grievance_type, priority, description, additional_context, submitted_by))
        
        grievance_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return grievance_id
    except sqlite3.Error as e:
        print(f"Database insert error: {e}")
        if 'conn' in locals():
            conn.close()
        raise Exception(f"Database error: {str(e)}")

def get_all_grievances():
    """Get all grievances from database"""
    try:
        conn = get_db_connection()
        grievances = conn.execute('''
            SELECT * FROM grievances 
            ORDER BY date_submitted DESC
        ''').fetchall()
        conn.close()
        return grievances
    except sqlite3.Error as e:
        print(f"Database select error: {e}")
        if 'conn' in locals():
            conn.close()
        return []

def update_grievance_status(grievance_id, status, notes=None):
    """Update grievance status and add husband notes"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
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
        conn.close()
        return True
        
    except sqlite3.Error as e:
        print(f"Database update error: {e}")
        if 'conn' in locals():
            conn.close()
        raise Exception(f"Database error: {str(e)}")

# Initialize Twilio client
try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except Exception as e:
    twilio_client = None
    print(f"Twilio initialization error: {e}")

# Initialize database
init_db()

def send_gmail_notification(grievance_data):
    """Send Gmail notification about new grievance"""
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = f"💕 New Grievance Submitted - Priority: {grievance_data['priority']}"
        
        # Create email body
        body = f"""
Hi Ushnish! 💕

Puchu has submitted a new grievance that needs your attention:

🏷️ Type: {grievance_data['grievance_type']}
🚨 Priority: {grievance_data['priority']}
📝 Description: {grievance_data['description']}

"""
        
        if grievance_data['additional_context']:
            body += f"📋 Additional Context: {grievance_data['additional_context']}\n\n"
        
        body += f"""
📅 Submitted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
👤 Submitted by: {grievance_data['submitted_by']}

Please check your Husband Portal to respond and update the status.

With love,
Your Grievance Management System 💕
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Create SMTP session
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()  # Enable TLS encryption
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        
        # Send email
        text = msg.as_string()
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, text)
        server.quit()
        
        return True
        
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False

def send_grievance_notification():
    """Send a simple SMS notification that a grievance has been added"""
    if twilio_client is None:
        return False
    
    try:
        message = twilio_client.messages.create(
            body="💕 New grievance submitted by Puchu! Check the Husband Portal for details.",
            from_=TWILIO_PHONE,
            to=YOUR_PHONE
        )
        return True
    except Exception as e:
        print(f"Failed to send SMS: {str(e)}")
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
    username = request.form['username']
    password = request.form['password']
    
    if username == 'Wifey' and password == 'Ushnish@1330':
        session['logged_in'] = True
        session['username'] = username
        session['user_type'] = 'wife'
        return redirect(url_for('portal'))
    elif username == 'Hubby' and password == 'Aatreyee@3013':
        session['logged_in'] = True
        session['username'] = username
        session['user_type'] = 'husband'
        return redirect(url_for('husband_portal'))
    else:
        flash('Invalid credentials! Please check your username and password.')
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
        grievance_id = request.json.get('id')
        status = request.json.get('status')
        notes = request.json.get('notes', '')
        
        update_grievance_status(grievance_id, status, notes)
        
        return jsonify({'success': True, 'message': 'Grievance updated successfully!'})
    
    except Exception as e:
        print(f"Error updating grievance: {str(e)}")
        return jsonify({'error': f'Error updating grievance: {str(e)}'}), 500

@app.route('/chatbot', methods=['POST'])
def chatbot():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_message = request.json.get('message', '')
    
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
            json=payload
        )
        
        if response.status_code == 200:
            data = response.json()
            bot_response = data['candidates'][0]['content']['parts'][0]['text']
            return jsonify({'response': bot_response})
        else:
            return jsonify({'error': 'Sorry, I had trouble connecting to my thoughts. Try again?'})
            
    except Exception as e:
        print(f"Chatbot error: {str(e)}")
        return jsonify({'error': 'Something went wrong. I\'m here when you\'re ready to try again.'})

@app.route('/submit_grievance', methods=['POST'])
def submit_grievance():
    if 'logged_in' not in session or session.get('user_type') != 'wife':
        return redirect(url_for('portal'))
    
    try:
        # Get form data with validation
        grievance_type = request.form.get('grievance_type', 'Not specified')
        priority = request.form.get('priority', 'Not specified')
        description = request.form.get('description', 'No description provided')
        additional_context = request.form.get('additional_context', '')
        
        # Validate required fields
        if not description.strip():
            flash('Please provide a description of your grievance.', 'error')
            return redirect(url_for('portal'))
        
        # Add grievance to database
        grievance_id = add_grievance_to_db(
            grievance_type, 
            priority, 
            description, 
            additional_context,
            session.get('username', 'Wifey')
        )
        
        # Prepare grievance data for notifications
        grievance_data = {
            'grievance_type': grievance_type,
            'priority': priority,
            'description': description,
            'additional_context': additional_context,
            'submitted_by': session.get('username', 'Wifey')
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
        
    except Exception as e:
        print(f"Error submitting grievance: {str(e)}")
        flash(f'Error submitting grievance: {str(e)}', 'error')
    
    return redirect(url_for('portal'))

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    if request.method == 'POST':
        return '', 204  # No content for sendBeacon
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
