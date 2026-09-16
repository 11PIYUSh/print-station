import io
import os
import json
import socket
import urllib.request
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps, ImageDraw

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
    'rates': {'bw_single': 2.0, 'bw_double': 5.0, 'color_single': 5.0, 'color_double': 0.50},
    'paper_rates': {'A4': 0.0, 'Letter': 0.0, 'Legal': 1.0, 'A5': 0.0, 'B5': 0.0, '4x6': 10.0, '5x7': 15.0, 'Card': 5.0},
    'layout_rates': {'1_photo': 0.0, '1_full': 0.0, '2_tb': 2.0, '2_lr': 2.0, '4_grid': 4.0, 'passport': 10.0, 'custom': 5.0},
    'media_rates': {'Plain Paper': 0.0, 'Glossy Photo Paper': 8.0, 'Matte Photo Paper': 10.0}
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

def check_printer_socket(ip, port=631, timeout=1.5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        res = sock.connect_ex((ip, int(port)))
        sock.close()
        return res == 0
    except Exception:
        return False

# ================= PRINT ENGINE =================
def format_image_for_printer(im, paper_size="A4", color_mode="Color"):
    """Converts the image into a perfectly sized, high-quality JPEG payload for IPP."""
    dimensions = {
        'A4': (2480, 3508), 'Letter': (2550, 3300), 'Legal': (2550, 4200),
        '4x6': (1200, 1800), '5x7': (1500, 2100), 'Card': (651, 1074)
    }
    target_w, target_h = dimensions.get(paper_size, (2480, 3508))

    im = ImageOps.exif_transpose(im)
    im.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
    
    canvas = Image.new('RGB', (target_w, target_h), color=(255, 255, 255))
    
    if color_mode == 'Monochrome':
        im = im.convert('L').convert('RGB')
    else:
        im = im.convert('RGB')
        
    canvas.paste(im, ((target_w - im.width) // 2, (target_h - im.height) // 2))
    
    buf = io.BytesIO()
    canvas.save(buf, format='JPEG', quality=95, optimize=True)
    return buf.getvalue()

def send_ipp_print(image_bytes, printer_ip, job_title="PrintJob", copies=1):
    """Sends the formatted JPEG directly to the Canon IPP endpoint."""
    endpoints = [f"http://{printer_ip}:631/ipp/print", f"http://{printer_ip}:631/ipp/printer"]
    
    ipp_data = bytearray([0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x01])
    
    def add_attr(tag, name, value):
        ipp_data.append(tag)
        ipp_data.extend(len(name).to_bytes(2, 'big'))
        ipp_data.extend(name.encode('utf-8'))
        val_bytes = value.encode('utf-8') if isinstance(value, str) else value
        ipp_data.extend(len(val_bytes).to_bytes(2, 'big'))
        ipp_data.extend(val_bytes)

    add_attr(0x47, 'attributes-charset', 'utf-8')
    add_attr(0x48, 'attributes-natural-language', 'en-us')
    add_attr(0x45, 'printer-uri', f'ipp://{printer_ip}:631/ipp/print')
    add_attr(0x42, 'job-name', job_title)
    add_attr(0x49, 'document-format', 'image/jpeg')  # universally accepted by Mopria/AirPrint
    ipp_data.append(0x03)
    
    full_request = bytes(ipp_data) + image_bytes

    last_error = ""
    for _ in range(int(copies)):
        success_for_copy = False
        for url in endpoints:
            try:
                req = urllib.request.Request(url, data=full_request, headers={'Content-Type': 'application/ipp'})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    if len(data) >= 4:
                        status_code = int.from_bytes(data[2:4], 'big')
                        if status_code in (0x0000, 0x0001, 0x0002):
                            success_for_copy = True
                            break
                        else:
                            last_error = f"Rejected (Code 0x{status_code:04X})"
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.reason}"
            except Exception as e:
                last_error = str(e)
                
        if not success_for_copy:
            return False, last_error
            
    return True, "Job accepted. Printer should blink now!"

# ================= APP ROUTES =================
@app.route('/')
def customer_portal(): return render_template('index.html')

@app.route('/admin')
def admin_portal(): return render_template('admin.html')

@app.route('/uploads/<path:filename>')
def serve_upload(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/config', methods=['GET'])
def get_config(): return jsonify(SETTINGS)

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
    logo_path = os.path.join(app.config['STATIC_FOLDER'], 'logo.png')
    file.save(logo_path)
    SETTINGS['logo_url'] = f"/static/logo.png?t={int(datetime.now().timestamp())}"
    save_settings(SETTINGS)
    return jsonify({'success': True, 'logo_url': SETTINGS['logo_url']})

@app.route('/api/printer/status', methods=['GET'])
def get_printer_status():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    online = check_printer_socket(ip, 631)
    return jsonify({'ip': ip, 'port': 631, 'status': 'Online' if online else 'Offline'})

@app.route('/api/admin/test/blank', methods=['POST'])
def test_print_blank():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    try:
        blank_im = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
        payload = format_image_for_printer(blank_im, paper_size="A4", color_mode="Monochrome")
        success, msg = send_ipp_print(payload, ip, job_title="Test_Blank_Page")
        return jsonify({'success': success, 'message': msg if success else 'Failed', 'error': msg if not success else None}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': f"Processing error: {str(e)}"}), 200

@app.route('/api/admin/test/image', methods=['POST'])
def test_print_image():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    try:
        test_im = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
        draw = ImageDraw.Draw(test_im)
        draw.rectangle([100, 100, 2380, 3408], outline=(0, 0, 0), width=6)
        draw.line([1240, 200, 1240, 3308], fill=(180, 180, 180), width=3)
        draw.line([200, 1754, 2280, 1754], fill=(180, 180, 180), width=3)

        colors = [(0, 255, 255), (255, 0, 255), (255, 255, 0), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
        start_y = 600
        for col in colors:
            draw.rectangle([300, start_y, 2180, start_y + 150], fill=col, outline=(0, 0, 0), width=2)
            start_y += 220

        payload = format_image_for_printer(test_im, paper_size="A4", color_mode="Color")
        success, msg = send_ipp_print(payload, ip, job_title="Test_Color_Page")
        return jsonify({'success': success, 'message': msg if success else 'Failed', 'error': msg if not success else None}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': f"Processing error: {str(e)}"}), 200

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
def list_jobs(): return jsonify(PRINT_JOBS)

@app.route('/api/job/<int:job_id>', methods=['GET'])
def get_single_job(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    return jsonify(target) if target else (jsonify({'error': 'Job not found'}), 404)

@app.route('/api/jobs/verify_and_print/<int:job_id>', methods=['POST'])
def verify_and_print(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if not target: return jsonify({'error': 'Job not found'}), 404

    target['payment_status'] = 'Paid'
    target['print_status'] = 'Printing'

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], target['primary_file'])
    ip = SETTINGS.get('printer_ip', '192.168.1.15')

    try:
        with Image.open(file_path) as im:
            mode = "Monochrome" if target['color_mode'] == 'bw' else "Color"
            payload = format_image_for_printer(im, paper_size=target.get('paper_size', 'A4'), color_mode=mode)
            
        success, msg = send_ipp_print(payload, ip, job_title=f"Order_{target['id']}", copies=target.get('copies', 1))
        
        if success:
            target['print_status'] = 'Completed'
            return jsonify({'success': True, 'message': 'Print sent'})
        else:
            target['print_status'] = 'Failed'
            return jsonify({'success': False, 'error': msg}), 200
    except Exception as e:
        target['print_status'] = 'Failed'
        return jsonify({'success': False, 'error': str(e)}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
