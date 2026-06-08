import os
import requests
import zipfile

# แก้ไข URL สองบรรทัดนี้ให้ตรงกับระบบ Server ส่วนกลางของคุณตอนปล่อยงานจริง
VERSION_URL = "http://your-central-server.com/lift_version.txt"
PATCH_URL = "http://your-central-server.com/patch.zip"
LOCAL_VERSION_FILE = "version.txt"

def get_local_version():
    if os.path.exists(LOCAL_VERSION_FILE):
        with open(LOCAL_VERSION_FILE, "r") as f:
            return f.read().strip()
    return "1.0.0"  # ยึดตามเวอร์ชันตั้งต้นในโค้ดหลักของคุณ

def check_and_apply_update():
    current_version = get_local_version()
    print(f"[*] Checking updates... Local version is v{current_version}")
    
    try:
        # 1. เช็คเลขเวอร์ชันล่าสุดจากเซิร์ฟเวอร์หลัก
        response = requests.get(VERSION_URL, timeout=4)
        latest_version = response.text.strip()
        
        if latest_version != current_version:
            print(f"[+] New version detected: v{latest_version}. Downloading patch...")
            
            # 2. ดาวน์โหลดไฟล์ zip โกดังโค้ดใหม่
            patch_resp = requests.get(PATCH_URL, timeout=15)
            zip_name = "patch_temp.zip"
            with open(zip_name, "wb") as f:
                f.write(patch_resp.content)
            
            # 3. แตกไฟล์ทับเฉพาะโค้ด (ข้ามไฟล์ config เพื่อเซฟไอพีหน้างาน)
            with zipfile.ZipFile(zip_name, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    # ป้องกันไม่ให้ไฟล์คอนฟิกหน้างานโดนเขียนทับเด็ดขาด
                    if file_info.filename in ["lift_config.json", "config.json"]:
                        print(f"[!] Skipped: {file_info.filename} to protect local settings.")
                        continue
                    zip_ref.extract(file_info, ".")
            
            # 4. บันทึกประวัติเวอร์ชันล่าสุดลงเครื่องหน้างาน
            with open(LOCAL_VERSION_FILE, "w") as f:
                f.write(latest_version)
                
            os.remove(zip_name)
            print(f"[🎉] Successfully updated to v{latest_version}!")
        else:
            print("[*] Your system is up-to-date.")
            
    except Exception as e:
        # ดักจับ Error เผื่อเซิร์ฟเวอร์ปิดหรือไม่มีอินเทอร์เน็ต ระบบลิฟต์หลักจะยังคงรันต่อไปได้ปกติ
        print(f"[⚠️] Update check skipped or server offline: {e}")

if __name__ == "__main__":
    check_and_apply_update()