import os
import requests
import zipfile
import json
import time
import platform 

LOCAL_VERSION_FILE = "version.txt"
CONFIG_FILE = "lift_config.json"

def get_local_version():
    if os.path.exists(LOCAL_VERSION_FILE):
        with open(LOCAL_VERSION_FILE, "r") as f:
            return f.read().strip()
    return "1.0.0"

def get_server_urls():
    central_ip = "127.0.0.1" 
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                central_ip = config.get("central_server_ip", "127.0.0.1")
        except Exception as e:
            print(f"Error reading config: {e}")
            
    version_url = f"http://{central_ip}/lift_version.txt"
    patch_url = f"http://{central_ip}/patch.zip"
    
    return version_url, patch_url

def check_and_apply_update():
    current_version = get_local_version()
    VERSION_URL, PATCH_URL = get_server_urls()
    
    print(f"[*] Local version is v{current_version}")
    print(f"[*] Checking updates from: {VERSION_URL}")
    
    try:
        response = requests.get(VERSION_URL, timeout=4)
        latest_version = response.text.strip()
        
        if latest_version != current_version:
            print(f"[+] New version detected: v{latest_version}. Downloading patch...")
            
            # --- [ปรับปรุงจุดนี้: ป้องกันไฟล์โดนล็อคแบบรองรับสองแพลตฟอร์ม] ---
            print("[*] Closing current Lift Server if it's running...")
            if platform.system() == "Windows":
                # สำหรับ Windows: สั่งปิดไฟล์ .exe ตัวหลัก
                os.system("taskkill /f /im lift_single_ui.exe >nul 2>&1")
            else:
                # สำหรับ Ubuntu/Linux: สั่งสั่งปิดโปรเซสสคริปต์หลัก Python ทันที
                os.system("pkill -f lift_single_ui.py >/dev/null 2>&1")
                
            time.sleep(1) # หน่วงเวลา 1 วินาทีให้ระบบคืนค่าไฟล์
            # ------------------------------------------------------------------
            
            patch_resp = requests.get(PATCH_URL, timeout=15)
            zip_name = "patch_temp.zip"
            with open(zip_name, "wb") as f:
                f.write(patch_resp.content)
            
            print("[*] Extracting files...")
            with zipfile.ZipFile(zip_name, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.filename in ["lift_config.json", "config.json"]:
                        print(f"[!] Skipped: {file_info.filename} to protect local settings.")
                        continue
                    zip_ref.extract(file_info, ".")
            
            with open(LOCAL_VERSION_FILE, "w") as f:
                f.write(latest_version)
                
            os.remove(zip_name)
            print(f"\n[🎉] Successfully updated to v{latest_version}!")
            
            if platform.system() == "Windows":
                print("[*] You can now open 'lift_single_ui.exe' to run the new version.")
            else:
                print("[*] You can now run 'python3 lift_single_ui.py' to start the new version.")
        else:
            print("\n[*] Your system is up-to-date.")
            
    except Exception as e:
        print(f"\n[⚠️] Update check failed or server offline: {e}")

if __name__ == "__main__":
    print("=== Lift System Updater ===")
    check_and_apply_update()
    print("\n===========================")
    
    # --- [ปรับปรุงจุดนี้: ใช้ฟังก์ชันภายในของ Python เพื่อค้างหน้าจอข้ามแพลตฟอร์ม] ---
    input("\nPress Enter to exit...")