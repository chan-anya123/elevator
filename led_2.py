import time, threading, subprocess
from pyModbusTCP.client import ModbusClient

# --- Configuration ---
MPU_IP = '192.168.20.60'
PORT = 1502
HOLD_TIME = 5
COLOR_REG = 5
HEARTBEAT_REG = 1
BRIGHT_REG = 7
COMMAND_REG = 3  # Register 

class RobustMasterMonitor:
    def __init__(self, ip):
        self.ip = ip
        self.client = ModbusClient(host=self.ip, port=1502, auto_open=True, timeout=2.0)
        
        self.station = {
            'last_heartbeat': -1,
            'last_update_time': time.time(),
            'fail_count': 0,
            'is_active': True,
            'system_error': False  # เพิ่มสถานะตรวจจับ Error ของระบบ
        }
        
        self.lift_current_floor = 0
        self.lift_door_status = 2 # 2=CLOSED
        
        print(f"🚀 Monitoring System Started for {self.ip}")

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")

    def restart_lift_service(self):
        self.log(f"🔄 Triggering SSH Restart on {self.ip}...")
        ssh_cmd = f"ssh -o ConnectTimeout=5 arduino@{self.ip} 'sudo systemctl restart lift-service.service'"
        try:
            subprocess.Popen(ssh_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.station['last_update_time'] = time.time() + 15.0 
            self.log("⏳ Restart command sent. Cooling down for 15s...")
        except Exception as e:
            self.log(f"❌ SSH Error: {e}")

    def reconnect_logic(self):
        self.log("🔌 Connection Lost. Attempting Reconnect...")
        self.client.close()
        time.sleep(1)
        if self.client.open():
            self.log("✅ Reconnected!")
            return True
        return False

    def global_monitor_loop(self):
        """ ลูปมอนิเตอร์สถานะ และจัดการ Safety Stop ทันทีเมื่อเจอ Error """
        while True:
            try:
                data = self.client.read_holding_registers(0, 6)
                if data:
                    self.station['fail_count'] = 0
                    
                    # --- 1. HEARTBEAT CHECK ---
                    current_hb = data[1]
                    if current_hb != self.station['last_heartbeat']:
                        self.station['last_heartbeat'] = current_hb
                        self.station['last_update_time'] = time.time()
                        if not self.station['is_active']:
                            self.log(f"❇️ Floor MPU is now ACTIVE (HB: {current_hb})")
                            self.station['is_active'] = True
                    else:
                        if time.time() - self.station['last_update_time'] > 7.0:
                            if self.station['is_active']:
                                self.log(f"🚨 ALERT: HEARTBEAT FROZEN. Restarting...")
                                self.station['is_active'] = False
                                self.restart_lift_service()

                    # --- 2. ERROR & SAFETY LOGIC ---
                    # เช็ค Register 2 (Lift Status) ว่าติด Error (ค่า 5) หรือไม่
                    if data[2] == 5:
                        self.log("🚨 LIFT ERROR DETECTED (Status 5)! Sending Stop Command...")
                        self.client.write_single_register(COMMAND_REG, 0)
                        self.station['system_error'] = True
                    else:
                        self.station['system_error'] = False

                    # อัปเดต Floor & Door
                    if data[4] == 1: 
                        self.lift_current_floor = data[0]
                        self.lift_door_status = 1
                    
                else:
                    # --- 3. CONNECTION LOST ---
                    self.station['fail_count'] += 1
                    if self.station['fail_count'] >= 3:
                        if self.station['is_active']:
                            self.log("⚠️ Station OFFLINE. Triggering Safety Stop...")
                            self.client.write_single_register(COMMAND_REG, 0) # พยายามสั่งหยุด
                            self.station['is_active'] = False
                            self.reconnect_logic()

            except Exception as e:
                self.log(f"⚠️ Loop Error: {e}")
            
            time.sleep(0.5)

    def test_led_sequence(self):
        """ ลูปทดสอบ: สีไฟสัมพันธ์กับทิศทางลิฟต์ และสถานะไฟปุ่มกด """
        # Scenario: Color -> (ColorID, LiftAction, LED_Action)
        scenarios = {
            "BLUE (UP)": (2, 1, 3),    # สีน้ำเงิน -> สั่งขึ้น (1) + เปิดไฟขึ้น (3)
            "GREEN (DOWN)": (1, 2, 4), # สีเขียว -> สั่งลง (2) + เปิดไฟลง (4)
            "RED (STOP)": (4, 5, 6),   # สีแดง -> หยุดsolinoid (5) + ดับไฟปุ่ม (6)
            "PURPLE (IDLE)": (3, 0, 6) # สีม่วง -> หยุด/สแตนบาย (0) + ดับไฟปุ่ม (6)
        }
        
        self.client.write_single_register(BRIGHT_REG, 20)
        
        while True:
            if self.station['is_active'] and not self.station['system_error']:
                for name, (cid, lift_cmd, btn_led_cmd) in scenarios.items():
                    if not self.station['is_active'] or self.station['system_error']:
                        break
                        
                    self.log(f"🎨 Scenario: {name} | Lift: {lift_cmd} | BtnLED: {btn_led_cmd}")
                    
                    # 1. เปลี่ยนสีไฟ WS2812 (แถบไฟสถานะ)
                    self.client.write_single_register(COLOR_REG, cid)
                    
                    # 2. ส่งคำสั่งคุม Solenoid (ขึ้น/ลง/หยุด)
                    self.client.write_single_register(COMMAND_REG, lift_cmd)
                    
                    # 3. ส่งคำสั่งคุม LED ที่ปุ่มกด (ตามที่คุณต้องการ)
                    # เราจะใช้หน่วงเวลาเล็กน้อยเพื่อให้ MPU ประมวลผลทัน (เพราะส่ง 2 คำสั่งติดกัน)
                    time.sleep(0.2) 
                    success = self.client.write_single_register(COMMAND_REG, btn_led_cmd)
                    
                    if not success:
                        self.log(f"❌ Write Failed at {name}")
                        break
                        
                    time.sleep(HOLD_TIME)
            else:
                # Safety Stop: หยุดลิฟต์ (0) และ ดับไฟปุ่ม (6)
                self.client.write_single_register(COMMAND_REG, 0)
                time.sleep(0.2)
                self.client.write_single_register(COMMAND_REG, 6)
                time.sleep(2)

if __name__ == "__main__":
    monitor = RobustMasterMonitor(MPU_IP)
    t = threading.Thread(target=monitor.global_monitor_loop, daemon=True)
    t.start()
    monitor.test_led_sequence()