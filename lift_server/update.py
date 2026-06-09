import os
import zipfile
import time
import platform
import sys
import shutil
import subprocess

LOCAL_VERSION_FILE = "version.txt"
PATCH_FILE = "patch.zip"

def get_local_version():
    if os.path.exists(LOCAL_VERSION_FILE):
        with open(LOCAL_VERSION_FILE, "r") as f:
            return f.read().strip()
    return "1.0.0"

def restart_server():
    print("[*] กำลังรีสตาร์ท Lift Server...")
    try:
        if platform.system() == "Windows":
            if os.path.exists("lift_single_ui.exe"):
                subprocess.Popen(["lift_single_ui.exe"], creationflags=0x00000008) # DETACHED_PROCESS
            else:
                # Use sys.executable for python if running as script
                subprocess.Popen([sys.executable, "lift_single_ui.py"], creationflags=0x00000008)
        else:
            # สำหรับ Linux (Ubuntu) สั่งรันใน Session ใหม่
            subprocess.Popen(["python3", "lift_single_ui.py"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[✅] รีสตาร์ทสำเร็จ!")
    except Exception as e:
        print(f"[⚠️] รีสตาร์ทล้มเหลว: {e}")

def apply_patch():
    if not os.path.exists(PATCH_FILE):
        print(f"[!] ไม่พบไฟล์อัปเดต '{PATCH_FILE}'")
        return False

    print(f"[*] พบไฟล์อัปเดต {PATCH_FILE}. กำลังเริ่มการติดตั้งแบบฉลาด...")
    
    # --- 1. ปิดโปรแกรมหลัก ---
    print("[*] กำลังปิด Lift Server...")
    if platform.system() == "Windows":
        os.system("taskkill /f /im lift_single_ui.exe >nul 2>&1")
    else:
        os.system("pkill -f lift_single_ui.py >/dev/null 2>&1")
    time.sleep(2)

    # --- 2. แตกไฟล์แบบลบชื่อโฟลเดอร์ชั้นแรก (Flatten) ---
    try:
        with zipfile.ZipFile(PATCH_FILE, 'r') as zip_ref:
            new_version = "Unknown"
            
            for file_info in zip_ref.infolist():
                # ข้ามโฟลเดอร์เปล่า
                if file_info.is_dir(): continue
                
                # ตัดชื่อโฟลเดอร์ชั้นแรกออก (ถ้ามี) เช่น "patch/version.txt" -> "version.txt"
                filename = os.path.basename(file_info.filename)
                
                # เช็คไฟล์เวอร์ชันเพื่ออัปเดตสถานะ
                if filename == "version.txt":
                    with zip_ref.open(file_info) as f:
                        new_version = f.read().decode('utf-8').strip()

                # ป้องกันไฟล์สำคัญ
                if filename in ["lift_config.json", "config.json"]:
                    print(f"[!] ข้าม: {filename}")
                    continue

                # แตกไฟล์ออกมาที่โฟลเดอร์หลักโดยตรง
                source = zip_ref.open(file_info)
                target_path = os.path.join(".", filename)
                with source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)
                print(f"[+] ติดตั้ง: {filename}")
        
        if new_version != "Unknown":
            with open(LOCAL_VERSION_FILE, "w") as f:
                f.write(new_version)
            print(f"[🎉] อัปเดตเป็น v{new_version} สำเร็จ!")
        
        os.remove(PATCH_FILE)
        
    except Exception as e:
        print(f"\n[⚠️] ข้อผิดพลาด: {e}")
        return False
    return True

if __name__ == "__main__":
    print("=== Smart Manual Updater ===")
    current = get_local_version()
    print(f"[*] ปัจจุบัน: v{current}")
    
    if apply_patch():
        print("\n[OK] อัปเดตเสร็จสิ้น")
        restart_server()
        print("===============================")
        
        # ตรวจสอบว่ารันแบบมีหน้าจอ (Interactive) หรือไม่
        if sys.stdin and sys.stdin.isatty():
            try:
                print("\n[ℹ️] ระบบจะปิดอัตโนมัติใน 5 วินาที...")
                import select
                select.select([sys.stdin], [], [], 5)
            except:
                pass
        else:
            print("[*] อัปเดตเสร็จสิ้น (Non-interactive mode)")
            time.sleep(2)
    else:
        time.sleep(2)

    print("[*] ปิดโปรแกรมอัปเดต")
