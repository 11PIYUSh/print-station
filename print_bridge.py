import io
import os
import struct
import urllib.request
from PIL import Image, ImageOps

def compress_pwg_scanline(raw_line):
    """
    Applies standard PWG-Raster PackBits compression per line.
    PWG line format: [1-byte line repeat count - 1] + [PackBits compressed data]
    """
    out = bytearray([0])  # Repeat count = 0 (line appears once)
    i = 0
    n = len(raw_line)
    
    while i < n:
        # Check for matching bytes
        run = 1
        while i + run < n and run < 128 and raw_line[i + run] == raw_line[i]:
            run += 1
        
        if run > 1:
            out.append(257 - run)  # Repeat run
            out.append(raw_line[i])
            i += run
        else:
            # Literal run
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

def build_canon_pwg(image_path, paper_size="A4", color_mode="Color", dpi=300):
    dimensions = {
        'A4': (2480, 3508),
        'Letter': (2550, 3300),
        'Legal': (2550, 4200),
        '4x6': (1200, 1800),
        '5x7': (1500, 2100),
        'Card': (651, 1074)
    }
    target_w, target_h = dimensions.get(paper_size, (2480, 3508))

    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        
        if color_mode == 'Monochrome':
            canvas = Image.new('L', (target_w, target_h), color=255)
            im = im.convert('L')
            canvas.paste(im, ((target_w - im.width) // 2, (target_h - im.height) // 2))
            bits_per_color = 8
            bits_per_pixel = 8
            color_space = 1  # SGray
        else:
            canvas = Image.new('RGB', (target_w, target_h), color=(255, 255, 255))
            im = im.convert('RGB')
            canvas.paste(im, ((target_w - im.width) // 2, (target_h - im.height) // 2))
            bits_per_color = 8
            bits_per_pixel = 24
            color_space = 19  # SRGB

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

    # Encode scanlines with PWG compression
    compressed_body = io.BytesIO()
    raw_pixels = canvas.tobytes()
    for y in range(target_h):
        line = raw_pixels[y * bytes_per_line : (y + 1) * bytes_per_line]
        compressed_body.write(compress_pwg_scanline(line))

    return pwg_sync + bytes(pwg_header) + compressed_body.getvalue()

def dispatch_print(file_path, printer_ip="192.168.1.16", paper_size="A4", color_mode="Color", copies=1):
    """
    Sends properly framed IPP Print-Job request to Canon G3010.
    """
    # G3010 firmware listens on /ipp/printer
    url = f"http://{printer_ip}:631/ipp/printer"
    
    try:
        pwg_payload = build_canon_pwg(file_path, paper_size, color_mode)

        # Build IPP Header (Version 2.0, Operation: Print-Job 0x0002, Request ID 1)
        ipp_data = bytearray([0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01])
        
        # Operation Attributes Tag
        ipp_data.append(0x01)

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
        add_attr(0x42, 'job-name', 'MobileStation')
        add_attr(0x49, 'document-format', 'image/pwg-raster')
        
        # End of attributes tag
        ipp_data.append(0x03)

        full_request = bytes(ipp_data) + pwg_payload

        for _ in range(int(copies)):
            req = urllib.request.Request(
                url,
                data=full_request,
                headers={'Content-Type': 'application/ipp'}
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                response_data = resp.read()
                # Status code is in bytes 2-3 of IPP response (0x0000 = successful-ok)
                if len(response_data) >= 4:
                    status_code = int.from_bytes(response_data[2:4], 'big')
                    if status_code != 0x0000:
                        return False, f"IPP Rejected by Canon (Code 0x{status_code:04X})"

        return True, "Job accepted. Green light should blink now!"
    except Exception as e:
        return False, str(e)
