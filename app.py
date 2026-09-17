import os
import re
import json
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
    'upi_id': 'piyush@upi',
    'payee_name': 'PIYUSH',
    'printer_ip': '192.168.1.15',
    'printer_port': 631,
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

# ================= HIGH-END RENDER ENGINE =================
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

def build_print_sheet(job):
    filenames = job.get('filenames', [])
    if not filenames: return None, None

    if job['primary_file'].lower().endswith('.pdf'):
        return job['primary_file'], 'pdf'

    dimensions = {
        'A4': (2480, 3508), 'Letter': (2550, 3300), 'Legal': (2550, 4200),
        '4x6': (1200, 1800), '5x7': (1500, 2100), 'Card': (651, 1074),
        'A5': (1748, 2480), 'B5': (2079, 2953)
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
    
    if job.get('border', 'Bordered') == 'Borderless':
        margin, cell_gap = 0, 0
    else:
        margin, cell_gap = grid_gap, grid_gap

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
            if job.get('color_mode') == 'bw': img = img.convert('L').convert('RGB')
            else: img = img.convert('RGB')
            if rotation != 0: img = img.rotate(-rotation, expand=True, fillcolor=(255, 255, 255))
            images.append(img)
        except Exception: pass

    if not images: return None, None

    # ONLY repeat images if layout is passport
    repeat_images = True if layout == 'passport' else False

    img_idx = 0
    for r in range(rows):
        for c in range(cols):
            # Stop filling slots if we run out of uploaded images and it's NOT a passport
            if img_idx >= len(images):
                if repeat_images and len(images) > 0:
                    src_img = images[img_idx % len(images)]
                else:
                    img_idx += 1
                    continue # Leave this cell completely blank
            else:
                src_img = images[img_idx]
                
            img_idx += 1
            
            cell_img = process_cell_image(src_img, cell_w, cell_h, fit_mode, zoom)
            x = margin + c * (cell_w + cell_gap)
            y = margin + r * (cell_h + cell_gap)
            canvas.paste(cell_img, (x, y))

    out_name = f"compiled_job_{job['id']}.jpg"
    out_path = os.path.join(app.config['STATIC_FOLDER'], out_name)
    canvas.save(out_path, format='JPEG', quality=95, optimize=True)
    return out_name, 'image'

def secure_shred(job):
    for fn in job.get('filenames', []):
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        except: pass
    compiled_name = f"compiled_job_{job.get('id', 0)}.jpg"
    try: os.remove(os.path.join(app.config['STATIC_FOLDER'], compiled_name))
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
    for key in ['shop_name', 'tagline', 'upi_id', 'payee_name', 'printer_ip']:
        if key in data and str(data[key]).strip(): SETTINGS[key] = str(data[key]).strip()
    
    if 'printer_port' in data: SETTINGS['printer_port'] = int(data['printer_port'])
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

# ================= HARDWARE TEST ROUTES =================
@app.route('/api/admin/test/blank', methods=['POST'])
def test_print_blank():
    try:
        blank_im = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
        filename = f"test_blank_{int(datetime.now().timestamp())}.jpg"
        filepath = os.path.join(app.config['STATIC_FOLDER'], filename)
        blank_im.save(filepath, format='JPEG', quality=85)
        return jsonify({'success': True, 'url': f'/print_ready/{filename}/image/0'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/test/image', methods=['POST'])
def test_print_image():
    try:
        test_im = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
        draw = ImageDraw.Draw(test_im)
        draw.rectangle([100, 100, 2380, 3408], outline=(0, 0, 0), width=6)
        colors = [(0, 255, 255), (255, 0, 255), (255, 255, 0), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
        start_y = 600
        for col in colors:
            draw.rectangle([300, start_y, 2180, start_y + 150], fill=col, outline=(0, 0, 0), width=2)
            start_y += 220

        filename = f"test_color_{int(datetime.now().timestamp())}.jpg"
        filepath = os.path.join(app.config['STATIC_FOLDER'], filename)
        test_im.save(filepath, format='JPEG', quality=90)
        return jsonify({'success': True, 'url': f'/print_ready/{filename}/image/0'})
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

    try:
        filename, ftype = build_print_sheet(target)
        if not filename: return jsonify({'success': False, 'error': 'Failed to process files.'}), 200
            
        target['print_status'] = 'Completed'
        return jsonify({
            'success': True, 
            'url': f"/print_ready/{filename}/{ftype}/{target['id']}",
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/jobs/secure_delete/<int:job_id>', methods=['POST'])
def manual_secure_delete(job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if target:
        secure_shred(target)
        target['print_status'] = 'Securely Erased'
    return jsonify({'success': True})

# ================= THE WORKING PRINT BRIDGE TAB =================
@app.route('/print_ready/<filename>/<filetype>/<int:job_id>')
def print_ready(filename, filetype, job_id):
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), {})
    print_side = target.get('print_side', 'single')

    if filetype == 'pdf':
        return f"""
        <script>
            if ("{print_side}" === "double") {{
                alert("MANUAL DUPLEX PRINTING:\\n1. Choose 'Print Odd Pages' in the dialog.\\n2. Turn the pages and place them back in the tray.\\n3. Choose 'Print Even Pages'.");
            }}
            window.location.href="/uploads/{filename}";
        </script>
        """

    if print_side == 'double':
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Duplex Print - Order #{job_id}</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{ font-family: sans-serif; text-align: center; background: #09090b; color: white; padding: 20px; }}
                .btn {{ display: inline-block; padding: 15px 30px; margin: 10px; font-size: 18px; font-weight: bold; color: white; background: #2563eb; border: none; border-radius: 10px; cursor: pointer; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
                .btn-green {{ background: #16a34a; }}
                .btn-red {{ background: #dc2626; }}
                .img-preview {{ max-width: 90%; max-height: 45vh; border: 2px solid #333; margin: 20px auto; display: block; border-radius: 8px; }}
                @media print {{
                    body * {{ visibility: hidden; }}
                    .img-preview {{ visibility: visible; position: absolute; left: 0; top: 0; width: 100vw; height: 99vh; object-fit: contain; margin: 0; border: none; }}
                    @page {{ margin: 0; size: auto; }}
                }}
            </style>
        </head>
        <body>
            <h2 style="margin-bottom: 5px;">Manual Duplex Mode</h2>
            <img src="/static/{filename}" class="img-preview">
            
            <div id="step1">
                <p style="color:#60a5fa; font-size: 18px; margin-top: 0;">Step 1: Print the Front Side</p>
                <button class="btn" onclick="window.print(); document.getElementById('step1').style.display='none'; document.getElementById('step2').style.display='block';">🖨️ Print Front Side</button>
            </div>
            
            <div id="step2" style="display:none;">
                <p style="color:#fbbf24; font-size: 22px; font-weight: bold; margin-bottom: 5px;">⚠️ TURN THE PAGE NOW!</p>
                <p style="margin-top: 0; font-size: 16px; color: #a1a1aa;">Take the printed sheet out, flip it over, and re-insert it into the printer.</p>
                <button class="btn btn-green" onclick="window.print(); document.getElementById('step2').style.display='none'; document.getElementById('step3').style.display='block';">🖨️ Print Back Side</button>
            </div>

            <div id="step3" style="display:none;">
                <p style="color:#4ade80; font-size: 18px; font-weight: bold;">✅ Printing Complete</p>
                <button class="btn btn-red" onclick="shredAndClose()">🗑️ Shred Data & Close Tab</button>
            </div>

            <script>
                async function shredAndClose() {{
                    if({job_id} !== 0) {{
                        try {{ await fetch(`/api/jobs/secure_delete/{job_id}`, {{method: 'POST'}}); }} catch(e) {{}}
                    }}
                    window.close();
                }}
            </script>
        </body>
        </html>
        """
        
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Secure Print - Order #{job_id}</title>
        <style>
            @page {{ margin: 0; size: auto; }}
            html, body {{ margin: 0; padding: 0; width: 100vw; height: 99vh; background: #fff; overflow: hidden; }}
            img {{ width: 100%; height: 100%; object-fit: contain; display: block; page-break-inside: avoid; }}
        </style>
    </head>
    <body>
        <img src="/static/{filename}">
        <script>
            window.onload = () => {{
                setTimeout(() => {{ window.print(); }}, 500);
            }};
            window.addEventListener('afterprint', () => {{
                if({job_id} !== 0) fetch(`/api/jobs/secure_delete/{job_id}`, {{method: 'POST'}});
            }});
        </script>
    </body>
    </html>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
