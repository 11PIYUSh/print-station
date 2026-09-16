import socket
import io
from PIL import Image, ImageOps

def format_image(image_path, paper_size="A4", color_mode="Color"):
    """Prepares the image to the exact dimensions of the paper."""
    dimensions = {
        'A4': (2480, 3508), 'Letter': (2550, 3300), 'Legal': (2550, 4200),
        '4x6': (1200, 1800), '5x7': (1500, 2100), 'Card': (651, 1074)
    }
    target_w, target_h = dimensions.get(paper_size, (2480, 3508))

    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        
        canvas = Image.new('RGB', (target_w, target_h), color=(255, 255, 255))
        
        if color_mode == 'Monochrome':
            im = im.convert('L').convert('RGB')
        else:
            im = im.convert('RGB')
            
        canvas.paste(im, ((target_w - im.width) // 2, (target_h - im.height) // 2))
        
        buf = io.BytesIO()
        # Save as a standard JPEG byte stream
        canvas.save(buf, format='JPEG', quality=95, optimize=True)
        return buf.getvalue()

def send_to_canon(payload_bytes, printer_ip, port=9100):
    """
    Sends the raw bytes directly to Canon's RAW Port 9100.
    This avoids HTTP 404 errors because it does not use web protocols.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15.0)
        sock.connect((printer_ip, int(port)))
        
        # Send data directly to the printer's hardware buffer
        sock.sendall(payload_bytes)
        sock.close()
        
        return True, "Sent successfully to Port 9100"
    except Exception as e:
        return False, f"Connection Error: {str(e)}"
