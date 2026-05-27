import time, threading
from pyModbusTCP.client import ModbusClient

# --- Configuration ---
# ระบุ IP ให้ตรงกับบอร์ดที่ติดตั้งในแต่ละลิฟต์
CONFIG = [
    # ลิฟต์ A (ใช้ Register 0-7)
    # {'ip': '192.168.20.42', 'type': 'A'}, 
    {'ip': '192.168.20.60', 'type': 'A'},
    {'ip': '192.168.20.49', 'type': 'A'},
    # {'ip': '192.168.20.52', 'type': 'A'},
    
    # ลิฟต์ B (ใช้ Register 10-17)
    # {'ip': '192.168.20.37', 'type': 'B'},
    # {'ip': '192.168.20.66', 'type': 'B'},
    # {'ip': '192.168.20.38', 'type': 'B'},
    # {'ip': '192.168.20.43', 'type': 'B'}
]

HOLD_TIME = 3

class LiftController:
    def __init__(self, ip, lift_type):
        self.ip = ip
        self.lift_type = lift_type
        
        # ตั้งค่า Register ตามประเภทลิฟต์
        if lift_type == 'A':
            self.reg_command = 3
            self.reg_led     = 5
            self.reg_bright  = 7
        else: # Lift B
            self.reg_command = 13
            self.reg_led     = 15
            self.reg_bright  = 17
            
        self.client = ModbusClient(host=self.ip, port=1502, auto_open=True, timeout=2.0)

    def log(self, msg):
        print(f"[{self.ip} | LIFT {self.lift_type}] {msg}")

    def run_test(self):
        # Scenario: (ข้อความ, สีไฟ, คำสั่งเคลื่อนที่, คำสั่งไฟปุ่ม)
        scenarios = [
            ("UP SEQUENCE", 2, 1, 3),    # BLUE, Move UP, LED UP
            ("DOWN SEQUENCE", 1, 2, 4),  # GREEN, Move DOWN, LED DOWN
            ("STOP / IDLE", 4, 0, 6)     # RED, STOP, LED OFF
        ]
        
        try:
            # เริ่มต้น: ตั้งความสว่าง 10%
            self.client.write_single_register(self.reg_bright, 50)
            self.log("✅ Initialized and Ready")
            
            while True:
                for msg, color_id, lift_cmd, led_cmd in scenarios:
                    self.log(f"🎬 {msg}")
                    
                    # 1. สั่งสีไฟสถานะ
                    self.client.write_single_register(self.reg_led, color_id)
                    time.sleep(0.5)
                    
                    # 2. สั่งการลิฟต์ (Solenoid)
                    self.client.write_single_register(self.reg_command, lift_cmd)
                    time.sleep(0.1)
                    
                    # 3. สั่งเปิดไฟที่ปุ่มกด
                    self.client.write_single_register(self.reg_command, led_cmd)
                    
                    time.sleep(HOLD_TIME)
                    
        except Exception as e:
            self.log(f"⚠️ Error: {e}")
        finally:
            self.client.close()

def start_thread(conf):
    controller = LiftController(conf['ip'], conf['type'])
    controller.run_test()

if __name__ == "__main__":
    print(f"🚀 Starting Dual Lift System (2x Lift A, 2x Lift B)")
    print("-" * 50)
    
    threads = []
    for conf in CONFIG:
        t = threading.Thread(target=start_thread, args=(conf,), daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.5) # เว้นจังหวะสั้นๆ ในการเริ่มแต่ละบอร์ด

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 System Stopped")