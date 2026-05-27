from pyModbusTCP.client import ModbusClient
import time

# ทดสอบกับบอร์ด Floor 1 Lift A
c = ModbusClient(host="192.168.20.66", port=502, auto_open=True)

def test_procedure():
    if not c.is_open:
        if not c.open():
            print("❌ Cannot connect to MPU")
            return

    print("--- STEP 1: Set Brightness & Color ---")
    c.write_single_register(7, 50)  # Brightness 50%
    c.write_single_register(5, 2)   # Color BLUE (2)
    print("✅ LED should be BLUE now")
    time.sleep(1)

    print("--- STEP 2: Trigger Solenoid (UP) ---")
    # ส่ง 1 เพื่อสั่ง Solenoid ทำงาน (ทิศทางขึ้น)
    if c.write_single_register(3, 1):
        print("✅ Solenoid UP activated")
    time.sleep(2) # เปิดค้างไว้ 2 วินาที

    print("--- STEP 3: Stop Solenoid but Keep LED ---")
    c.write_single_register(3, 0)   # Stop Solenoid
    print("✅ Solenoid Stopped, LED should still be BLUE")
    
    time.sleep(1)
    
    print("--- STEP 4: Reset All ---")
    c.write_single_register(5, 1)   # Back to GREEN (1)
    # c.write_single_register(5, 5) # หรือ OFF (5)
    print("✅ System Reset to Standby")

if __name__ == "__main__":
    test_procedure()
    c.close()