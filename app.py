import os
import json
import socket
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw

import print_bridge

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs('uploads', exist_ok=True)
os.makedirs('static', exist_ok=True)

CONFIG_FILE = 'settings.json'
DEFAULT_CONFIG = {
    'upi_id': 'piyush@upi',
    'payee_name': 'Maruti Print Point',
    'printer_ip': '192.168.1.16',
    'printer_port': 9100,
    'rates': {
        'A4': 3.0,
        'Letter': 3.0,
        'Legal': 4.0,
        'A5': 2.5,
        'B5': 3.0,
        '4x6': 15.0,
        '5x7': 20.0,
        '8x10': 35.0,
        'L': 12.0,
        '2L': 18.0,
        'Square': 15.0,
        'Hagaki': 10.0,
        'Card': 8.0,
        'color_addon': 7.0,
        'photo_paper_addon': 10.0
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
            for k, v in DEFAULT_CONFIG['rates'].items():
                if k not in data.get('rates', {}):
                    data.setdefault('rates', {})[k] = v
            return data
    except Exception:
        return DEFAULT_CONFIG

def save_settings(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

SETTINGS = load_settings()
PRINT_JOBS = []
job_counter = 1

def check_printer_socket(ip, port, timeout=1.2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        res = sock.connect_ex((ip, int(port)))
        sock.close()
        return res == 0
    except Exception:
        return False

def calculate_amount(paper_size, media_type, color_mode, copies):
    rates = SETTINGS.get('rates', DEFAULT_CONFIG['rates'])
    base_rate = float(rates.get(paper_size, rates.get('A4', 3.0)))
    if color_mode == 'Color':
        base_rate += float(rates.get('color_addon', 7.0))
    if any(k in media_type for k in ['Photo', 'Glossy', 'Luster']):
        base_rate += float(rates.get('photo_paper_addon', 10.0))
    return round(base_rate * int(copies), 2)

@app.route('/')
def customer_portal():
    return render_template('index.html')

@app.route('/admin')
def admin_portal():
    return render_template('admin.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(SETTINGS)

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
    ip = SETTINGS.get('printer_ip', '192.168.1.16')
    p9100 = check_printer_socket(ip, 9100)
    p631 = check_printer_socket(ip, 631)
    status = 'Online' if (p9100 or p631) else 'Offline'
    return jsonify({
        'ip': ip,
        'raw_port_9100': p9100,
        'ipp_port_631': p631,
        'status': status
    })

@app.route('/api/printer/test_blank', methods=['POST'])
def test_blank_page():
    test_path = os.path.join(app.config['UPLOAD_FOLDER'], 'test_page.jpg')
    # Generate test page with actual visible banner so it doesn't print blank
    img = Image.new('RGB', (1200, 1600), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(100, 100), (1100, 300)], fill=(20, 20, 20))
    draw.rectangle([(150, 400), (1050, 500)], fill=(0, 120, 220))
    img.save(test_path, 'JPEG')
    
    ip = SETTINGS.get('printer_ip', '192.168.1.16')
    success, msg = print_bridge.dispatch_print(test_path, ip, paper_size='A4', color_mode='Color', copies=1)
    if success:
        return jsonify({'success': True, 'method': msg})
    return jsonify({'success': False, 'error': msg}), 500

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

    total_price = calculate_amount(paper_size, media_type, color_mode, copies)

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
    if not target: return jsonify({'error': 'Not found'}), 404
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
    ip = SETTINGS.get('printer_ip', '192.168.1.16')

    success, msg = print_bridge.dispatch_print(
        file_path=file_path,
        printer_ip=ip,
        paper_size=target.get('paper_size', 'A4'),
        color_mode=target.get('color_mode', 'Color'),
        copies=target.get('copies', 1)
    )

    if success:
        target['print_status'] = 'Completed'
        return jsonify({'success': True, 'method': msg})
    else:
        target['print_status'] = 'Failed'
        return jsonify({'success': False, 'error': msg}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
