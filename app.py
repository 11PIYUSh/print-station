import io
import os
import json
import socket
import struct
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
    'printer_port': 9100,
    'logo_url': '/static/logo.png',
    'rates': {
        'bw_single': 2.0,
        'bw_double': 5.0,
        'color_single': 5.0,
        'color_double': 0.50
    },
    'paper_rates': {
        'A4': 0.0,
        'Letter': 0.0,
        'Legal': 1.0,
        'A5': 0.0,
        'B5': 0.0,
        '4x6': 10.0,
        '5x7': 15.0,
        'Card': 5.0
    },
    'layout_rates': {
        '1_photo': 0.0,
        '1_full': 0.0,
        '2_tb': 2.0,
        '2_lr': 2.0,
        '4_grid': 4.0,
        'passport': 10.0,
        'custom': 5.0
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

def check_printer_socket(ip, port=631, timeout=1.5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        res = sock.connect_ex((ip, int(port)))
        sock.close()
        return res == 0
    except Exception:
        return False

def compress_pwg_scanline(raw_line):
    out = bytearray([0])
    i = 0
    n = len(raw_line)
    while i < n:
        run = 1
        while i + run < n and run < 128 and raw_line[i + run] == raw_line[i]:
            run += 1
        if run > 1:
            out.append(257 - run)
            out.append(raw_line[i])
            i += run
        else:
            lit_start = i
            while i < n and (i - lit_start < 128):
                if i + 1 < n and raw_line[i] == raw_line[i + 1]:
                    break
                i += 1
            lit_len = i - lit_start
            if lit_len > 0:
                out.append(lit_len - 1)
                out.extend(raw_line[lit_start:i])
    return bytes(out)

def build_canon_pwg_from_image(im, paper_size="A4", color_mode="Color", dpi=300):
    dimensions = {
        'A4': (2480, 3508),
        'Letter': (2550, 3300),
        'Legal': (2550, 4200),
        '4x6': (1200, 1800),
        '5x7': (1500, 2100),
        'Card': (651, 1074)
    }
    target_w, target_h = dimensions.get(paper_size, (2480, 3508))

    im = ImageOps.exif_transpose(im)
    im.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
    
    if color_mode == 'Monochrome':
        canvas = Image.new('L', (target_w, target_h), color=255)
        im = im.convert('L')
        canvas.paste(im, ((target_w - im.width) // 2, (target_h - im.height) // 2))
        bits_per_color = 8
        bits_per_pixel = 8
        color_space = 1
    else:
        canvas = Image.new('RGB', (target_w, target_h), color=(255, 255, 255))
        im = im.convert('RGB')
        canvas.paste(im, ((target_w - im.width) // 2, (target_h - im.height) // 2))
        bits_per_color = 8
        bits_per_pixel = 24
        color_space = 19

    pwg_sync = b'RaS2'
    pwg_header = bytearray(512)
    pwg_header[0:64] = b'Canon_G3010'.ljust(64, b'\x00')
    
    bytes_per_line = (target_w * bits_per_pixel) // 8
    struct.pack_into('>I', pwg_header, 280, dpi)
    struct.pack_into('>I', pwg_header, 284, dpi)
    struct.pack_into('>I', pwg_header, 344, target_w)
    struct.pack_into('>I', pwg_header, 348, target_h)
    struct.pack_into('>I', pwg_header, 352, bits_per_color)
    struct.pack_into('>I', pwg_header, 356, bits_per_pixel)
    struct.pack_into('>I', pwg_header, 360, bytes_per_line)
    struct.pack_into('>I', pwg_header, 364, 0)
    struct.pack_into('>I', pwg_header, 368, color_space)
    struct.pack_into('>I', pwg_header, 372, 1)

    compressed_body = io.BytesIO()
    raw_pixels = canvas.tobytes()
    for y in range(target_h):
        line = raw_pixels[y * bytes_per_line : (y + 1) * bytes_per_line]
        compressed_body.write(compress_pwg_scanline(line))

    return pwg_sync + bytes(pwg_header) + compressed_body.getvalue()

def send_ipp_print(pwg_payload, printer_ip, job_title="PrintJob", copies=1):
    url = f"http://{printer_ip}:631/ipp/printer"
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
    add_attr(0x45, 'printer-uri', f'ipp://{printer_ip}:631/ipp/printer')
    add_attr(0x42, 'job-name', job_title)
    add_attr(0x49, 'document-format', 'image/pwg-raster')
    ipp_data.append(0x03)

    full_request = bytes(ipp_data) + pwg_payload

    for _ in range(int(copies)):
        req = urllib.request.Request(url, data=full_request, headers={'Content-Type': 'application/ipp'})
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = resp.read()
            if len(data) >= 4:
                status_code = int.from_bytes(data[2:4], 'big')
                if status_code != 0x0000:
                    return False, f"IPP Error code: 0x{status_code:04X}"
    return True, "Success"

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
        SETTINGS['rates'].update({k: float(v) for k, v in data['rates'].items()})
    if 'paper_rates' in data:
        SETTINGS['paper_rates'].update({k: float(v) for k, v in data['paper_rates'].items()})
    if 'layout_rates' in data:
        SETTINGS['layout_rates'].update({k: float(v) for k, v in data['layout_rates'].items()})
    if 'media_rates' in data:
        SETTINGS['media_rates'].update({k: float(v) for k, v in data['media_rates'].items()})
        
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
    online = check_printer_socket(ip, 631)
    return jsonify({'ip': ip, 'port': 631, 'status': 'Online' if online else 'Offline'})

# Hardware Diagnostic Test Endpoints
@app.route('/api/admin/test/blank', methods=['POST'])
def test_print_blank():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    blank_im = Image.new('L', (2480, 3508), color=255)
    pwg = build_canon_pwg_from_image(blank_im, paper_size="A4", color_mode="Monochrome")
    success, msg = send_ipp_print(pwg, ip, job_title="Test_Blank_Page", copies=1)
    if success:
        return jsonify({'success': True, 'message': 'Blank page test dispatched!'})
    return jsonify({'success': False, 'error': msg}), 500

@app.route('/api/admin/test/image', methods=['POST'])
def test_print_image():
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    
    # Generate high-resolution test pattern
    test_im = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
    draw = ImageDraw.Draw(test_im)
    
    # Header box & target crosshairs
    draw.rectangle([100, 100, 2380, 3408], outline=(0, 0, 0), width=6)
    draw.line([1240, 200, 1240, 3308], fill=(200, 200, 200), width=2)
    draw.line([200, 1754, 2280, 1754], fill=(200, 200, 200), width=2)
    
    # Primary CMYK/RGB calibration bands
    bands = [
        ((0, 255, 255), "CYAN"),
        ((255, 0, 255), "MAGENTA"),
        ((255, 255, 0), "YELLOW"),
        ((0, 0, 0), "BLACK (K)"),
        ((255, 0, 0), "RED"),
        ((0, 255, 0), "GREEN"),
        ((0, 0, 255), "BLUE")
    ]
    
    start_y = 600
    for color, name in bands:
        draw.rectangle([300, start_y, 2180, start_y + 160], fill=color, outline=(0, 0, 0), width=2)
        start_y += 220
        
    pwg = build_canon_pwg_from_image(test_im, paper_size="A4", color_mode="Color")
    success, msg = send_ipp_print(pwg, ip, job_title="Test_Calibration_Image", copies=1)
    if success:
        return jsonify({'success': True, 'message': 'Diagnostic image test dispatched!'})
    return jsonify({'success': False, 'error': msg}), 500

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

    copies = int(request.form.get('copies', 1))
    color_mode = request.form.get('color_mode', 'bw')
    print_side = request.form.get('print_side', 'single')
    layout = request.form.get('layout', '1_photo')
    custom_rows = request.form.get('custom_rows', '3')
    custom_cols = request.form.get('custom_cols', '3')
    paper_size = request.form.get('paper_size', 'A4')
    media_type = request.form.get('media_type', 'Plain Paper')
    border = request.form.get('border', 'Bordered')
    total_price = float(request.form.get('total_price', 0.0))

    job = {
        'id': job_counter,
        'filenames': saved_filenames,
        'primary_file': saved_filenames[0],
        'copies': copies,
        'color_mode': color_mode,
        'print_side': print_side,
        'layout': f"Custom ({custom_rows}x{custom_cols})" if layout == 'custom' else layout,
        'paper_size': paper_size,
        'media_type': media_type,
        'border': border,
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

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], target['primary_file'])
    ip = SETTINGS.get('printer_ip', '192.168.1.15')

    try:
        with Image.open(file_path) as im:
            mode = "Monochrome" if target['color_mode'] == 'bw' else "Color"
            pwg = build_canon_pwg_from_image(im, paper_size=target.get('paper_size', 'A4'), color_mode=mode)
            
        success, msg = send_ipp_print(pwg, ip, job_title=f"Order_{target['id']}", copies=target.get('copies', 1))
        if success:
            target['print_status'] = 'Completed'
            return jsonify({'success': True, 'message': 'Print sent'})
        else:
            target['print_status'] = 'Failed'
            return jsonify({'success': False, 'error': msg}), 500
    except Exception as e:
        target['print_status'] = 'Failed'
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
