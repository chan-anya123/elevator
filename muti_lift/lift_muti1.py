import time, threading, requests, json
from pyModbusTCP.client import ModbusClient
from enum import IntEnum
import subprocess

class Sequence(IntEnum):
    IDLE    = 0
    MOVING  = 1
    ARRIVED = 2
    PULSING = 3
    DONE    = 4
    ERROR   = 5

class LedColor(IntEnum):
    GREEN  = 1
    BLUE   = 2
    PURPLE = 3
    RED    = 4
    OFF    = 5
    YELLOW = 6
    ORANGE = 7
    PINK   = 8
    WHITE  = 9

class Tcp_address(IntEnum):
    COMMAND_LIFT    = 3
    LED_COLOR       = 5
    LED_BRIGHT      = 7
    # LIFT A
    CURRENT_FLOOR_A = 0
    LIFT_STATUS_A   = 2
    DOOR_STATUS_A   = 4
    TARGET_A        = 6
    # LIFT B
    CURRENT_FLOOR_B = 10
    LIFT_STATUS_B   = 12
    DOOR_STATUS_B   = 14
    TARGET_B        = 16

class MasterSystem:
    def __init__(self, config_path='lift_config.json'):
        self.load_config(config_path)
        self.lock = threading.Lock()
        self.stations = {}
        
        self.lifts = {
            "A": {"floor": -1, "door": "CLOSED", "busy": False},
            "B": {"floor": -1, "door": "CLOSED", "busy": False}
        }
        self.prev_status = {
            "A": {"floor": -2, "door": ""},
            "B": {"floor": -2, "door": ""}
        }

        self.robot_client = ModbusClient(host=self.robot_ip, port=502, auto_open=True, timeout=1.0)
        self.discover_stations()
        
        threading.Thread(target=self.main_control_loop, daemon=True).start()
        threading.Thread(target=self.mission_scanner_loop, daemon=True).start()
        self.log(f"🚀 Master System Started (Robot: {self.robot_ip})")

    def load_config(self, path):
        try:
            with open(path, 'r') as f:
                config = json.load(f)
                self.config_lifts = config['lifts']
                self.robot_ip = config['robot_ip']
                self.bright = config['settings']['brightness']
                self.max_timeout = config['settings']['max_timeout']
        except Exception as e:
            print(f"❌ Load Config Error: {e}")
            exit(1)

    def log(self, msg):
        print(f"{msg}")

    def write_led(self, floor_id, color_id, bright=None):
        val_bright = bright if bright is not None else self.bright
        self.write_modbus(floor_id, Tcp_address.LED_BRIGHT, val_bright)
        self.write_modbus(floor_id, Tcp_address.LED_COLOR, color_id)

    def read_modbus(self, floor_id, reg, count=1):
        if floor_id not in self.stations: return None
        client = self.stations[floor_id]['client']
        with self.lock:
            try:
                if not client.is_open: client.open()
                res = client.read_holding_registers(reg, count)
                if res is None: client.close()
                return res
            except:
                client.close()
                return None

    def write_modbus(self, floor_id, reg, val):
        if floor_id not in self.stations: return None
        client = self.stations[floor_id]['client']
        with self.lock:
            try:
                if not client.is_open: client.open()
                res = client.write_single_register(reg, val)
                if res is None: client.close()
                else: time.sleep(0.02) # Slightly reduced delay
                return res
            except:
                client.close()
                return None

    def discover_stations(self):
        for item in self.config_lifts:
            ip = item['ip']
            try:
                r = requests.get(f"http://{ip}:5000/status", timeout=1.5)
                if r.status_code == 200:
                    f_id = int(r.json().get("floor"))
                    client = ModbusClient(host=ip, port=502, auto_open=True, timeout=0.5)
                    self.stations[f_id] = {
                        'ip': ip, 
                        'client': client,
                        'last_heartbeat': -1,
                        'last_update_time': time.time(),
                        'fail_count': 0,
                        'is_active': True
                    }
                    self.log(f"✅ Floor {f_id} discovered at {ip}")
            except:
                self.log(f"⚠️ Cannot reach station at {ip}")

    def print_dashboard_on_change(self):
        st = self.lifts
        pr = self.prev_status
        changed = (st["A"]["floor"] != pr["A"]["floor"] or st["A"]["door"] != pr["A"]["door"] or
                   st["B"]["floor"] != pr["B"]["floor"] or st["B"]["door"] != pr["B"]["door"])
        if changed:
            print(f"--- [UPDATE] --- A: F{st['A']['floor']} ({st['A']['door']}) | B: F{st['B']['floor']} ({st['B']['door']})")
            self.prev_status["A"] = st["A"].copy()
            self.prev_status["B"] = st["B"].copy()

    def sync_to_robot(self, reg, val):
        try: return self.robot_client.write_single_register(reg, val)
        except: return None

    def check_for_target(self):
        try:
            res = self.robot_client.read_holding_registers(Tcp_address.TARGET, 1)
            if res and res[0] > 0:
                target = res[0]
                threading.Thread(target=self.run_mission, args=(target,), daemon=True).start()
        except: pass

    def main_control_loop(self):
        """
        Full Control Loop: 
        1. Sync data between Robot and MPU 
        2. Monitor Lift Health (Heartbeat/Connection)
        3. Reverse Sync Physical Button Press to Robot
        """
        while True:
            try:
                # --- 1. อ่านข้อมูลจาก Robot PLC ---
                robot_data = self.robot_client.read_holding_registers(0, 20)
                if not robot_data:
                    # ถ้าเชื่อมต่อ Robot ไม่ได้ ให้รอซักพักแล้วลองใหม่
                    time.sleep(1)
                    continue

                f_a_robot = robot_data[Tcp_address.CURRENT_FLOOR_A]
                f_b_robot = robot_data[Tcp_address.CURRENT_FLOOR_B]
                
                # อัปเดตสถานะที่ Master รู้จำ
                self.lifts["A"]["floor"] = f_a_robot
                self.lifts["B"]["floor"] = f_b_robot

                # --- 2. วนลูปเช็คสถานะแต่ละชั้น (MPU Stations) ---
                for f_id, station in self.stations.items():
                    # A. ส่งสถานะ Floor ไปแสดงผลที่หน้าจอ MPU
                    self.write_modbus(f_id, Tcp_address.CURRENT_FLOOR_A, f_a_robot)

                    # B. อ่านค่าจาก MPU (อ่านทีเดียว 15 Registers เพื่อความเร็ว)
                    # [0]:Floor, [1]:Heartbeat, [4]:DoorStatusA, [14]:DoorStatusB
                    mpu_status = self.read_modbus(f_id, 0, 15)
                    
                    if mpu_status:
                        station['fail_count'] = 0 # Reset ค่าเมื่อการเชื่อมต่อปกติ
                        
                        # --- C. SELF-HEALING LOGIC (Heartbeat) ---
                        new_hb = mpu_status[1]
                        if new_hb != station.get('last_heartbeat', -1):
                            station['last_heartbeat'] = new_hb
                            station['last_update_time'] = time.time()
                            if not station.get('is_active'):
                                self.log(f"✅ Floor {f_id} is now ACTIVE")
                                station['is_active'] = True
                        else:
                            # ถ้า Heartbeat ค้างเกิน 10 วินาที
                            if time.time() - station.get('last_update_time', 0) > 10.0:
                                if station.get('is_active', True):
                                    self.log(f"🚨 Floor {f_id} Heartbeat Frozen! Triggering Restart...")
                                    station['is_active'] = False
                                    self.restart_lift_service(f_id)

                        # --- D. REVERSE SYNC LOGIC (Lift A) ---
                        if mpu_status[4] == 1: # ประตูลิฟต์ A เปิดที่ชั้นนี้ (จากปุ่มกดจริง)
                            self.lifts["A"]["door"], self.lifts["A"]["floor"] = "OPEN", f_id
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_A, 1)    # 1=OPEN
                            self.sync_to_robot(Tcp_address.CURRENT_FLOOR_A, f_id)
                            self.sync_to_robot(Tcp_address.LIFT_STATUS_A, 1)   # 1=ARRIVED
                        elif f_a_robot == f_id:
                            # ถ้า Robot คิดว่าอยู่ที่ชั้นนี้ แต่ MPU บอกประตูปิด
                            self.lifts["A"]["door"] = "CLOSED"
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_A, 2)    # 2=CLOSED
                            # เฉพาะเมื่อไม่ได้รันภารกิจ ให้เซ็ต Status เป็น IDLE
                            if not self.lifts["A"]["busy"]:
                                self.sync_to_robot(Tcp_address.LIFT_STATUS_A, 0)

                        # --- E. REVERSE SYNC LOGIC (Lift B) ---
                        if mpu_status[14] == 1: # ประตูลิฟต์ B เปิดที่ชั้นนี้
                            self.lifts["B"]["door"], self.lifts["B"]["floor"] = "OPEN", f_id
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_B, 1)
                            self.sync_to_robot(Tcp_address.CURRENT_FLOOR_B, f_id)
                            self.sync_to_robot(Tcp_address.LIFT_STATUS_B, 1)
                        elif f_b_robot == f_id:
                            self.lifts["B"]["door"] = "CLOSED"
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_B, 2)
                            if not self.lifts["B"]["busy"]:
                                self.sync_to_robot(Tcp_address.LIFT_STATUS_B, 0)
                    
                    else:
                        # --- F. CONNECTION RECOVERY ---
                        station['fail_count'] = station.get('fail_count', 0) + 1
                        if station['fail_count'] >= 5: # ตรวจไม่เจอ 2 วินาทีต่อเนื่อง
                            if station.get('is_active', True):
                                self.log(f"⚠️ Floor {f_id} OFFLINE. Attempting Recovery...")
                                station['is_active'] = False
                                self.restart_lift_service(f_id)

                # แสดง Dashboard เมื่อมีการเปลี่ยนแปลง
                # self.print_dashboard_on_change()

            except Exception as e:
                # ป้องกัน Loop ตายเมื่อเจอ Error ที่ไม่คาดคิด
                # self.log(f"Main Loop Error: {e}")
                pass
            
            time.sleep(0.4) # ความถี่ในการ Scan (0.4s กำลังดี ไม่กินทรัพยากรเกินไป)

    def mission_scanner_loop(self):
        while True:
            for lid in ["A", "B"]:
                if not self.lifts[lid]["busy"]:
                    reg = Tcp_address.TARGET_A if lid == "A" else Tcp_address.TARGET_B
                    t_val = self.robot_client.read_holding_registers(reg, 1)
                    if t_val and t_val[0] > 0:
                        target = t_val[0]
                        if target in self.stations:
                            self.lifts[lid]["busy"] = True
                            self.sync_to_robot(reg, 0)
                            threading.Thread(target=self.run_mission, args=(target, lid), daemon=True).start()
            time.sleep(0.5)

    def run_mission(self, target, lift_type):
        door_reg = 4 if lift_type == 'A' else 14
        ready_reg = 2 if lift_type == 'A' else 12
        robot_floor_reg = 0 if lift_type == 'A' else 10
        target_robot_reg = Tcp_address.TARGET_A if lift_type == 'A' else Tcp_address.TARGET_B
        
        self.log(f"🚀 MISSION START: {lift_type} -> Floor {target}")
        mission_status = Sequence.IDLE
        start_pulsing_time = None
        
        try:
            r_pos = self.robot_client.read_holding_registers(robot_floor_reg, 1)
            current_f = r_pos[0] if r_pos else 0
            
            action_cmd = 1 if target >= current_f else 2
            led_cmd = 3 if target >= current_f else 4

            while mission_status not in [Sequence.DONE, Sequence.ERROR]:
                match mission_status:
                    case Sequence.IDLE:
                        mission_status = Sequence.MOVING

                    case Sequence.MOVING:
                        self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                        time.sleep(0.3)
                        self.write_modbus(target, Tcp_address.COMMAND_LIFT, led_cmd)
                        self.write_led(target, LedColor.BLUE)
                        mission_status = Sequence.ARRIVED # FIX: was self.mission_status

                    case Sequence.ARRIVED:
                        r_pos_now = self.robot_client.read_holding_registers(robot_floor_reg, 1)
                        d_stat = self.read_modbus(target, door_reg)
                        if (r_pos_now and r_pos_now[0] == target) and (d_stat and d_stat[0] == 1):
                            self.log(f"✅ {lift_type} Arrived at Floor {target}")
                            mission_status = Sequence.PULSING
                        time.sleep(0.5)

                    case Sequence.PULSING:
                        if start_pulsing_time is None: 
                            start_pulsing_time = time.time()
                            self.log(f"⏱️ Pulse Timer Started: F{target}")

                        elapsed = time.time() - start_pulsing_time
                        
                        # ตรวจสอบว่าหมดเวลาหรือยัง
                        if elapsed >= self.max_timeout:
                            self.log(f"⏱️ Pulse Timeout reached at F{target}")
                            mission_status = Sequence.DONE
                            continue

                        door_check = self.read_modbus(target, door_reg)
                        if door_check:
                            door_val = door_check[0]
                            
                            # อัปเดตสถานะประตูไปที่ Robot
                            robot_door_addr = Tcp_address.DOOR_STATUS_A if lift_type == 'A' else Tcp_address.DOOR_STATUS_B
                            self.sync_to_robot(robot_door_addr, 1 if door_val == 1 else 2)
                            
                            if door_val == 1: # ประตูเปิดอยู่ปกติ
                                self.log(f"⚡ Pulse Triggering | Remaining: {self.max_timeout - elapsed:.1f}s")
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, LedColor.PINK)
                                # time.sleep(0.5) 
                                # self.write_modbus(target, Tcp_address.COMMAND_LIFT, solenoid_stop)
                                time.sleep(1.0)
                            else: 
                                # --- ประตูปิดก่อนกำหนด (Door Closed Prematurely) ---
                                self.log(f"⚠️ Door CLOSED - Re-Triggering & Reset Timer")
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, LedColor.RED)
                                
                                # *** RESET TIMER ตามที่คุณต้องการ ***
                                start_pulsing_time = time.time() 
                                time.sleep(1.0)

            if mission_status == Sequence.DONE:
                self.log(f"🎯 MISSION {lift_type} COMPLETE")

        except Exception as e:
            self.log(f"❌ {lift_type} Mission Error: {e}")
            mission_status = Sequence.ERROR
        finally:
            if mission_status == Sequence.ERROR:
                self.handle_error_blink(target)
            self.cleanup_mission(target, lift_type, target_robot_reg, ready_reg)

    def handle_error_blink(self, floor_id):
        """แจ้งเตือนเมื่อเกิด Error ด้วยไฟกระพริบสีแดง"""
        self.log(f"🚨 Error blinking at Floor {floor_id}")
        for _ in range(5):
            self.write_led(floor_id, LedColor.RED)
            time.sleep(0.3)
            self.write_led(floor_id, LedColor.OFF)
            time.sleep(0.3)

    def cleanup_mission(self, floor_id, lift_type, target_reg, ready_reg):
        """คืนค่า Register และสถานะหลังจบงาน"""
        try:
            # หยุดคำสั่งลิฟต์และเปิดไฟเขียว
            self.write_modbus(floor_id, Tcp_address.COMMAND_LIFT, 0)
            self.write_led(floor_id, LedColor.GREEN)
            
            for _ in range(2):
                self.sync_to_robot(target_reg, 0)
                self.sync_to_robot(ready_reg, 0)
                time.sleep(0.1)
        except Exception as e:
            self.log(f"⚠️ Cleanup error: {e}")

        time.sleep(1.0) # Cooldown กันการรับงานซ้อนทันที
        self.lifts[lift_type]["busy"] = False

    def release_busy(self, lift_type):
        if lift_type == 'A': self.busy_a = False
        else: self.busy_b = False

    def restart_lift_service(self, floor_id):
        if floor_id not in self.stations: return
        
        ip = self.stations[floor_id]['ip']
        self.log(f"🔄 Self-Healing: Restarting service on Floor {floor_id} ({ip})...")
        ssh_cmd = f"ssh -o ConnectTimeout=5 arduino@{ip} 'sudo systemctl restart lift-service.service'"
        
        try:
            
            subprocess.Popen(ssh_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.stations[floor_id]['last_update_time'] = time.time() + 15.0 
        except Exception as e:
            self.log(f"❌ SSH Restart Failed: {e}")

if __name__ == '__main__':
    master = MasterSystem()
    while True: time.sleep(1)