import time, threading, subprocess
from pyModbusTCP.client import ModbusClient

# --- Configuration ---
MPU_IP = '192.168.20.49'
PORT = 1502
COLOR_REG = 5
HEARTBEAT_REG = 1
COMMAND_REG = 3
BRIGHT_REG = 7

class RobustLiftController:
    def __init__(self, ip):
        self.ip = ip
        self.client = ModbusClient(host=self.ip, port=PORT, auto_open=True, timeout=2.0)
        self.station = {
            'last_heartbeat': -1,
            'last_update_time': time.time(),
            'is_active': True,
            'system_error': False,
            'door_open': False,
            'current_floor': 0
        }
        self.running = True

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def set_visuals(self, color_id, brightness=10):
        self.client.write_single_register(BRIGHT_REG, brightness)
        self.client.write_single_register(COLOR_REG, color_id)

    def global_monitor_loop(self):
        """ ลูปอ่านค่าสถานะจาก MPU ตลอดเวลา """
        while self.running:
            try:
                data = self.client.read_holding_registers(0, 6)
                if data:
                    # Heartbeat Check
                    if data[1] != self.station['last_heartbeat']:
                        self.station['last_heartbeat'] = data[1]
                        self.station['last_update_time'] = time.time()
                        self.station['is_active'] = True
                    elif time.time() - self.station['last_update_time'] > 7.0:
                        self.station['is_active'] = False

                    # Status & Safety
                    self.station['system_error'] = (data[2] == 5)
                    self.station['current_floor'] = data[0]
                    self.station['door_open'] = (data[4] == 1) # 1=Open, 2=Closed

                    if self.station['system_error']:
                        self.set_visuals(4) # RED for Error
                else:
                    self.station['is_active'] = False
                    
            except Exception as e:
                self.log(f"Monitor Error: {e}")
            time.sleep(0.5)

    # def global_monitor_loop(self):
    #     while self.running:
    #         try:
    #             data = self.client.read_holding_registers(10, 5) 
                
    #             if data:
    #                 # data[0] คือ Address 10 (Floor)
    #                 # data[1] คือ Address 11 (Heartbeat)
    #                 # data[2] คือ Address 12 (Status)
    #                 # data[3] คือ Address 13 (Command)
    #                 # data[4] คือ Address 14 (Door)
    #                 # Heartbeat Check (ใช้ data[1] ซึ่งตรงกับ Register 11)
    #                 if data[1] != self.station['last_heartbeat']:
    #                     self.station['last_heartbeat'] = data[1]
    #                     self.station['last_update_time'] = time.time()
    #                     self.station['is_active'] = True
    #                 elif time.time() - self.station['last_update_time'] > 7.0:
    #                     self.station['is_active'] = False

    #                 # Status & Safety (ใช้ data[2] ซึ่งตรงกับ Register 12)
    #                 self.station['system_error'] = (data[2] == 5)
    #                 self.station['current_floor'] = data[0]
                    
    #                 # Door Status (ใช้ data[4] ซึ่งตรงกับ Register 14)
    #                 self.station['door_open'] = (data[4] == 1) # 1=Open, 2=Closed

    #                 if self.station['system_error']:
    #                     self.set_visuals(4) # RED for Error
    #             else:
    #                 self.station['is_active'] = False
                    
    #         except Exception as e:
    #             self.log(f"Monitor Error: {e}")
    #         time.sleep(0.5)

    def execute_lift_cycle(self, direction_cmd, btn_led_cmd):
        """ ทำงาน 1 รอบ: สั่งเคลื่อนที่ -> รอประตูเปิด -> ย้ำคำสั่ง 2 รอบ -> รอประตูปิด """
        
        # 1. เริ่มเคลื่อนที่ (Moving)
        self.log(f"🚀 Step 1: Moving (CMD: {direction_cmd})")
        self.set_visuals(2) 
        self.client.write_single_register(COMMAND_REG, direction_cmd)
        time.sleep(0.5)
        self.client.write_single_register(COMMAND_REG, btn_led_cmd)

        # 2. รอจนกว่าประตูจะเปิด
        while not self.station['door_open']:
            if not self.station['is_active'] or self.station['system_error']:
                self.log("⚠️ Aborting mission: Station offline or Error")
                return False
            time.sleep(0.1)

        # 3. ประตูเปิดแล้ว: Pulsing 2 รอบ
        self.log(f"🟣 Step 2: Door Opened. Pulsing 2 rounds...")
        self.set_visuals(3)

        for i in range(1, 3):
            self.client.write_single_register(COMMAND_REG, direction_cmd)
            self.log(f"   - Pulse Round {i}/2")
            time.sleep(1.5)

        # 4. รอจนกว่าประตูจะปิด (Finish process)
        self.log("⏳ Step 3: Waiting for door to close...")
        while self.station['door_open']:
            if not self.station['is_active']: return False
            time.sleep(0.1)
        
        self.log("✅ Step 4: Cycle Finished.")
        self.set_visuals(1)
        return True

    def main_logic(self):
        self.set_visuals(2)
        
        try:
            while True:
                if self.station['is_active'] and not self.station['system_error']:
                    # รอบที่ 1: สั่งขึ้น (UP=1, LED_UP=3)
                    if self.execute_lift_cycle(1, 3):
                        time.sleep(2) # พักระหว่างรอบ
                        
                        # รอบที่ 2: สั่งลง (DOWN=2, LED_DOWN=4)
                        self.execute_lift_cycle(2, 4)
                        
                    time.sleep(3) # รอสักพักก่อนเริ่มลูปใหม่
                else:
                    self.set_visuals(4) # RED
                    self.log("⚠️ System not ready... waiting.")
                    time.sleep(2)

        except KeyboardInterrupt:
            self.log("Stop.")
            self.running = False

if __name__ == "__main__":
    controller = RobustLiftController(MPU_IP)
    t = threading.Thread(target=controller.global_monitor_loop, daemon=True)
    t.start()
    controller.main_logic()