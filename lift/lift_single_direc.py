import time
import threading
import requests
from pyModbusTCP.client import ModbusClient
from enum import IntEnum
import json
import subprocess

# --- State Definition ---
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

# --- TCP Register Address ---
class Tcp_address(IntEnum):
    CURRENT_FLOOR_A = 0     # 1, 2, ..         
    HEARTBEAT_A     = 1     # Pulse count (0-65535)
    LIFT_STATUS_A   = 2     # Ready Status (1: Ready, 0: Not Ready)
    COMMAND_LIFT_A  = 3     # 1 (Up), 2 (Down), 0 (Stop)      
    DOOR_STATUS_A   = 4     # (1: OPEN, 2: CLOSED)
    LED_COLOR_A     = 5     # 1=G, 2=B, 3=P, 4=R, 5=S
    TARGET_A        = 6     # 1, 2, ..
    LED_BRIGHT_A    = 7     # 1-100

    CURRENT_FLOOR_B = 10     # 1, 2, ..         
    HEARTBEAT_B     = 11     # Pulse count (0-65535)
    LIFT_STATUS_B   = 12     # Ready Status (1: Ready, 0: Not Ready)
    COMMAND_LIFT_B  = 13     # 1 (Up), 2 (Down), 0 (Stop)      
    DOOR_STATUS_B   = 14     # (1: OPEN, 2: CLOSED)
    LED_COLOR_B     = 15     # 1=G, 2=B, 3=P, 4=R, 5=S
    TARGET_B        = 16     # 1, 2, ..
    LED_BRIGHT_B    = 17     # 1-100

    DIRECT          = 8

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
                    data = r.json()
                    f_id = int(data.get("floor"))
                    l_id = data.get("lift_id", "A")
                    addr_map = data.get("addr", {})
                    station_key = f"{f_id}{l_id}"

                    client = ModbusClient(host=ip, port=502, auto_open=True, timeout=0.5)
                    self.stations[station_key] = {
                        'ip': ip, 
                        'client': client,
                        'lift_id': l_id,
                        'addr': addr_map,
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
            # print(f"--- [UPDATE] --- A: F{st['A']['floor']} ({st['A']['door']}) | B: F{st['B']['floor']} ({st['B']['door']})")
            print(f"--- [UPDATE] --- A: {st['A'].get('floor_str', 'F?')}({st['A']['door']}) | B: {st['B'].get('floor_str', 'F?')}({st['B']['door']})")
            self.prev_status["A"] = st["A"].copy()
            self.prev_status["B"] = st["B"].copy()

    def sync_to_robot(self, reg, val):
        with self.lock: # ใช้ lock ด้วยเพราะ Robot Client คือตัวเดียวกัน
            try:
                if not self.robot_client.is_open: self.robot_client.open()
                return self.robot_client.write_single_register(reg, val)
            except: return None

    def check_for_target(self):
        try:
            res = self.robot_client.read_holding_registers(Tcp_address.TARGET, 1)
            if res and res[0] > 0:
                target = res[0]
                threading.Thread(target=self.run_mission, args=(target,), daemon=True).start()
        except: pass

    def main_control_loop(self):
        while True:
            try:
                # 1. อ่านข้อมูลจาก Robot (Baseline)
                robot_data = self.robot_client.read_holding_registers(0, 20)
                if not robot_data:
                    time.sleep(1); continue

                f_a_robot = robot_data[Tcp_address.CURRENT_FLOOR_A]
                f_b_robot = robot_data[Tcp_address.CURRENT_FLOOR_B]
                
                # 2. วนลูปเช็ค MPU แต่ละสถานี
                for f_key, station in self.stations.items():
                    addr = station['addr']  # ดึง Mapping มาจาก Station
                    l_type = station.get('lift_id', 'A')
                    current_station_floor = int(''.join(filter(str.isdigit, f_key)))
                    
                    # --- [อ่านข้อมูลที่จำเป็นจาก MPU] ---
                    # เราจะอ่านยกแผง (Bulk Read) ตั้งแต่ Register 0 ถึง 15 เพื่อให้ Index ไม่เพี้ยน
                    # หรืออ่านทีละตัวผ่าน addr.get()
                    mpu_data = self.read_modbus(f_key, 0, 20) # อ่านคลุมทั้งหมด
                    
                    if not mpu_data:
                        station['fail_count'] = station.get('fail_count', 0) + 1
                        if station['fail_count'] >= 5 and station.get('is_active', True):
                            self.log(f"⚠️ Floor {f_key} OFFLINE. Attempting Recovery...")
                            station['is_active'] = False
                            self.restart_lift_service(f_key)
                        continue
                    
                    station['fail_count'] = 0 

                    # --- [ดึงค่าโดยใช้ Key จาก Dynamic Addr] ---
                    # ป้องกัน error โดยใช้ค่า default ถ้าใน json ไม่มี key นั้น
                    val_door = mpu_data[addr.get("door", 4)] 
                    val_hb   = mpu_data[addr.get("heartbeat", 1)]
                    val_ok   = mpu_data[addr.get("lift_status", 2)]

                    # --- [LOGIC การ SYNC] ---
                    if l_type == "A":
                        # Sync Lift A
                        if val_door == 1: # ประตูเปิดจริง
                            self.lifts["A"]["door"] = "OPEN"
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_A, 1)
                            self.sync_to_robot(Tcp_address.CURRENT_FLOOR_A, current_station_floor)
                            self.sync_to_robot(Tcp_address.LIFT_STATUS_A, 1) # Arrived
                        elif f_a_robot == current_station_floor:
                            self.lifts["A"]["door"] = "CLOSED"
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_A, 2)
                            if not self.lifts["A"]["busy"]:
                                self.sync_to_robot(Tcp_address.LIFT_STATUS_A, 0)

                    elif l_type == "B":
                        # Sync Lift B
                        if val_door == 1:
                            self.lifts["B"]["door"] = "OPEN"
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_B, 1)
                            self.sync_to_robot(Tcp_address.CURRENT_FLOOR_B, current_station_floor)
                            self.sync_to_robot(Tcp_address.LIFT_STATUS_B, 1)
                        elif f_b_robot == current_station_floor:
                            self.lifts["B"]["door"] = "CLOSED"
                            self.sync_to_robot(Tcp_address.DOOR_STATUS_B, 2)
                            if not self.lifts["B"]["busy"]:
                                self.sync_to_robot(Tcp_address.LIFT_STATUS_B, 0)

                    # --- [SELF-HEALING (Heartbeat)] ---
                    if val_hb != station.get('last_heartbeat', -1):
                        station['last_heartbeat'] = val_hb
                        station['last_update_time'] = time.time()
                        if not station.get('is_active'):
                            self.log(f"✅ Floor {f_key} is now ACTIVE")
                            station['is_active'] = True
                    else:
                        if time.time() - station.get('last_update_time', 0) > 10.0:
                            if station.get('is_active', True):
                                self.log(f"🚨 Floor {f_key} Heartbeat Frozen! Triggering Restart...")
                                station['is_active'] = False
                                self.restart_lift_service(f_key)

            except Exception as e:
                # self.log(f"Main Loop Error: {e}")
                pass
            
            time.sleep(0.4)

    def mission_scanner_loop(self):
        while True:
            for lid in ["A", "B"]:
                if not self.lifts[lid]["busy"]:
                    reg = Tcp_address.TARGET_A if lid == "A" else Tcp_address.TARGET_B
                    t_val = self.robot_client.read_holding_registers(reg, 1)
                    if t_val and t_val[0] > 0:
                        target = t_val[0]
                        station_key = f"{target}{lid}"

                        if station_key in self.stations:
                            self.lifts[lid]["busy"] = True
                            self.sync_to_robot(reg, 0)
                            threading.Thread(target=self.run_mission, args=(station_key, lid), daemon=True).start()
            time.sleep(0.5)

    def run_mission(self, target, lift_type):
        DIRECT_REG = 8
        if lift_type == 'A':
            CMD_REG = Tcp_address.COMMAND_LIFT_A
            COLOR_REG = Tcp_address.LED_COLOR_A
            BRIGHT_REG = Tcp_address.LED_BRIGHT_A
            DOOR_REG_MPU = 4  # ตำแหน่งในบอร์ด Arduino
            ROBOT_DOOR_REG = Tcp_address.DOOR_STATUS_A
            ROBOT_FLOOR_REG = Tcp_address.CURRENT_FLOOR_A
            ROBOT_READY_REG = Tcp_address.LIFT_STATUS_A
            ROBOT_TARGET_REG = Tcp_address.TARGET_A
        else:
            CMD_REG = Tcp_address.COMMAND_LIFT_B
            COLOR_REG = Tcp_address.LED_COLOR_B
            BRIGHT_REG = Tcp_address.LED_BRIGHT_B
            DOOR_REG_MPU = 14
            ROBOT_DOOR_REG = Tcp_address.DOOR_STATUS_B
            ROBOT_FLOOR_REG = Tcp_address.CURRENT_FLOOR_B
            ROBOT_READY_REG = Tcp_address.LIFT_STATUS_B
            ROBOT_TARGET_REG = Tcp_address.TARGET_B

        target_floor_num = int(''.join(filter(str.isdigit, target)))
        self.log(f"🚀 MISSION START: {lift_type} -> Floor {target_floor_num}")
        mission_status = Sequence.IDLE
        start_pulsing_time = None
        
        try:
            # อ่านชั้นปัจจุบันจาก Robot
            r_pos = self.robot_client.read_holding_registers(ROBOT_FLOOR_REG, 1)
            current_f = r_pos[0] if r_pos else 0
            
            action_cmd = 1 if target_floor_num >= current_f else 2
            led_cmd = 3 if target_floor_num >= current_f else 4 # หรือใช้ LedColor Enum

            while mission_status not in [Sequence.DONE, Sequence.ERROR]:
                match mission_status:
                    case Sequence.IDLE:
                        mission_status = Sequence.MOVING

                    case Sequence.MOVING:
                        self.write_modbus(target, CMD_REG, action_cmd)
                        time.sleep(0.5)
                        self.write_modbus(target, CMD_REG, led_cmd)
                        self.write_modbus(target, BRIGHT_REG, self.bright)
                        self.write_modbus(target, COLOR_REG, LedColor.BLUE)
                        mission_status = Sequence.ARRIVED

                    case Sequence.ARRIVED:
                        r_pos_now = self.robot_client.read_holding_registers(ROBOT_FLOOR_REG, 1)
                        d_stat = self.read_modbus(target, DOOR_REG_MPU)
                        if (r_pos_now and r_pos_now[0] == target_floor_num) and (d_stat and d_stat[0] == 1):
                            self.log(f"✅ {lift_type} Arrived at Floor {target_floor_num}")
                            mission_status = Sequence.PULSING
                        time.sleep(0.5)

                    case Sequence.PULSING:
                        if start_pulsing_time is None: 
                            start_pulsing_time = time.time()

                        # --- [Check Timeout 10 นาที (600 วินาที)] ---
                        elapsed = time.time() - start_pulsing_time
                        if elapsed >= 600: # 10 minutes timeout
                            self.log(f"⏰ {lift_type} PULSING TIMEOUT! (10 mins exceeded)")
                            mission_status = Sequence.ERROR
                            continue

                        res_direct = self.robot_client.read_holding_registers(DIRECT_REG, 1)
                        direct_val = res_direct[0] if res_direct else 0

                        # --- [4. ถ้า Robot ขยับ (1=เข้า, 2=ออก)] ---
                        if direct_val in [1, 2]:
                            # self.log(f"Robot Moving {direct_val} (Lift {lift_type})")
                            # Safety Hold 3 วินาที
                            for _ in range(3):
                                self.write_modbus(target, CMD_REG, action_cmd)
                                self.write_modbus(target, COLOR_REG, LedColor.PINK)
                                time.sleep(1.0)
                            # เคลียร์ Register
                            self.sync_to_robot(ROBOT_TARGET_REG, 0)
                            self.sync_to_robot(DIRECT_REG, 0) 
                            self.log(f"⏳ Waiting for {lift_type} Door to Close...")
                            # Final Door Check (บล็อกจนกว่าจะปิด)
                            while True:
                                door_check = self.read_modbus(target, DOOR_REG_MPU)
                                if door_check and door_check[0] == 2: break
                                time.sleep(1.0)
                            
                            mission_status = Sequence.DONE
                            continue

                        # --- [5. Logic ประคองประตูระหว่างรอหุ่นยนต์] ---
                        door_check = self.read_modbus(target, DOOR_REG_MPU)
                        if door_check:
                            door_val = door_check[0]
                            self.sync_to_robot(ROBOT_DOOR_REG, door_val)
                            
                            if door_val == 1: # ประตูเปิด = ยิง Pulse ประคอง
                                self.write_modbus(target, CMD_REG, action_cmd)
                                self.write_modbus(target, COLOR_REG, LedColor.PINK)
                                time.sleep(1.0)
                            else: # ประตูปิดก่อนหุ่นขยับ = สั่งเปิดใหม่
                                self.log(f"⚠️ {lift_type} Door closed - Re-opening...")
                                self.write_modbus(target, CMD_REG, action_cmd)
                                self.write_modbus(target, COLOR_REG, LedColor.RED)
                                time.sleep(1.5)
                                
            self.log(f"MISSION END: Floor {target}")
        except Exception as e:
            self.log(f"❌ {lift_type} Mission Error: {e}")
            mission_status = Sequence.ERROR
        finally:
            self.cleanup_mission(target, lift_type, ROBOT_TARGET_REG, ROBOT_READY_REG, CMD_REG, COLOR_REG)

    def handle_error_blink(self, floor_id):
        """แจ้งเตือนเมื่อเกิด Error ด้วยไฟกระพริบสีแดง"""
        self.log(f"🚨 Error blinking at Floor {floor_id}")
        for _ in range(5):
            self.write_led(floor_id, LedColor.RED)
            time.sleep(0.3)
            self.write_led(floor_id, LedColor.OFF)
            time.sleep(0.3)

    def cleanup_mission(self, floor_id, lift_type, target_reg, ready_reg, cmd_reg, color_reg):
        try:
            # 1. หยุดคำสั่ง Solenoid และเปลี่ยนไฟเป็นสีเขียว
            self.write_modbus(floor_id, cmd_reg, 0)
            self.write_modbus(floor_id, color_reg, LedColor.GREEN)
            
            # 2. เคลียร์สถานะที่ Robot PLC
            self.sync_to_robot(target_reg, 0)
            self.sync_to_robot(ready_reg, 0)
            self.sync_to_robot(8, 0) # เคลียร์ Direct เผื่อไว้กรณี Error
            
        except Exception as e:
            self.log(f"⚠️ Cleanup error: {e}")

        time.sleep(1.0)
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