import os
import subprocess
from PIL import Image, ImageOps

def format_image(image_path, paper_size="A4", color_mode="Color"):
    """Formats the image to the exact page size and saves a temporary file for the print spooler."""
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
        
        # Save a temporary file for the Linux print driver to pick up
        temp_path = os.path.join(os.getcwd(), "temp_print_job.jpg")
        canvas.save(temp_path, format='JPEG', quality=95)
        return temp_path

def send_to_canon(payload_path, printer_ip, port=None):
    """
    Hands the formatted image to the CUPS Linux Print Server.
    CUPS handles the complex JPEG-to-Raster Canon translation automatically.
    """
    try:
        # We tell the 'lp' command line tool to print to the printer we named 'CanonG3010'
        # -o media=A4 sets the paper size, -o fit-to-page ensures it doesn't bleed off the edge
        result = subprocess.run(
            ["lp", "-d", "CanonG3010", "-o", "media=A4", "-o", "fit-to-page", payload_path],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return True, "Job sent to print spooler. Green light should blink!"
        else:
            return False, f"Print Spooler Error: {result.stderr}"
            
    except Exception as e:
        return False, f"System Error: Is 'cupsd' running? ({str(e)})"
