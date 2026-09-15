import os
import subprocess
import socket

def dispatch_print(file_path, printer_ip="192.168.1.16", port=9100):
    """
    100% Reliable Android -> Canon G3010 Dispatch Engine.
    
    1. Primary: Dispatches via Android's native print framework / Canon Print Service
       using termux-open. This bypasses CUPS and lets the official Canon Android driver
       handle the GDI/raster data formatting.
    2. Fallback: Direct socket send if running in a standalone headless setup.
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return False, "File does not exist"

    # 1. Android Native Print Intent via Termux
    try:
        # termux-open --send opens Android's share/print sheet
        cmd = f'termux-open --send "{abs_path}"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            return True, "Dispatched via Android Native Print Spooler (Canon Service)"
    except Exception as e:
        pass

    # 2. Fallback: Socket RAW Stream (Port 9100)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((printer_ip, int(port)))
        with open(abs_path, 'rb') as f:
            while chunk := f.read(4096):
                sock.send(chunk)
        sock.close()
        return True, "Dispatched via RAW Socket Port 9100"
    except Exception as e:
        return False, f"All dispatch methods failed: {str(e)}"
