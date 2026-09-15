import os
import json
import socket
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs('uploads', exist_ok=True)

CONFIG_FILE = 'settings.json'
DEFAULT_CONFIG = {
    'upi_id': 'piyush@upi',
    'payee_name': 'Maruti Print Point',
    'printer_ip': '192.168.1.15',
    'printer_port': 9100,
    'rates': {
        'plain_bw': 3.0,
        'plain_color': 10.0,
        'photo_glossy': 20.0,
        'photo_matte': 25.0
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

def calculate_amount(media_type, color_mode, copies):
    rates = SETTINGS.get('rates', DEFAULT_CONFIG['rates'])
    if 'Glossy' in media_type or 'Pro Luster' in media_type or 'Semi-gloss' in media_type:
        unit = rates.get('photo_glossy', 20.0)
    elif 'Matte' in media_type or 'High Resolution' in media_type:
        unit = rates.get('photo_matte', 25.0)
    else:
        unit = rates.get('plain_color', 10.0) if color_mode == 'Color' else rates.get('plain_bw', 3.0)
    return round(float(unit) * int(copies), 2)

@app.route('/')
def customer_portal():
    return render_template('index.html')

@app.route('/admin')
def admin_portal():
    return render_template('admin.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({
        'upi_id': SETTINGS.get('upi_id'),
        'payee_name': SETTINGS.get('payee_name'),
        'printer_ip': SETTINGS.get('printer_ip'),
        'rates': SETTINGS.get('rates')
    })

@app.route('/api/admin/config/update', methods=['POST'])
def update_admin_config():
    data = request.json or {}
    if 'upi_id' in data: SETTINGS['upi_id'] = data['upi_id'].strip()
    if 'payee_name' in data: SETTINGS['payee_name'] = data['payee_name'].strip()
    if 'printer_ip' in data: SETTINGS['printer_ip'] = data['printer_ip'].strip()
    if 'rates' in data: SETTINGS['rates'].update(data['rates'])
    save_settings(SETTINGS)
    return jsonify({'success': True, 'settings': SETTINGS})

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
    paper_size = request.form.get('paper_size', 'A4')
    media_type = request.form.get('media_type', 'Plain Paper')
    border = request.form.get('border', 'Bordered')
    color_mode = request.form.get('color_mode', 'Color')

    total_price = calculate_amount(media_type, color_mode, copies)

    job = {
        'id': job_counter,
        'filename': unique_name,
        'original_name': filename,
        'copies': copies,
        'paper_size': paper_size,
        'media_type': media_type,
        'border': border,
        'color_mode': color_mode,
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
        return jsonify({'success': True, 'message': 'Print dispatched successfully'})
    except Exception as e:
        target['print_status'] = 'Failed'
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
