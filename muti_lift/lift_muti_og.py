import time, threading, requests
from pyModbusTCP.client import ModbusClient
from enum import IntEnum

class Sequence(IntEnum):
    IDLE    = 0
    MOVING  = 1
    ARRIVED = 2
    PULSING = 3
    DONE    = 4
    ERROR   = 5

class Tcp_address(IntEnum):
    COMMAND_LIFT    = 3
    LED_COLOR       = 5
    LED_BRIGHT      = 7

    CURRENT_FLOOR_A = 0
    LIFT_STATUS_A   = 2
    DOOR_STATUS_A   = 4
    TARGET_A        = 6

    CURRENT_FLOOR_B = 10
    LIFT_STATUS_B   = 12
    DOOR_STATUS_B   = 14
    TARGET_B        = 16
    
LIFT_IP_LIST = ['192.168.20.52', '192.168.20.66']
ROBOT_IP = '192.168.10.5'

class MasterSystem:
    def __init__(self):
        self.lock = threading.Lock() # เพิ่ม Lock ป้องกันการอ่านซ้อน
        self.busy_a = False
        self.busy_b = False
        self.stations = {}
        self.max_timeout = 5
        self.robot_client = ModbusClient(host=ROBOT_IP, port=502, auto_open=True, timeout=0.5)
        self.bright = 10
        
        # เก็บสถานะปัจจุบัน
        self.current_status = {
            "A": {"floor": 0, "door": "CLOSED"},
            "B": {"floor": 0, "door": "CLOSED"}
        }
        # เก็บสถานะก่อนหน้าเพื่อเช็คการเปลี่ยนแปลง
        self.prev_status = {
            "A": {"floor": -1, "door": ""},
            "B": {"floor": -1, "door": ""}
        }
        
        self.log("🔍 Scanning for Lift Stations...")
        self.discover_stations()
        
        threading.Thread(target=self.main_control_loop, daemon=True).start()
        self.log("🚀 Master System is Running (Logging on change only)")

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def print_dashboard_on_change(self):
        st = self.current_status
        pr = self.prev_status
        
        # เช็คว่า Floor หรือ Door ของ A หรือ B มีอะไรเปลี่ยนไหม
        changed = (st["A"]["floor"] != pr["A"]["floor"] or 
                   st["A"]["door"] != pr["A"]["door"] or
                   st["B"]["floor"] != pr["B"]["floor"] or 
                   st["B"]["door"] != pr["B"]["door"])
        
        if changed:
            print(f"--- [UPDATE] --- A: F{st['A']['floor']} ({st['A']['door']}) | B: F{st['B']['floor']} ({st['B']['door']})")
            # อัปเดตสถานะล่าสุดเก็บไว้
            self.prev_status["A"] = st["A"].copy()
            self.prev_status["B"] = st["B"].copy()

    def write_to_robot(self, reg, val):
        try: return self.robot_client.write_single_register(reg, val)
        except: return None

    def write_led(self, floor_id, color_id, bright=None):
        if floor_id not in self.stations: return False
        cfg = self.stations[floor_id]; client = cfg['client']
        val_bright = bright if bright is not None else self.bright
        try:
            if not client.is_open: client.open()
            client.write_single_register(Tcp_address.LED_BRIGHT, val_bright)
            client.write_single_register(Tcp_address.LED_COLOR, color_id)
            return True
        except: return False

    def discover_stations(self):
        for ip in LIFT_IP_LIST:
            try:
                r = requests.get(f"http://{ip}:5000/status", timeout=1.5)
                if r.status_code == 200:
                    f_id = int(r.json().get("floor"))
                    client = ModbusClient(host=ip, port=502, auto_open=True, timeout=0.3)
                    self.stations[f_id] = {'ip': ip, 'client': client, 'is_busy_a': False, 'is_busy_b': False}
                    self.log(f"✅ Found Floor {f_id} at {ip}")
                    self.write_led(f_id, 1)
            except Exception as e: self.log(f"❌ Connection error {ip}: {e}")

    def main_control_loop(self):
        """Loop หลัก: Sync ข้อมูล 2 ทาง (Robot <-> MPU) และรองรับ Physical Button"""
        while True:
            try:
                # 1. อ่านข้อมูลปัจจุบันจาก Robot
                robot_data = self.robot_client.read_holding_registers(0, 11)
                if robot_data:
                    f_a, f_b = robot_data[0], robot_data[10]
                    self.current_status["A"]["floor"] = f_a
                    self.current_status["B"]["floor"] = f_b
                    
                    # 2. ตรวจสอบสถานะจาก MPU ทุกชั้น
                    for f_id, cfg in self.stations.items():
                        client = cfg['client']
                        if not client.is_open: client.open()
                        
                        # ส่งข้อมูล Floor จาก Robot ไปโชว์ที่หน้าจอ MPU (ปกติ)
                        client.write_single_register(Tcp_address.CURRENT_FLOOR_A, f_a)
                        
                        # อ่านสถานะจาก MPU (เพื่อเช็คว่ามีการกดปุ่ม Physical จนประตูเปิดหรือไม่)
                        mpu_status = client.read_holding_registers(0, 15)
                        if mpu_status:
                            val_door_a = mpu_status[4]   
                            val_door_b = mpu_status[14]
                            
                            # --- [PHYSICAL BUTTON LOGIC / REVERSE SYNC] ---
                            if val_door_a == 1: 
                                self.current_status["A"]["door"] = "OPEN"
                                self.current_status["A"]["floor"] = f_id # Force update current floor
                                self.write_to_robot(Tcp_address.DOOR_STATUS_A, 1)
                                self.write_to_robot(Tcp_address.CURRENT_FLOOR_A, f_id) # Sync floor back to Robot
                            else:
                                if f_a == f_id: # ถ้าปิดอยู่แต่เป็นชั้นที่ Robot คุมอยู่
                                    self.current_status["A"]["door"] = "CLOSED"
                                    self.write_to_robot(Tcp_address.DOOR_STATUS_A, 2)

                            # ทำแบบเดียวกันกับลิฟต์ B
                            if val_door_b == 1:
                                self.current_status["B"]["door"] = "OPEN"
                                self.current_status["B"]["floor"] = f_id
                                self.write_to_robot(Tcp_address.DOOR_STATUS_B, 1)
                                self.write_to_robot(Tcp_address.CURRENT_FLOOR_B, f_id) # Sync floor back to Robot
                            else:
                                if f_b == f_id:
                                    self.current_status["B"]["door"] = "CLOSED"
                                    self.write_to_robot(Tcp_address.DOOR_STATUS_B, 2)
                
                self.print_dashboard_on_change()
                
            except Exception as e: pass
            
            self.check_missions()
            time.sleep(0.3)

    def check_missions(self):
        with self.lock: # ล็อกไว้ก่อนเพื่อไม่ให้ Thread อื่นเข้ามาอ่านช่วงนี้
            try:
                # ตรวจสอบลิฟต์ A
                if not self.busy_a:
                    t_a = self.robot_client.read_holding_registers(Tcp_address.TARGET_A, 1)
                    if t_a and t_a[0] > 0:
                        target = t_a[0]
                        self.busy_a = True # ล็อกทันทีที่อ่านเจอ
                        self.robot_client.write_single_register(Tcp_address.TARGET_A, 0) # เคลียร์ที่ Robot ทันที
                        threading.Thread(target=self.run_mission, args=(target, 'A'), daemon=True).start()

                # ตรวจสอบลิฟต์ B
                if not self.busy_b:
                    t_b = self.robot_client.read_holding_registers(Tcp_address.TARGET_B, 1)
                    if t_b and t_b[0] > 0:
                        target = t_b[0]
                        self.busy_b = True # ล็อกทันที
                        self.robot_client.write_single_register(Tcp_address.TARGET_B, 0) # เคลียร์ที่ Robot ทันที
                        threading.Thread(target=self.run_mission, args=(target, 'B'), daemon=True).start()
            except Exception as e:
                pass

    def run_mission(self, target, lift_type):
        # 1. เตรียม Register ให้ถูกตัว
        door_reg = 4 if lift_type == 'A' else 14
        ready_reg = 2 if lift_type == 'A' else 12
        robot_floor_reg = 0 if lift_type == 'A' else 10
        target_robot_reg = Tcp_address.TARGET_A if lift_type == 'A' else Tcp_address.TARGET_B
        
        cfg = self.stations[target]; client = cfg['client']
        self.log(f"🚀 MISSION START: {lift_type} -> Floor {target}")
        mission_status = Sequence.IDLE
        start_pulsing_time = None
        
        try:
            # หา Action Command (1=UP/OPEN, 2=DOWN)
            robot_pos = self.robot_client.read_holding_registers(robot_floor_reg, 1)
            current_f = robot_pos[0] if robot_pos else 0
            action_cmd = 1 if target >= current_f else 2

            while mission_status not in [Sequence.DONE, Sequence.ERROR]:
                match mission_status:
                    case Sequence.IDLE:
                        mission_status = Sequence.MOVING

                    case Sequence.MOVING:
                        client.write_single_register(Tcp_address.COMMAND_LIFT, action_cmd)
                        self.write_led(target, 2) # Blue
                        mission_status = Sequence.ARRIVED

                    case Sequence.ARRIVED:
                        r_pos = self.robot_client.read_holding_registers(robot_floor_reg, 1)
                        d_stat = client.read_holding_registers(door_reg, 1)
                        if (r_pos and r_pos[0] == target) and (d_stat and d_stat[0] == 1):
                            self.log(f"✅ {lift_type} Ready at Floor {target}")
                            mission_status = Sequence.PULSING
                        time.sleep(0.5)

                    case Sequence.PULSING:
                        if start_pulsing_time is None: 
                            start_pulsing_time = time.time()
                            self.log(f"⏱️ Start Timer for Floor {target}")

                        elapsed = time.time() - start_pulsing_time
                        if elapsed >= self.max_timeout:
                            mission_status = Sequence.DONE # ใช้ local variable mission_status
                            continue

                        # อ่านค่าจาก client ของชั้นนั้นๆ
                        door_check = client.read_holding_registers(door_reg, 1)
                        if door_check:
                            current_door = door_check[0]
                            
                            if current_door == 1: # 1 = OPEN
                                self.log(f"⚡ Pulse Trigger | {elapsed:.1f}s")
                                client.write_single_register(Tcp_address.COMMAND_LIFT, 1)
                                self.write_led(target, 3) # Pink (Loading)
                                self.write_to_robot(ready_reg, 1) # แจ้ง Robot ว่าพร้อมทำงาน
                                time.sleep(1.2) 
                                client.write_single_register(Tcp_address.COMMAND_LIFT, 0)
                                time.sleep(0.2)
                            else:
                                # กรณีประตูปิดก่อนกำหนด
                                self.log(f"⚠️ Door CLOSED prematurely at {elapsed:.1f}s | RE-OPENING & RESET TIMER")
                                self.write_to_robot(ready_reg, 0)
                                client.write_single_register(Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, 4) # Red (Warning)
                                
                                # RESET TIMER ตามที่คุณต้องการ
                                start_pulsing_time = time.time() 
                                time.sleep(1.5) # รอให้กลไกประตูเริ่มทำงานก่อนวน Loop ใหม่

            if mission_status == Sequence.DONE:
                self.log(f"🎯 MISSION {lift_type} COMPLETE")

        except Exception as e:
            self.log(f"❌ {lift_type} Mission Error: {e}")
            mission_status = Sequence.ERROR
        finally:
            if mission_status == Sequence.ERROR:
                # แก้ไขการส่ง Arguments ให้ตรงกับนิยามฟังก์ชัน (floor_id, lift_type, target_reg, ready_reg)
                self.handle_error_blink(target, lift_type, target_robot_reg, ready_reg)
            self.cleanup_mission(target, lift_type, target_robot_reg, ready_reg)

    def handle_error_blink(self, floor_id, target_reg, ready_reg, times=5):
        self.log(f"🚨 [Alert] Error LED Blinking at Floor {floor_id}")
        client = self.stations[floor_id]['client']
        
        # เคลียร์ค่าทันที (ตรวจสอบ address ว่าเป็น int)
        client.write_single_register(Tcp_address.COMMAND_LIFT, 0)
        self.robot_client.write_single_register(int(target_reg), 0) # บังคับเป็น int
        self.robot_client.write_single_register(int(ready_reg), 0)

        for _ in range(times):
            self.write_led(floor_id, 4) # Red
            time.sleep(0.3)
            self.write_led(floor_id, 5) # Off
            time.sleep(0.3)

    def cleanup_mission(self, floor_id, lift_type, target_reg, ready_reg):
        try:
            client = self.stations[floor_id]['client']
            client.write_single_register(Tcp_address.COMMAND_LIFT, 0)
            self.write_led(floor_id, 1) # Green
            for _ in range(3):
                self.robot_client.write_single_register(target_reg, 0)
                self.robot_client.write_single_register(ready_reg, 0)
                time.sleep(0.1)
        except: pass

        time.sleep(1.0) # Cooldown
        self.release_busy(lift_type)

    def release_busy(self, lift_type):
        if lift_type == 'A': self.busy_a = False
        else: self.busy_b = False

if __name__ == '__main__':
    master = MasterSystem()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: print("\nStopping...")