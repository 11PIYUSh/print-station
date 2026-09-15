import os
import json
import socket
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['STATIC_FOLDER'] = 'static'
os.makedirs('uploads', exist_ok=True)
os.makedirs('static', exist_ok=True)

CONFIG_FILE = 'settings.json'
DEFAULT_CONFIG = {
    'shop_name': 'Satya Xerox',
    'tagline': 'ONLINE PRINT PORTAL',
    'upi_id': 'piyush@upi',
    'payee_name': 'PIYUSH',
    'printer_ip': '192.168.1.15',
    'printer_port': 9100,
    'logo_url': '/static/logo.png',
    'rates': {
        'bw_single': 2.0,
        'bw_double': 5.0,
        'color_single': 5.0,
        'color_double': 0.50
    }
}

def load_settings():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG

def save_settings(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

SETTINGS = load_settings()
PRINT_JOBS = []
job_counter = 1

def check_printer_socket(ip, port=9100, timeout=1.5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        res = sock.connect_ex((ip, int(port)))
        sock.close()
        return res == 0
    except Exception:
        return False

@app.route('/')
def customer_portal():
    return render_template('index.html')

@app.route('/admin')
def admin_portal():
    return render_template('admin.html')

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(SETTINGS)

@app.route('/api/admin/config/update', methods=['POST'])
def update_admin_config():
    data = request.json or {}
    for key in ['shop_name', 'tagline', 'upi_id', 'payee_name', 'printer_ip']:
        if key in data and str(data[key]).strip():
            SETTINGS[key] = str(data[key]).strip()
    if 'rates' in data:
        for rk in ['bw_single', 'bw_double', 'color_single', 'color_double']:
            if rk in data['rates']:
                SETTINGS['rates'][rk] = float(data['rates'][rk])
    save_settings(SETTINGS)
    return jsonify({'success': True, 'settings': SETTINGS})

@app.route('/api/admin/logo/upload', methods=['POST'])
def upload_logo():
    if 'logo' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
    logo_path = os.path.join(app.config['STATIC_FOLDER'], 'logo.png')
    file.save(logo_path)
    SETTINGS['logo_url'] = f"/static/logo.png?t={int(datetime.now().timestamp())}"
    save_settings(SETTINGS)
    return jsonify({'success': True, 'logo_url': SETTINGS['logo_url']})

@app.route('/api/printer/status', methods=['GET'])
def get_printer_status():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    port = SETTINGS.get('printer_port', 9100)
    online = check_printer_socket(ip, port)
    return jsonify({'ip': ip, 'port': port, 'status': 'Online' if online else 'Offline'})

@app.route('/upload', methods=['POST'])
def handle_upload():
    global job_counter
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    filename = secure_filename(file.filename)
    unique_name = f"{job_counter}_{filename}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(save_path)

    copies = int(request.form.get('copies', 1))
    color_mode = request.form.get('color_mode', 'bw')
    print_side = request.form.get('print_side', 'single')
    layout = request.form.get('layout', '1_photo')
    total_price = float(request.form.get('total_price', 0.0))

    job = {
        'id': job_counter,
        'filename': unique_name,
        'original_name': filename,
        'copies': copies,
        'color_mode': color_mode,
        'print_side': print_side,
        'layout': layout,
        'total_price': total_price,
        'time': datetime.now().strftime('%d %b, %I:%M %p'),
        'payment_status': 'Pending Verification',
        'print_status': 'Waiting'
    }
    PRINT_JOBS.append(job)
    job_counter += 1
    return jsonify({'success': True, 'job': job})

@app.route('/api/job/<int:job_id>', methods=['GET'])
def get_single_job(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if not target:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(target)

@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    return jsonify(PRINT_JOBS)

@app.route('/api/jobs/verify_and_print/<int:job_id>', methods=['POST'])
def verify_and_print(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if not target:
        return jsonify({'error': 'Job not found'}), 404

    target['payment_status'] = 'Paid'
    target['print_status'] = 'Printing'

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], target['filename'])
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    port = int(SETTINGS.get('printer_port', 9100))

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(6.0)
        sock.connect((ip, port))
        with open(file_path, 'rb') as f:
            chunk = f.read(4096)
            while chunk:
                sock.send(chunk)
                chunk = f.read(4096)
        sock.close()
        target['print_status'] = 'Completed'
        return jsonify({'success': True, 'message': 'Print sent'})
    except Exception as e:
        target['print_status'] = 'Failed'
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
