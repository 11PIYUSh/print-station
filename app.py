import os
import re
import json
import socket
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for, render_template_string, Response
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps, ImageDraw

app = Flask(__name__)
# SECURITY FIX: Uses system environment variables on Render, falls back to secure key locally
app.secret_key = os.environ.get('SECRET_KEY', 'piush_xerox_secure_super_key_2026_x!z') 
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
    'portal_url': '',
    'logo_url': '/static/logo.png',
    'admin_password': 'admin',
    'rates': {'bw_single': 2.0, 'bw_double': 5.0, 'color_single': 5.0, 'color_double': 0.50},
    'paper_rates': {'A4': 0.0, 'Letter': 0.0, 'Legal': 1.0, 'A5': 0.0, 'B5': 0.0, '4x6': 10.0, '5x7': 15.0, 'Card': 5.0},
    'layout_rates': {'1_photo': 0.0, '1_full': 0.0, '2_tb': 2.0, '2_lr': 2.0, '4_grid': 4.0, 'passport': 10.0, 'custom': 5.0},
    'media_rates': {'Plain Paper': 0.0, 'Photo Paper Plus Glossy II': 10.0, 'Matte Photo Paper': 10.0}
}

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login — Piush Xerox</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>body { font-family: 'Inter', sans-serif; }</style>
</head>
<body class="bg-[#09090B] text-zinc-100 min-h-screen flex items-center justify-center p-4">
    <div class="w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-3xl p-8 shadow-2xl">
        <div class="text-center mb-6">
            <h1 class="text-xl font-extrabold text-white">Admin Station Secure</h1>
            <p class="text-xs text-zinc-400 mt-1">Enter password to access control panel</p>
        </div>
        {% if error %}<div class="bg-rose-950/50 border border-rose-900 text-rose-400 text-xs text-center p-2 rounded-lg mb-4 font-bold">{{ error }}</div>{% endif %}
        <form method="POST" action="/login" class="space-y-4">
            <div><input type="password" name="password" required placeholder="Enter Password" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl p-3 text-center text-white font-bold tracking-widest outline-none focus:border-blue-500 transition"></div>
            <button type="submit" class="w-full py-3 bg-[#2563EB] hover:bg-blue-600 text-white font-bold rounded-xl shadow-lg transition">Unlock System</button>
        </form>
    </div>
</body>
</html>
"""

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
    except Exception: return DEFAULT_CONFIG

def save_settings(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

SETTINGS = load_settings()
PRINT_JOBS = []
job_counter = 1

def check_printer_socket(ip, port=631, timeout=1.0):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        res = sock.connect_ex((ip, int(port)))
        sock.close()
        return res == 0
    except Exception: return False

def process_cell_image(img, cell_w, cell_h, fit_mode, zoom):
    if fit_mode == 'cover': img = ImageOps.fit(img, (cell_w, cell_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    elif fit_mode == 'fill': img = img.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
    else: img = ImageOps.pad(img, (cell_w, cell_h), color=(255, 255, 255))
    if zoom != 1.0:
        zw, zh = int(cell_w * zoom), int(cell_h * zoom)
        img = img.resize((zw, zh), Image.Resampling.LANCZOS)
        left, top = (zw - cell_w) // 2, (zh - cell_h) // 2
        img = img.crop((left, top, left + cell_w, top + cell_h))
    return img

def build_print_sheet(job):
    if job.get('job_mode') == 'document': return 'multiple_docs', 'document'
    filenames = job.get('filenames', [])
    if not filenames: return None, None

    canvas_w, canvas_h = (2480, 3508)
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

    fit_mode, zoom, rotation = job.get('fit_mode', 'contain'), float(job.get('zoom', 1.0)), int(job.get('rotation', 0))
    raw_gap = int(job.get('grid_gap', 40))
    scaled_gap = 0 if job.get('border', 'Bordered') == 'Borderless' else int((raw_gap / 10.0) * (canvas_w / 260.0))

    margin = scaled_gap
    cell_w = (canvas_w - (margin * 2) - (cols - 1) * scaled_gap) // cols
    cell_h = (canvas_h - (margin * 2) - (rows - 1) * scaled_gap) // rows

    images = []
    for fn in filenames:
        try:
            img = ImageOps.exif_transpose(Image.open(os.path.join(app.config['UPLOAD_FOLDER'], fn))).convert('RGB')
            if rotation != 0: img = img.rotate(-rotation, expand=True, fillcolor=(255, 255, 255))
            images.append(img)
        except Exception: pass

    if not images: return None, None
    repeat_images = True if layout == 'passport' else False

    img_idx = 0
    for r in range(rows):
        for c in range(cols):
            if img_idx >= len(images):
                if repeat_images and len(images) > 0: src_img = images[img_idx % len(images)]
                else:
                    img_idx += 1
                    continue
            else: src_img = images[img_idx]
            img_idx += 1
            canvas.paste(process_cell_image(src_img, cell_w, cell_h, fit_mode, zoom), (margin + c * (cell_w + scaled_gap), margin + r * (cell_h + scaled_gap)))

    out_name = f"compiled_job_{job['id']}.pdf"
    canvas.save(os.path.join(app.config['STATIC_FOLDER'], out_name), "PDF", resolution=300)
    return out_name, 'pdf'

def secure_shred(job):
    for fn in job.get('filenames', []):
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        except: pass
    try: os.remove(os.path.join(app.config['STATIC_FOLDER'], f"compiled_job_{job.get('id', 0)}.pdf"))
    except: pass

# ================= PWA ROUTES =================
@app.route('/manifest.json')
def serve_manifest():
    shop_name = SETTINGS.get('shop_name', 'Piush Xerox')
    logo = SETTINGS.get('logo_url', '/static/logo.png')
    manifest = {
        "name": shop_name,
        "short_name": "Print Portal",
        "description": "Upload and print documents instantly",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#09090B",
        "theme_color": "#2563EB",
        "icons": [{"src": logo, "sizes": "512x512", "type": "image/png"}]
    }
    return jsonify(manifest)

@app.route('/sw.js')
def serve_sw():
    js = """
    self.addEventListener('install', (e) => { self.skipWaiting(); });
    self.addEventListener('activate', (e) => { e.waitUntil(clients.claim()); });
    self.addEventListener('fetch', (e) => { });
    """
    return Response(js, mimetype='application/javascript')

# ================= APP ROUTES & AUTH =================
@app.route('/')
def customer_portal(): return render_template('index.html')

@app.route('/admin')
def admin_portal(): 
    if not session.get('admin_logged_in'): return redirect(url_for('login_page'))
    return render_template('admin.html')

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == SETTINGS.get('admin_password', 'admin'):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_portal'))
        error = "Invalid Password"
    return render_template_string(LOGIN_TEMPLATE, error=error)

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('login_page'))

@app.route('/uploads/<path:filename>')
def serve_upload(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/config', methods=['GET'])
def get_config(): return jsonify(SETTINGS)

@app.route('/api/printer/status', methods=['GET'])
def printer_status():
    if not session.get('admin_logged_in'): return jsonify({'status': 'Offline'}), 401
    ip, port = SETTINGS.get('printer_ip', '192.168.1.15'), SETTINGS.get('printer_port', 631)
    return jsonify({'ip': ip, 'port': port, 'status': 'Online' if check_printer_socket(ip, port) else 'Offline'})

@app.route('/api/admin/config/update', methods=['POST'])
def update_admin_config():
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    for key in ['shop_name', 'tagline', 'upi_id', 'payee_name', 'printer_ip', 'portal_url', 'admin_password']:
        if key in data and str(data[key]).strip(): SETTINGS[key] = str(data[key]).strip()
    if 'printer_port' in data: SETTINGS['printer_port'] = int(data['printer_port'])
    for cat in ['rates', 'paper_rates', 'layout_rates', 'media_rates']:
        if cat in data: SETTINGS[cat].update({k: float(v) for k, v in data[cat].items()})
    save_settings(SETTINGS)
    return jsonify({'success': True, 'settings': SETTINGS})

@app.route('/api/admin/logo/upload', methods=['POST'])
def upload_logo():
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    if 'logo' not in request.files: return jsonify({'error': 'No file uploaded'}), 400
    logo_path = os.path.join(app.config['STATIC_FOLDER'], 'logo.png')
    request.files['logo'].save(logo_path)
    SETTINGS['logo_url'] = f"/static/logo.png?t={int(datetime.now().timestamp())}"
    save_settings(SETTINGS)
    return jsonify({'success': True, 'logo_url': SETTINGS['logo_url']})

@app.route('/upload', methods=['POST'])
def handle_upload():
    global job_counter
    uploaded_files = request.files.getlist('files')
    if not uploaded_files or uploaded_files[0].filename == '': return jsonify({'error': 'No file'}), 400

    saved_filenames = []
    for file in uploaded_files:
        filename = f"{job_counter}_{secure_filename(file.filename)}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        saved_filenames.append(filename)

    layout = request.form.get('layout', '1_photo')
    job = {
        'id': job_counter, 'filenames': saved_filenames, 'primary_file': saved_filenames[0],
        'job_mode': request.form.get('job_mode', 'image'), 'copies': int(request.form.get('copies', 1)),
        'color_mode': request.form.get('color_mode', 'bw'), 'print_side': request.form.get('print_side', 'single'),
        'layout': f"Custom ({request.form.get('custom_rows', '3')}x{request.form.get('custom_cols', '3')})" if layout == 'custom' else layout,
        'paper_size': request.form.get('paper_size', 'A4'), 'media_type': request.form.get('media_type', 'Plain Paper'),
        'border': request.form.get('border', 'Bordered'), 'fit_mode': request.form.get('fit_mode', 'contain'),
        'zoom': float(request.form.get('zoom', 1.0)), 'rotation': int(request.form.get('rotation', 0)),
        'grid_gap': int(request.form.get('grid_gap', 40)), 'total_price': float(request.form.get('total_price', 0.0)),
        'time': datetime.now().strftime('%d %b, %I:%M %p'), 'payment_status': 'Pending Verification', 'print_status': 'Waiting'
    }
    PRINT_JOBS.append(job)
    job_counter += 1
    return jsonify({'success': True, 'job': job})

@app.route('/api/jobs', methods=['GET'])
def list_jobs(): 
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(PRINT_JOBS)

@app.route('/api/jobs/verify_and_print/<int:job_id>', methods=['POST'])
def verify_and_print(job_id):
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if not target: return jsonify({'error': 'Job not found'}), 404
    target['payment_status'] = 'Paid'
    try:
        filename, ftype = build_print_sheet(target)
        if not filename: return jsonify({'success': False, 'error': 'Failed processing'}), 200
        target['print_status'] = 'Completed'
        return jsonify({'success': True, 'url': f"/print_ready/{filename}/{ftype}/{target['id']}"})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/admin/test/blank', methods=['POST'])
def test_print_blank():
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    try:
        filename = f"test_blank_{int(datetime.now().timestamp())}.pdf"
        Image.new('RGB', (2480, 3508), color=(255, 255, 255)).save(os.path.join(app.config['STATIC_FOLDER'], filename), "PDF", resolution=300)
        return jsonify({'success': True, 'url': f'/print_ready/{filename}/pdf/0'})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/admin/test/image', methods=['POST'])
def test_print_image():
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    try:
        test_im = Image.new('RGB', (2480, 3508), color=(255, 255, 255))
        draw = ImageDraw.Draw(test_im)
        draw.rectangle([100, 100, 2380, 3408], outline=(0, 0, 0), width=6)
        start_y = 600
        for col in [(0, 255, 255), (255, 0, 255), (255, 255, 0), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]:
            draw.rectangle([300, start_y, 2180, start_y + 150], fill=col, outline=(0, 0, 0), width=2)
            start_y += 220
        filename = f"test_color_{int(datetime.now().timestamp())}.pdf"
        test_im.save(os.path.join(app.config['STATIC_FOLDER'], filename), "PDF", resolution=300)
        return jsonify({'success': True, 'url': f'/print_ready/{filename}/pdf/0'})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/jobs/secure_delete/<int:job_id>', methods=['POST'])
def manual_secure_delete(job_id):
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), None)
    if target:
        secure_shred(target)
        target['print_status'] = 'Securely Erased'
    return jsonify({'success': True})

# ================= SAMSUNG-PROOF PRINT BRIDGE =================
@app.route('/print_ready/<filename>/<filetype>/<int:job_id>')
def print_ready(filename, filetype, job_id):
    if not session.get('admin_logged_in'): return redirect(url_for('login_page'))
    target = next((j for j in PRINT_JOBS if j['id'] == job_id), {})
    print_side = target.get('print_side', 'single')
    copies = target.get('copies', 1)
    
    file_path = f"/static/{filename}" if job_id == 0 or target.get('job_mode', 'image') == 'image' else f"/uploads/{filename}"

    if filetype in ['pdf', 'document']:
        filenames = target.get('filenames', []) if filetype == 'document' else [filename]
        
        # FIX FOR SAMSUNG: Manual button to open PDF natively instead of Auto-Redirect
        if len(filenames) == 1:
            fn = filenames[0]
            serve_path = f"/static/{fn}" if filetype == 'pdf' else f"/uploads/{fn}"
            return f"""
            <!DOCTYPE html><html><head><title>Print HUB</title><meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{ font-family: sans-serif; text-align: center; background: #09090b; color: white; padding: 20px; }}
                .btn {{ display: inline-block; padding: 15px 30px; margin: 10px; font-size: 16px; font-weight: bold; color: white; background: #2563eb; border: none; border-radius: 10px; cursor: pointer; text-decoration: none; }}
                .btn-red {{ background: #dc2626; margin-top: 20px; }}
                .alert {{ background: #3f3f46; border: 2px solid #fbbf24; color: #fbbf24; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 16px; font-weight: bold; }}
            </style></head>
            <body>
                <h2 style="color: #60a5fa;">Ready to Print</h2>
                <div class="alert">
                    ⚠️ SET COPIES TO: {copies}<br>
                    { "⚠️ MANUAL DUPLEX: Print Odd Pages -> Reinsert -> Print Even Pages" if print_side == 'double' else "" }
                </div>
                <p style="color: #a1a1aa; margin-bottom: 30px;">Tap the button below to open the PDF in your native viewer, then tap Print.</p>
                
                <a href="{serve_path}" target="_blank" class="btn">📄 Open Document to Print</a>
                <hr style="border-color:#333; margin: 30px 0;">
                <button class="btn btn-red" onclick="fetch('/api/jobs/secure_delete/{job_id}', {{method: 'POST'}}); window.close();">🗑️ Shred Data & Close Hub</button>
            </body></html>
            """
        
        # Multiple uploaded PDF documents
        links = ""
        for i, fn in enumerate(filenames):
            links += f'<a href="/uploads/{fn}" target="_blank" class="btn">📄 Open & Print File {i+1}</a><br>'
            
        return f"""
        <!DOCTYPE html><html><head><title>Multiple Documents - Order #{job_id}</title><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: sans-serif; text-align: center; background: #09090b; color: white; padding: 20px; }}
            .btn {{ display: inline-block; padding: 15px 30px; margin: 10px; font-size: 16px; font-weight: bold; color: white; background: #2563eb; border: none; border-radius: 10px; cursor: pointer; text-decoration: none; }}
            .btn-red {{ background: #dc2626; margin-top: 20px; }}
            .alert {{ background: #3f3f46; border: 2px solid #fbbf24; color: #fbbf24; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 16px; font-weight: bold; }}
        </style></head>
        <body>
            <h2 style="color: #60a5fa;">Multiple Documents Detected</h2>
            <div class="alert">
                ⚠️ SET COPIES TO: {copies}<br>
                { "⚠️ MANUAL DUPLEX: Print Odd Pages -> Reinsert -> Print Even Pages" if print_side == 'double' else "" }
            </div>
            <p style="color: #a1a1aa; margin-bottom: 30px;">Please open and print each file below.</p>
            {links}
            <hr style="border-color:#333; margin: 30px 0;">
            <button class="btn btn-red" onclick="fetch('/api/jobs/secure_delete/{job_id}', {{method: 'POST'}}); window.close();">🗑️ Shred Data & Close Hub</button>
        </body></html>
        """
        
    return "Error generating print file."

if __name__ == '__main__':
    # Starts using Waitress/Gunicorn safely on Render, or standard Werkzeug locally
    app.run(host='0.0.0.0', port=5000)
