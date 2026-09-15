import os
import struct
import urllib.request
from PIL import Image, ImageOps

def image_to_pwg_raster(image_path, paper_size="A4", color_mode="Color", dpi=300):
    """
    Converts any JPG/PNG image into authentic PWG-Raster (RaS2) stream
    supported by driverless Canon G-series printers.
    """
    # Standard paper dimensions at 300 DPI
    dimensions = {
        'A4': (2480, 3508),
        'Letter': (2550, 3300),
        'Legal': (2550, 4200),
        '4x6': (1200, 1800),
        '5x7': (1500, 2100),
        '8x10': (2400, 3000),
        'Square': (1500, 1500),
        'Card': (651, 1074)
    }
    
    target_w, target_h = dimensions.get(paper_size, (2480, 3508))
    
    with Image.open(image_path) as im:
        # Correct orientation from EXIF camera tags
        im = ImageOps.exif_transpose(im)
        
        # Fit image neatly on printable canvas
        im.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        
        if color_mode == 'Monochrome':
            canvas = Image.new('L', (target_w, target_h), color=255)
            im = im.convert('L')
            # Center the image
            offset_x = (target_w - im.width) // 2
            offset_y = (target_h - im.height) // 2
            canvas.paste(im, (offset_x, offset_y))
            
            bits_per_color = 8
            bits_per_pixel = 8
            color_space = 1  # 1 = SGray
            raw_pixels = canvas.tobytes()
        else:
            canvas = Image.new('RGB', (target_w, target_h), color=(255, 255, 255))
            im = im.convert('RGB')
            offset_x = (target_w - im.width) // 2
            offset_y = (target_h - im.height) // 2
            canvas.paste(im, (offset_x, offset_y))
            
            bits_per_color = 8
            bits_per_pixel = 24
            color_space = 19  # 19 = SRGB
            raw_pixels = canvas.tobytes()

    # Build 512-byte PWG Header
    pwg_sync = b'RaS2'
    pwg_header = bytearray(512)
    pwg_header[0:64] = b'Canon_G3010'.ljust(64, b'\x00')
    
    bytes_per_line = (target_w * bits_per_pixel) // 8
    
    struct.pack_into('>I', pwg_header, 280, dpi)          # HWResolutionX
    struct.pack_into('>I', pwg_header, 284, dpi)          # HWResolutionY
    struct.pack_into('>I', pwg_header, 344, target_w)      # Width in pixels
    struct.pack_into('>I', pwg_header, 348, target_h)      # Height in pixels
    struct.pack_into('>I', pwg_header, 352, bits_per_color)
    struct.pack_into('>I', pwg_header, 356, bits_per_pixel)
    struct.pack_into('>I', pwg_header, 360, bytes_per_line)
    struct.pack_into('>I', pwg_header, 364, 0)             # Chunky
    struct.pack_into('>I', pwg_header, 368, color_space)
    struct.pack_into('>I', pwg_header, 372, 1)             # 1 page

    return pwg_sync + bytes(pwg_header) + raw_pixels

def dispatch_print(file_path, printer_ip="192.168.1.16", paper_size="A4", color_mode="Color", copies=1):
    """
    Sends fully formatted PWG-Raster data directly over IPP port 631 to Canon G3010.
    """
    url = f"http://{printer_ip}:631/ipp/print"
    
    try:
        pwg_payload = image_to_pwg_raster(file_path, paper_size, color_mode)
        
        # IPP 2.0 Print-Job
        header = bytearray([0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x01])

        def put_attr(tag, name, val):
            header.append(tag)
            header.extend(len(name).to_bytes(2, 'big'))
            header.extend(name.encode())
            header.extend(len(val).to_bytes(2, 'big'))
            header.extend(val.encode() if isinstance(val, str) else val)

        put_attr(0x47, 'attributes-charset', 'utf-8')
        put_attr(0x48, 'attributes-natural-language', 'en')
        put_attr(0x45, 'printer-uri', f'ipp://{printer_ip}:631/ipp/print')
        put_attr(0x42, 'job-name', 'StationOrder')
        put_attr(0x49, 'document-format', 'image/pwg-raster')
        header.append(0x03)

        for _ in range(int(copies)):
            req = urllib.request.Request(
                url,
                data=bytes(header) + pwg_payload,
                headers={'Content-Type': 'application/ipp'}
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                if resp.status not in (200, 201):
                    return False, f"Printer returned status {resp.status}"

        return True, "Printed successfully via PWG-Raster!"
    except Exception as e:
        return False, str(e)
