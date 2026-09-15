import os
import json
import socket
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs('uploads', exist_ok=True)
os.makedirs('static', exist_ok=True)

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
    if any(k in media_type for k in ['Glossy', 'Pro Luster', 'Semi-gloss']):
        unit = rates.get('photo_glossy', 20.0)
    elif any(k in media_type for k in ['Matte', 'High Resolution']):
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
    if 'printer_port' in data: SETTINGS['printer_port'] = int(data['printer_port'])
    if 'rates' in data: SETTINGS['rates'].update(data['rates'])
    save_settings(SETTINGS)
    return jsonify({'success': True, 'settings': SETTINGS})

@app.route('/api/printer/status', methods=['GET'])
def get_printer_status():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    port_9100 = check_printer_socket(ip, 9100)
    port_ipp = check_printer_socket(ip, 631)
    status = 'Online (Ready)' if (port_9100 or port_ipp) else 'Offline'
    return jsonify({
        'ip': ip,
        'raw_port_9100': port_9100,
        'ipp_port_631': port_ipp,
        'status': status
    })

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
        'print_status': 'Waiting',
        'error_log': ''
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

def send_ipp_print_request(printer_ip, file_path, doc_format="image/jpeg"):
    """
    Constructs an IPP 2.0 Print-Job binary frame to feed the Canon G3010
    directly via standard network printing port 631.
    """
    import urllib.request
    
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    # Minimal IPP 2.0 Print-Job Header
    # Version: 2.0 (0x02, 0x00) | Operation: Print-Job (0x00, 0x02) | Request-ID: 1
    ipp_req = bytearray([0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01])
    
    # Operation attributes group tag (0x01)
    ipp_req.append(0x01)

    def append_attr(tag, name, val):
        ipp_req.append(tag)
        ipp_req.extend(len(name).to_bytes(2, 'big'))
        ipp_req.extend(name.encode('utf-8'))
        ipp_req.extend(len(val).to_bytes(2, 'big'))
        ipp_req.extend(val.encode('utf-8'))

    append_attr(0x47, "attributes-charset", "utf-8")
    append_attr(0x48, "attributes-natural-language", "en")
    append_attr(0x45, "printer-uri", f"ipp://{printer_ip}:631/ipp/print")
    append_attr(0x49, "document-format", doc_format)

    # End of attributes tag (0x03)
    ipp_req.append(0x03)
    ipp_req.extend(file_bytes)

    # Dispatch over HTTP/IPP to port 631
    url = f"http://{printer_ip}:631/ipp/print"
    req = urllib.request.Request(url, data=bytes(ipp_req), headers={'Content-Type': 'application/ipp'})
    
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status in (200, 201)

@app.route('/api/jobs/verify_and_print/<int:job_id>', methods=['POST'])
def verify_and_print(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if not target:
        return jsonify({'error': 'Job not found'}), 404

    target['payment_status'] = 'Paid'
    target['print_status'] = 'Printing'

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], target['filename'])
    ip = SETTINGS.get('printer_ip', '192.168.1.15')

    # Convert uploaded image to JPEG for Canon rasterization
    processed_path = file_path + "_canon.jpg"
    try:
        with Image.open(file_path) as im:
            if target['color_mode'] == 'Monochrome':
                im = im.convert('L')
            else:
                im = im.convert('RGB')
            im.save(processed_path, 'JPEG', quality=95)
        print_target = processed_path
    except Exception:
        print_target = file_path

    # Try IPP Port 631 first (Native Canon mobile protocol)
    try:
        success = send_ipp_print_request(ip, print_target, "image/jpeg")
        if success:
            target['print_status'] = 'Completed'
            return jsonify({'success': True, 'method': 'IPP Port 631'})
    except Exception as ipp_err:
        target['error_log'] = f"IPP 631 failed: {str(ipp_err)}. Attempting RAW 9100 stream..."

    # Fallback to Socket Stream on 9100
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((ip, 9100))
        with open(print_target, 'rb') as f:
            while chunk := f.read(4096):
                sock.send(chunk)
        sock.close()
        target['print_status'] = 'Completed'
        return jsonify({'success': True, 'method': 'Port 9100 RAW'})
    except Exception as raw_err:
        target['print_status'] = 'Failed'
        target['error_log'] += f" | RAW 9100 failed: {str(raw_err)}"
        return jsonify({'success': False, 'error': target['error_log']}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
