import os
import re
import io
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
    'shop_name': 'Piush Xerox',
    'tagline': 'ONLINE PRINT PORTAL',
    'upi_id': 'piush@upi',
    'payee_name': 'PIUSH',
    'printer_ip': '192.168.1.15',
    'printer_port': 9100,
    'printer_protocol': 'RAW',  # Options: 'RAW' or 'IPP'
    'printer_format': 'PDF',    # Options: 'PDF' or 'JPEG'
    'logo_url': '/static/logo.png',
    'rates': {'bw_single': 2.0, 'bw_double': 5.0, 'color_single': 5.0, 'color_double': 0.50},
    'paper_rates': {'A4': 0.0, 'Letter': 0.0, 'Legal': 1.0, 'A5': 0.0, 'B5': 0.0, '4x6': 10.0, '5x7': 15.0, 'Card': 5.0},
    'layout_rates': {'1_photo': 0.0, '1_full': 0.0, '2_tb': 2.0, '2_lr': 2.0, '4_grid': 4.0, 'passport': 10.0, 'custom': 5.0},
    'media_rates': {'Plain Paper': 0.0, 'Photo Paper Plus Glossy II': 10.0, 'Matte Photo Paper': 10.0}
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
                if k not in data: data[k] = v
            return data
    except Exception:
        return DEFAULT_CONFIG

def save_settings(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

SETTINGS = load_settings()
PRINT_JOBS = []
job_counter = 1

# ================= DIRECT BACKEND PRINT ENGINE =================
def process_cell_image(img, cell_w, cell_h, fit_mode, zoom):
    if fit_mode == 'cover':
        img = ImageOps.fit(img, (cell_w, cell_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    elif fit_mode == 'fill':
        img = img.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
    else:
        img = ImageOps.pad(img, (cell_w, cell_h), color=(255, 255, 255))
        
    if zoom != 1.0:
        zw, zh = int(cell_w * zoom), int(cell_h * zoom)
        img = img.resize((zw, zh), Image.Resampling.LANCZOS)
        left = (zw - cell_w) // 2
        top = (zh - cell_h) // 2
        img = img.crop((left, top, left + cell_w, top + cell_h))
    return img

def compile_job_canvas(job):
    filenames = job.get('filenames', [])
    if not filenames: return None

    dimensions = {
        'A4': (2480, 3508), 'Letter': (2550, 3300), 'Legal': (2550, 4200),
        '4x6': (1200, 1800), '5x7': (1500, 2100), 'Card': (651, 1074)
    }
    canvas_w, canvas_h = dimensions.get(job.get('paper_size', 'A4'), (2480, 3508))
    canvas = Image.new('RGB', (canvas_w, canvas_h), color=(255, 255, 255))
    
    layout = job.get('layout', '1_photo')
    rows, cols = 1, 1
    if layout == '1_full': rows, cols = 1, 1
    elif layout == '2_tb': rows, cols = 2, 1
    elif layout == '2_lr': rows, cols = 1, 2
    elif layout == '4_grid': rows, cols = 2, 2
    elif layout == 'passport': rows, cols = 4, 2
    elif 'Custom' in layout:
        m = re.search(r'(\d+)x(\d+)', layout)
        if m: rows, cols = int(m.group(1)), int(m.group(2))

    fit_mode = job.get('fit_mode', 'contain')
    zoom = float(job.get('zoom', 1.0))
    rotation = int(job.get('rotation', 0))
    grid_gap = int(job.get('grid_gap', 40))
    
    margin = 0 if job.get('border', 'Bordered') == 'Borderless' else grid_gap
    cell_gap = 0 if job.get('border', 'Bordered') == 'Borderless' else grid_gap

    usable_w = canvas_w - (margin * 2)
    usable_h = canvas_h - (margin * 2)
    cell_w = (usable_w - (cols - 1) * cell_gap) // cols
    cell_h = (usable_h - (rows - 1) * cell_gap) // rows

    images = []
    for fn in filenames:
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], fn)
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            img = img.convert('L').convert('RGB') if job.get('color_mode') == 'bw' else img.convert('RGB')
            if rotation != 0:
                img = img.rotate(-rotation, expand=True, fillcolor=(255, 255, 255))
            images.append(img)
        except Exception: pass

    if not images: return None

    img_idx = 0
    for r in range(rows):
        for c in range(cols):
            src_img = images[img_idx % len(images)]
            img_idx += 1
            cell_img = process_cell_image(src_img, cell_w, cell_h, fit_mode, zoom)
            x = margin + c * (cell_w + cell_gap)
            y = margin + r * (cell_h + cell_gap)
            canvas.paste(cell_img, (x, y))

    return canvas

def transmit_to_printer(file_bytes, ip, port, protocol, data_format):
    """Multi-Protocol print dispatcher based on Admin Panel settings."""
    if protocol == 'RAW':
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15.0)
            sock.connect((ip, int(port)))
            sock.sendall(file_bytes)
            sock.close()
            return True, f"Sent via RAW Port {port}"
        except Exception as e:
            return False, f"RAW Socket Error: {str(e)}"
            
    elif protocol == 'IPP':
        try:
            url = f"http://{ip}:{port}/ipp/print"
            ipp_req = bytearray([0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x01])
            
            def add_attr(tag, name, value):
                ipp_req.append(tag)
                ipp_req.extend(len(name).to_bytes(2, 'big'))
                ipp_req.extend(name.encode())
                val_b = value.encode() if isinstance(value, str) else value
                ipp_req.extend(len(val_b).to_bytes(2, 'big'))
                ipp_req.extend(val_b)
                
            add_attr(0x47, 'attributes-charset', 'utf-8')
            add_attr(0x48, 'attributes-natural-language', 'en')
            add_attr(0x45, 'printer-uri', f'ipp://{ip}:{port}/ipp/print')
            add_attr(0x42, 'requesting-user-name', 'PiushAdmin')
            
            mime_type = 'application/pdf' if data_format == 'PDF' else 'image/jpeg'
            add_attr(0x49, 'document-format', mime_type)
            ipp_req.append(0x03)
            
            payload = bytes(ipp_req) + file_bytes
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/ipp'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in [200, 201]:
                    return True, "Sent via IPP Protocol"
                else:
                    return False, f"IPP Rejected (Status {resp.status})"
        except Exception as e:
            return False, f"IPP Error: {str(e)}"
    
    return False, "Unknown Protocol"

def secure_shred(job):
    for fn in job.get('filenames', []):
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        except: pass

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
    
    # Save network settings
    for key in ['shop_name', 'tagline', 'upi_id', 'payee_name', 'printer_ip', 'printer_port', 'printer_protocol', 'printer_format']:
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

# ================= HARDWARE DIAGNOSTICS =================
@app.route('/api/admin/test/<test_type>', methods=['POST'])
def run_hardware_test(test_type):
    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    port = SETTINGS.get('printer_port', 9100)
    protocol = SETTINGS.get('printer_protocol', 'RAW')
    fmt = SETTINGS.get('printer_format', 'PDF')

    try:
        canvas = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
        if test_type == 'image':
            draw = ImageDraw.Draw(canvas)
            draw.rectangle([100, 100, 2380, 3408], outline=(0, 0, 0), width=6)
            colors = [(0, 255, 255), (255, 0, 255), (255, 255, 0), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
            start_y = 600
            for col in colors:
                draw.rectangle([300, start_y, 2180, start_y + 150], fill=col, outline=(0, 0, 0), width=2)
                start_y += 220

        buf = io.BytesIO()
        if fmt == 'PDF':
            canvas.save(buf, format='PDF', resolution=300)
        else:
            canvas.save(buf, format='JPEG', quality=95)
            
        success, msg = transmit_to_printer(buf.getvalue(), ip, port, protocol, fmt)
        return jsonify({'success': success, 'message': msg, 'error': msg if not success else None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/upload', methods=['POST'])
def handle_upload():
    global job_counter
    uploaded_files = request.files.getlist('files')
    if not uploaded_files or uploaded_files[0].filename == '': return jsonify({'error': 'No file selected'}), 400

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
        'fit_mode': request.form.get('fit_mode', 'contain'),
        'zoom': float(request.form.get('zoom', 1.0)),
        'rotation': int(request.form.get('rotation', 0)),
        'grid_gap': int(request.form.get('grid_gap', 40)),
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

    ip = SETTINGS.get('printer_ip', '192.168.1.15')
    port = SETTINGS.get('printer_port', 9100)
    protocol = SETTINGS.get('printer_protocol', 'RAW')
    fmt = SETTINGS.get('printer_format', 'PDF')

    try:
        if target['primary_file'].lower().endswith('.pdf'):
            with open(os.path.join(app.config['UPLOAD_FOLDER'], target['primary_file']), 'rb') as f:
                payload_bytes = f.read()
            # Force PDF protocol
            fmt = 'PDF'
        else:
            canvas = compile_job_canvas(target)
            if not canvas: return jsonify({'success': False, 'error': 'Failed to build image.'}), 200
            
            buf = io.BytesIO()
            if fmt == 'PDF':
                canvas.save(buf, format='PDF', resolution=300)
            else:
                canvas.save(buf, format='JPEG', quality=95)
            payload_bytes = buf.getvalue()

        # Transmit
        for _ in range(int(target.get('copies', 1))):
            success, msg = transmit_to_printer(payload_bytes, ip, port, protocol, fmt)
            if not success:
                target['print_status'] = 'Failed'
                return jsonify({'success': False, 'error': msg}), 200

        secure_shred(target)
        target['print_status'] = 'Securely Erased'
        return jsonify({'success': True, 'message': 'Direct Print Dispatched'})

    except Exception as e:
        target['print_status'] = 'Failed'
        return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/jobs/secure_delete/<int:job_id>', methods=['POST'])
def manual_secure_delete(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if target:
        secure_shred(target)
        target['print_status'] = 'Securely Erased'
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
