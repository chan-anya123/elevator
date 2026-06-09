import os
import zipfile
import time
import platform

LOCAL_VERSION_FILE = "version.txt"
PATCH_FILE = "patch.zip"  # ไฟล์อัปเดตที่คุณก๊อปปี้มาวางเอง

def get_local_version():
    if os.path.exists(LOCAL_VERSION_FILE):
        with open(LOCAL_VERSION_FILE, "r") as f:
            return f.read().strip()
    return "1.0.0"

def apply_patch():
    if not os.path.exists(PATCH_FILE):
        print(f"[!] ไม่พบไฟล์อัปเดต '{PATCH_FILE}' ในโฟลเดอร์")
        print(f"    วิธีใช้งาน: ก๊อปปี้ไฟล์ {PATCH_FILE} มาวางในโฟลเดอร์นี้แล้วรันโปรแกรมอีกครั้ง")
        return False

    print(f"[*] พบไฟล์อัปเดต {PATCH_FILE}. กำลังเริ่มกระบวนการอัปเดต...")
    
    # --- 1. ปิดโปรแกรมหลักก่อน (เพื่อไม่ให้ไฟล์โดน Lock) ---
    print("[*] กำลังปิด Lift Server...")
    if platform.system() == "Windows":
        os.system("taskkill /f /im lift_single_ui.exe >nul 2>&1")
    else:
        os.system("pkill -f lift_single_ui.py >/dev/null 2>&1")
        
    time.sleep(2) # รอให้ระบบคืนค่าไฟล์

    # --- 2. แตกไฟล์อัปเดต ---
    try:
        print("[*] กำลังแตกไฟล์และติดตั้ง...")
        with zipfile.ZipFile(PATCH_FILE, 'r') as zip_ref:
            # ดึงเวอร์ชันใหม่จากใน Zip (ถ้ามี)
            new_version = "Unknown"
            if "version.txt" in zip_ref.namelist():
                with zip_ref.open("version.txt") as f:
                    new_version = f.read().decode('utf-8').strip()

            for file_info in zip_ref.infolist():
                # ป้องกันการเขียนทับไฟล์ตั้งค่า
                if file_info.filename in ["lift_config.json", "config.json"]:
                    print(f"[!] ข้ามการเขียนทับไฟล์: {file_info.filename} (เพื่อรักษาการตั้งค่าเดิม)")
                    continue
                zip_ref.extract(file_info, ".")
        
        # --- 3. อัปเดตไฟล์เวอร์ชันในเครื่อง ---
        if new_version != "Unknown":
            with open(LOCAL_VERSION_FILE, "w") as f:
                f.write(new_version)
            print(f"[🎉] อัปเดตเป็นเวอร์ชัน v{new_version} สำเร็จ!")
        else:
            print(f"[🎉] ติดตั้ง Patch สำเร็จ!")

        # --- 4. ลบไฟล์ Zip ทิ้งหลังทำเสร็จ ---
        os.remove(PATCH_FILE)
        
    except Exception as e:
        print(f"\n[⚠️] เกิดข้อผิดพลาดระหว่างการอัปเดต: {e}")
        return False

    return True

if __name__ == "__main__":
    print("=== Lift System Manual Updater ===")
    current = get_local_version()
    print(f"[*] เวอร์ชันปัจจุบัน: v{current}")
    
    if apply_patch():
        print("\n[OK] การอัปเดตเสร็จสมบูรณ์")
        if platform.system() == "Windows":
            print("[*] คุณสามารถเปิด 'lift_single_ui.exe' เพื่อใช้งานเวอร์ชันใหม่ได้ทันที")
        else:
            print("[*] คุณสามารถรัน 'python3 lift_single_ui.py' ได้ทันที")
    
    print("\n===============================")
    input("\nกด Enter เพื่อออกจากโปรแกรม...")
