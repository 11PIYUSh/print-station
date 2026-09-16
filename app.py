import os
import re
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps

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

# ================= RENDER ENGINE =================
def build_print_sheet(job):
    """Compiles the uploaded images into the requested grid layout on an A4 canvas."""
    filenames = job.get('filenames', [])
    if not filenames:
        return None, None

    # If it is a PDF document, bypass image processing and serve PDF directly
    if job['primary_file'].lower().endswith('.pdf'):
        return job['primary_file'], 'pdf'

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
        if m:
            rows, cols = int(m.group(1)), int(m.group(2))

    margin = 80
    usable_w = canvas_w - (margin * 2)
    usable_h = canvas_h - (margin * 2)
    cell_w = usable_w // cols
    cell_h = usable_h // rows

    images = []
    for fn in filenames:
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], fn)
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            if job.get('color_mode') == 'bw':
                img = img.convert('L').convert('RGB')
            else:
                img = img.convert('RGB')
            images.append(img)
        except Exception:
            pass

    if not images:
        return None, None

    img_idx = 0
    for r in range(rows):
        for c in range(cols):
            src_img = images[img_idx % len(images)]
            img_idx += 1
            
            # Maintain aspect ratio within the cell
            cell_img = src_img.copy()
            cell_img.thumbnail((cell_w - 20, cell_h - 20), Image.Resampling.LANCZOS)
            
            x = margin + (c * cell_w) + (cell_w - cell_img.width) // 2
            y = margin + (r * cell_h) + (cell_h - cell_img.height) // 2
            canvas.paste(cell_img, (x, y))

    out_name = f"compiled_job_{job['id']}.jpg"
    out_path = os.path.join(app.config['STATIC_FOLDER'], out_name)
    canvas.save(out_path, format='JPEG', quality=95, optimize=True)
    
    return out_name, 'image'

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

    try:
        # Generate the compiled image for the browser to print
        filename, ftype = build_print_sheet(target)
        if not filename:
            return jsonify({'success': False, 'error': 'Failed to process image files.'}), 200
            
        target['print_status'] = 'Completed'
        return jsonify({
            'success': True, 
            'file_type': ftype,
            'url': f"/print_ready/{filename}/{ftype}",
            'copies': target.get('copies', 1)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200

# Endpoint that serves the image/PDF with a script that auto-triggers the print dialog
@app.route('/print_ready/<filename>/<filetype>')
def print_ready(filename, filetype):
    if filetype == 'pdf':
        return f'<script>window.location.href="/uploads/{filename}";</script>'
        
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Print Job</title>
        <style>
            body, html {{ margin: 0; padding: 0; background: #fff; text-align: center; }}
            img {{ width: 100vw; height: 100vh; object-fit: contain; }}
            @page {{ margin: 0; size: auto; }}
        </style>
    </head>
    <body onload="setTimeout(() => {{ window.print(); }}, 500);">
        <img src="/static/{filename}">
    </body>
    </html>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
