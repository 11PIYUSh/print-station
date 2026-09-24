import time
import requests
import subprocess
import os

# Change this to your exact Render URL
RENDER_URL = "https://your-app-name.onrender.com"

def poll_render():
    print(f"Polling {RENDER_URL} for approved prints...")
    while True:
        try:
            # 1. Check for paid jobs
            res = requests.get(f"{RENDER_URL}/api/worker/pending")
            if res.status_code == 200:
                jobs = res.json()
                
                for job in jobs:
                    print(f"Incoming Job: {job['original_name']} ({job['copies']} copies)")
                    
                    file_url = f"{RENDER_URL}/uploads/{job['filename']}"
                    local_path = f"temp_{job['filename']}"
                    
                    # 2. Download the file from Render
                    img_data = requests.get(file_url).content
                    with open(local_path, 'wb') as f:
                        f.write(img_data)
                    
                    # 3. Dispatch to CUPS on the local Canon G3010
                    print("Dispatching to Canon G3010...")
                    cmd = ['lp', '-d', 'Canon_G3010', '-n', str(job['copies'])]
                    
                    if job.get('color_mode') == 'Monochrome':
                        cmd.extend(['-o', 'print-color-mode=monochrome'])
                    else:
                        cmd.extend(['-o', 'print-color-mode=color'])
                        
                    cmd.extend(['-o', f"media={job.get('paper_size', 'A4')}"])
                    cmd.append(local_path)
                    
                    subprocess.run(cmd)
                    
                    # 4. Tell Render the job is done
                    requests.post(f"{RENDER_URL}/api/worker/complete/{job['id']}")
                    
                    # Clean up the Termux storage
                    os.remove(local_path)
                    print("Job complete.")
                    
        except Exception as e:
            pass # Ignore temporary network drops
            
        # Ping every 5 seconds
        time.sleep(5)

if __name__ == "__main__":
    poll_render()
