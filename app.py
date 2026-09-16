import os
import io
import json
import socket
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw

# Import our new Port 9100 Bridge
from print_bridge import format_image, send_to_canon

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
    'logo_url': '/static/logo.png',
    'rates': {
        'bw_single': 2.0,
        'bw_double': 5.0,
        'color_single': 5.0,
        'color_double': 0.50
    },
    'paper_rates': {
        'A4': 0.0, 'Letter': 0.0, 'Legal': 1.0, 'A5': 0.0,
        'B5': 0.0, '4x6': 10.0, '5x7': 15.0, 'Card': 5.0
    },
    'layout_rates': {
        '1_photo': 0.0, '1_full': 0.0, '2_tb': 2.0, '2_lr': 2.0,
        '4_grid': 4.0, 'passport': 10.0, 'custom': 5.0
    },
    'media_rates': {
        'Plain Paper': 0.0,
        'Photo Paper Plus Glossy II': 10.0,
        'Photo Paper Pro Luster': 12.0,
        'Photo Paper Plus Semi-gloss': 10.0,
        'Glossy Photo Paper': 8.0,
        'Matte Photo Paper': 10.0,
        'High Resolution Paper': 5.0
    }
}

def load_settings():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, 'r') as f:
            data = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
                elif isinstance(v, dict):
                    for sub_k, sub_v in v.items():
                        if sub_k not in data[k]:
                            data[k][sub_k] = sub_v
            return data
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

# ================= APP ROUTES =================
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
    
    if 'rates' in data: SETTINGS['rates'].update({k: float(v) for k, v in data['rates'].items()})
    if 'paper_rates' in data: SETTINGS['paper_rates'].update({k: float(v) for k, v in data['paper_rates'].items()})
    if 'layout_rates' in data: SETTINGS['layout_rates'].update({k: float(v) for k, v in data['layout_rates'].items()})
    if 'media_rates' in data: SETTINGS['media_rates'].update({k: float(v) for k, v in data['media_rates'].items()})
        
    save_settings(SETTINGS)
    return jsonify({'success': True, 'settings': SETTINGS})

@app.route('/api/admin/logo/upload', methods=['POST'])
def upload_logo():
    if 'logo' not in request.files: return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['logo']
    if file.filename == '': return jsonify({'error': 'Empty filename'}), 400
    
    logo_path = os.path.join(app.config['STATIC_FOLDER'], 'logo.png')
    file.save(logo_path)
    SETTINGS['logo_url'] = f"/static/logo.png?t={int(datetime.now().timestamp())}"
    save_settings(SETTINGS)
    return jsonify({'success': True, 'logo_url': SETTINGS['logo_url']})

@app.route('/api/printer/status', methods=['GET'])
def get_printer_status():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    online = check_printer_socket(ip, 9100)
    return jsonify({'ip': ip, 'port': 9100, 'status': 'Online' if online else 'Offline'})

# ================= HARDWARE TESTING ROUTES =================
@app.route('/api/admin/test/blank', methods=['POST'])
def test_print_blank():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    try:
        # Create a tiny blank white image
        blank_im = Image.new('RGB', (100, 100), color=(255, 255, 255))
        buf = io.BytesIO()
        blank_im.save(buf, format='JPEG', quality=85)
        
        success, msg = send_to_canon(buf.getvalue(), ip, port=9100)
        return jsonify({'success': success, 'message': msg, 'error': msg if not success else None}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/admin/test/image', methods=['POST'])
def test_print_image():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    try:
        test_im = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
        draw = ImageDraw.Draw(test_im)
        draw.rectangle([100, 100, 2380, 3408], outline=(0, 0, 0), width=6)
        
        colors = [(0, 255, 255), (255, 0, 255), (255, 255, 0), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
        start_y = 600
        for col in colors:
            draw.rectangle([300, start_y, 2180, start_y + 150], fill=col, outline=(0, 0, 0), width=2)
            start_y += 220

        buf = io.BytesIO()
        test_im.save(buf, format='JPEG', quality=95)

        success, msg = send_to_canon(buf.getvalue(), ip, port=9100)
        return jsonify({'success': success, 'message': msg, 'error': msg if not success else None}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200

# ================= CUSTOMER UPLOAD & QUEUE ROUTES =================
@app.route('/upload', methods=['POST'])
def handle_upload():
    global job_counter
    uploaded_files = request.files.getlist('files')
    if not uploaded_files or uploaded_files[0].filename == '':
        return jsonify({'error': 'No file selected'}), 400

    saved_filenames = []
    for file in uploaded_files:
        filename = secure_filename(file.filename)
        unique_name = f"{job_counter}_{filename}"
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(save_path)
        saved_filenames.append(unique_name)

    custom_rows = request.form.get('custom_rows', '3')
    custom_cols = request.form.get('custom_cols', '3')
    layout = request.form.get('layout', '1_photo')

    job = {
        'id': job_counter,
        'filenames': saved_filenames,
        'primary_file': saved_filenames[0],
        'copies': int(request.form.get('copies', 1)),
        'color_mode': request.form.get('color_mode', 'bw'),
        'print_side': request.form.get('print_side', 'single'),
        'layout': f"Custom ({custom_rows}x{custom_cols})" if layout == 'custom' else layout,
        'paper_size': request.form.get('paper_size', 'A4'),
        'media_type': request.form.get('media_type', 'Plain Paper'),
        'border': request.form.get('border', 'Bordered'),
        'total_price': float(request.form.get('total_price', 0.0)),
        'time': datetime.now().strftime('%d %b, %I:%M %p'),
        'payment_status': 'Pending Verification',
        'print_status': 'Waiting'
    }
    PRINT_JOBS.append(job)
    job_counter += 1
    return jsonify({'success': True, 'job': job})

@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    return jsonify(PRINT_JOBS)

@app.route('/api/job/<int:job_id>', methods=['GET'])
def get_single_job(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    return jsonify(target) if target else (jsonify({'error': 'Job not found'}), 404)

@app.route('/api/jobs/verify_and_print/<int:job_id>', methods=['POST'])
def verify_and_print(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if not target: 
        return jsonify({'error': 'Job not found'}), 404

    target['payment_status'] = 'Paid'
    target['print_status'] = 'Printing'

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], target['primary_file'])
    ip = SETTINGS.get('printer_ip', '192.168.1.15')

    try:
        # Prepare the stream via bridge
        payload = format_image(file_path, target.get('paper_size', 'A4'), target.get('color_mode', 'bw'))
        
        for _ in range(int(target.get('copies', 1))):
            success, msg = send_to_canon(payload, ip, port=9100)
            if not success:
                target['print_status'] = 'Failed'
                return jsonify({'success': False, 'error': msg}), 200
                
        target['print_status'] = 'Completed'
        return jsonify({'success': True, 'message': 'Print sent'})
    except Exception as e:
        target['print_status'] = 'Failed'
        return jsonify({'success': False, 'error': str(e)}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
