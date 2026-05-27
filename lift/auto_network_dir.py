import time
import threading
import requests
from pyModbusTCP.client import ModbusClient
from enum import IntEnum
import json
import subprocess
import socket

# --- State Definition ---
class Sequence(IntEnum):
    IDLE    = 0
    MOVING  = 1
    ARRIVED = 2
    PULSING = 3
    DONE    = 4 
    ERROR   = 5

class LedColor(IntEnum):
    GREEN  = 1; BLUE   = 2; PURPLE = 3; RED    = 4; OFF = 5
    YELLOW = 6; ORANGE = 7; PINK   = 8; WHITE  = 9

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
        
        max_reg = max(self.robot_dir_regs) if self.robot_dir_regs else 38
        self.robot_live_data = [0] * (max_reg + 5)
        self.lift_addrs = {"A": {}, "B": {}}
        self.robot_client = ModbusClient(host=self.robot_ip, port=502, auto_open=True, timeout=1.0)
        self.discover_stations()
        threading.Thread(target=self.main_control_loop, daemon=True).start()
        threading.Thread(target=self.mission_scanner_loop, daemon=True).start()
        self.log(f"Master System Started (Robot: {self.robot_ip})")

    def get_my_subnet(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 1))
            my_ip = s.getsockname()[0] 
            subnet = '.'.join(my_ip.split('.')[:-1]) + '.' 
            return subnet
        except Exception:
            return "192.168.1." 
        finally:
            s.close()

    def load_config(self, path):
        try:
            with open(path, 'r') as f:
                config = json.load(f)
                self.config_lifts = config['lifts']
                self.robot_ip = config['robot_ip']
                self.bright = config['settings']['brightness']
                self.max_timeout = config['settings']['max_timeout']
                self.robot_dir_regs = config['settings'].get('robot_dir_registers', [8, 18, 28, 38])
                
                # --- [เพิ่มการโหลดกลุ่ม Landmark หน้าลิฟต์จาก Config ถ้ามี] ---
                self.liftA_landmarks = config['settings'].get('liftA_landmarks', ["LM14", "LM24", "LM34", "LM44"])
                self.liftB_landmarks = config['settings'].get('liftB_landmarks', ["LM13", "LM23", "LM33", "LM43"])
                self.poi_register = config['settings'].get('poi_register', 100)   # สมมติ Register ที่เก็บค่า POI หุ่นยนต์
                self.lpoi_register = config['settings'].get('lpoi_register', 101) # สมมติ Register ที่เก็บค่า LPOI หุ่นยนต์
        except Exception as e:
            print(f"❌ Load Config Error: {e}")
            exit(1)

    def log(self, msg):
        print(f"{msg}")

    def write_led(self, station_key, color_id, bright=None):
        if station_key not in self.stations: return
        addr = self.stations[station_key]['addr']
        val_bright = bright if bright is not None else self.bright
        
        self.write_modbus(station_key, addr.get("led_bright", 7), val_bright)
        self.write_modbus(station_key, addr.get("led_target", 5), color_id)

    def read_modbus(self, station_key, reg, count=1):
        if station_key not in self.stations: return None
        client = self.stations[station_key]['client']
        with self.lock:
            try:
                if not client.is_open: client.open()
                res = client.read_holding_registers(reg, count)
                if res is None: client.close()
                return res
            except:
                client.close()
                return None

    def write_modbus(self, station_key, reg, val):
        if station_key not in self.stations: return None
        client = self.stations[station_key]['client']
        with self.lock:
            try:
                if not client.is_open: client.open()
                res = client.write_single_register(reg, val)
                if res is None: client.close()
                else: time.sleep(0.02)
                return res
            except:
                client.close()
                return None

    def discover_stations(self):
        base_ip = self.get_my_subnet()
        target_ips = set() 
        for item in self.config_lifts:
            target_ips.add(item['ip'])
            
        for item in self.config_lifts:
            cfg_subnet = '.'.join(item['ip'].split('.')[:-1]) + '.'
            for i in range(1, 255): 
                target_ips.add(f"{cfg_subnet}{i}")

        for i in range(1, 255):
            target_ips.add(f"{base_ip}{i}")
        
        threads = []
        for ip in target_ips:
            t = threading.Thread(target=self.scan_worker, args=(ip,))
            t.daemon = True 
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=0.05) 
            
        for k in sorted(self.stations.keys()):
            self.log(f"Ready: Floor {k} at {self.stations[k]['ip']}")

    def scan_worker(self, ip):
        try:
            r = requests.get(f"http://{ip}:5000/status", timeout=0.5)
            if r.status_code == 200:
                data = r.json()
                f_id = int(data.get("floor"))
                l_id = data.get("lift_id", "A")
                addr_map = data.get("addr", {})
                station_key = f"{f_id}{l_id}"
                
                with self.lock:
                    self.lift_addrs[l_id] = addr_map
                    if l_id not in self.lifts:
                        self.lifts[l_id] = {"floor": -1, "door": "CLOSED", "busy": False}
                    
                    self.stations[station_key] = {
                        'ip': ip, 
                        'client': ModbusClient(host=ip, port=1502, auto_open=True, timeout=0.5),
                        'lift_id': l_id,
                        'addr': addr_map,
                        'last_heartbeat': -1,
                        'last_update_time': time.time(),
                        'fail_count': 0,
                        'is_active': True
                    }
        except:
            pass

    def print_dashboard_on_change(self):
        st = self.lifts
        pr = self.prev_status
        changed = (st["A"]["floor"] != pr["A"]["floor"] or st["A"]["door"] != pr["A"]["door"] or
                   st["B"]["floor"] != pr["B"]["floor"] or st["B"]["door"] != pr["B"]["door"])
        if changed:
            print(f"--- [UPDATE] --- A: F{st['A']['floor']}({st['A']['door']}) | B: F{st['B']['floor']}({st['B']['door']})")
            self.prev_status["A"] = st["A"].copy()
            self.prev_status["B"] = st["B"].copy()

    def sync_to_robot(self, reg, val):
        with self.lock:
            try:
                if not self.robot_client.is_open: self.robot_client.open()
                return self.robot_client.write_single_register(reg, val)
            except: return None

    def decode_landmark_string(self, reg_val):
        """ แปลงค่าเลขจากโมดบัสกลับมาเป็นชื่อ Landmark (ปรับแต่งตามระบบของคุณ) """
        # สมมติว่าใน PLC ส่งมาเป็นตัวเลข เช่น 14 หมายถึง LM14, 1 หมายถึง SM1
        if reg_val == 1: return "SM1"
        if reg_val == 2: return "SM2"
        if reg_val in [14, 24, 34, 44]: return f"LM{reg_val}"
        if reg_val in [13, 23, 33, 43]: return f"LM{reg_val}"
        return ""

    def main_control_loop(self):
        while True:
            try:
                read_count = len(self.robot_live_data)
                robot_data = self.robot_client.read_holding_registers(0, read_count)
                if not robot_data:
                    time.sleep(1); continue
                
                with self.lock:
                    self.robot_live_data = robot_data

                # --- [ส่วนเสริมระบบ: TRAFFIC INTERLOCK & SOLENOID CONTROL] ---
                # อ่านค่า POI และ LPOI จากข้อมูลหุ่นยนต์ (กรณีที่อยู่ในช่วง register ที่กวาดมาแล้ว)
                # ปรับแก้ index ตามความจริงของตำแหน่ง register ใน Robot PLC
                raw_poi = robot_data[self.poi_register] if self.poi_register < len(robot_data) else 0
                raw_lpoi = robot_data[self.lpoi_register] if self.lpoi_register < len(robot_data) else 0
                
                poi = self.decode_landmark_string(raw_poi)
                lpoi = self.decode_landmark_string(raw_lpoi)

                target_state = 0
                active_lift = ""
                station_floor_key = "" # สำหรับอ้างอิงบอร์ดประจำชั้นเพื่อยิงโซเลนอยด์

                # เช็คการเคลื่อนไหวของ Lift A
                if lpoi in self.liftA_landmarks and poi == "SM1":
                    target_state = 1  # กำลังจะเข้าลิฟต์ A
                    active_lift = "A"
                elif lpoi == "SM1" and poi in self.liftA_landmarks:
                    target_state = 2  # กำลังจะออกจากลิฟต์ A
                    active_lift = "A"

                # เช็คการเคลื่อนไหวของ Lift B
                elif lpoi in self.liftB_landmarks and poi == "SM2":
                    target_state = 1  # กำลังจะเข้าลิฟต์ B
                    active_lift = "B"
                elif lpoi == "SM2" and poi in self.liftB_landmarks:
                    target_state = 2  # กำลังจะออกจากลิฟต์ B
                    active_lift = "B"

                # หากตรวจพบว่า AGV กำลังจะเปลี่ยนสถานะเป็น 1 หรือ 2 (ต้องการข้ามโซน)
                if target_state in [1, 2]:
                    current_door = self.lifts[active_lift]["door"]
                    current_floor = self.lifts[active_lift]["floor"]
                    station_floor_key = f"{current_floor}{active_lift}"

                    if current_door == "OPEN":
                        # ประตูเปิดอยู่แล้ว -> ปล่อยให้ระบบทำงานต่อตามปกติ ส่งสัญญาณอนุมัติให้หุ่นยนต์วิ่งได้
                        # (ตัวอย่าง: ส่ง State ไปบอก Robot PLC ใน register ประจำตัว)
                        # self.sync_to_robot(99, target_state) 
                        pass
                    else:
                        # 🚨 ประตูยังปิดอยู่! -> สั่ง interlock บล็อกหุ่นยนต์ไว้ และยิงคำสั่งเปิดประตูผ่าน Solenoid
                        self.log(f"⚠️ AGV tries State {target_state} at Lift {active_lift} but door is CLOSED! Holding Robot & Pulsing Solenoid...")
                        
                        # บล็อกสถานะของหุ่นยนต์ไว้ก่อน ไม่ให้ขยับจนกว่าจะสำเร็จ (ตัวอย่าง: สั่งหุ่นยนต์หยุด หรือค้าง State=0)
                        # self.sync_to_robot(99, 0)

                        if station_floor_key in self.stations:
                            addr = self.stations[station_floor_key]['addr']
                            action_cmd = 1 if target_state == 1 else 2 # สั่งคำสั่งทิศทางตามลักษณะภารกิจ
                            
                            # สั่งการบอร์ดประจำชั้นเพื่อกระตุ้นรีเลย์โซเลนอยด์เปิดประตูลิฟต์
                            self.write_modbus(station_floor_key, addr.get("led_target"), LedColor.ORANGE) # เปลี่ยนไฟเตือน
                            self.write_modbus(station_floor_key, addr.get("command"), action_cmd) # ยิงพัลส์โซเลนอยด์
                # -------------------------------------------------------------

                # 2. Iterate through stations (Logic เดิมของเซิร์ฟเวอร์)
                for f_key, station in self.stations.items():
                    addr = station['addr']
                    l_type = station.get('lift_id', 'A')
                    current_station_floor = int(''.join(filter(str.isdigit, f_key)))
                    
                    mpu_data = self.read_modbus(f_key, 0, 20)
                    
                    if not mpu_data:
                        station['fail_count'] = station.get('fail_count', 0) + 1
                        if station['fail_count'] >= 5 and station.get('is_active', True):
                            self.log(f"⚠️ Floor {f_key} OFFLINE. Attempting Recovery...")
                            station['is_active'] = False
                            self.restart_lift_service(f_key)
                        continue
                    station['fail_count'] = 0 

                    val_door = mpu_data[addr.get("door", 4)] 
                    val_hb   = mpu_data[addr.get("heartbeat", 1)]
                    f_robot  = robot_data[addr.get("floor", 0)]

                    if val_door == 1: 
                        self.lifts[l_type]["door"] = "OPEN"
                        self.lifts[l_type]["floor"] = current_station_floor
                        self.sync_to_robot(addr.get("door"), 1)
                        self.sync_to_robot(addr.get("floor"), current_station_floor)
                        self.sync_to_robot(addr.get("lift_status"), 1)
                    
                    elif f_robot == current_station_floor: 
                        self.lifts[l_type]["door"] = "CLOSED"
                        self.lifts[l_type]["floor"] = current_station_floor
                        self.sync_to_robot(addr.get("door"), 2)
                        if not self.lifts[l_type]["busy"]:
                            self.sync_to_robot(addr.get("lift_status"), 0)

                    if val_hb != station.get('last_heartbeat', -1):
                        station['last_heartbeat'] = val_hb
                        station['last_update_time'] = time.time()
                        if not station.get('is_active'):
                            self.log(f"Floor {f_key} is now ACTIVE")
                            station['is_active'] = True
                    else:
                        if time.time() - station.get('last_update_time', 0) > 10.0:
                            if station.get('is_active', True):
                                self.log(f"🚨 Floor {f_key} Heartbeat Frozen! Triggering Restart...")
                                station['is_active'] = False
                                self.restart_lift_service(f_key)
            except Exception as e:
                pass
            time.sleep(0.4)

    def mission_scanner_loop(self):
        while True:
            for lid in list(self.lifts.keys()):
                if not self.lifts[lid]["busy"] and self.lift_addrs.get(lid):
                    addr = self.lift_addrs[lid]
                    target_reg = addr.get("target", 6 if lid == "A" else 16)
                    
                    t_val = self.robot_client.read_holding_registers(target_reg, 1)
                    if t_val and t_val[0] > 0:
                        target_floor = t_val[0]
                        station_key = f"{target_floor}{lid}"

                        if station_key in self.stations:
                            self.lifts[lid]["busy"] = True
                            self.sync_to_robot(target_reg, 0) 
                            threading.Thread(target=self.run_mission, 
                                             args=(station_key, lid), 
                                             daemon=True).start()
            time.sleep(0.5)

    def run_mission(self, station_key, lift_type):
        station = self.stations.get(station_key)
        if not station:
            self.log(f"❌ Mission aborted: Station {station_key} not found")
            return
            
        addr = station['addr']
        
        # --- ดึงค่าจุดเชื่อมต่อหลักและตั้งค่า Default ให้ชัดเจนก่อนเข้าบล็อก try ---
        CMD_REG    = addr.get("command", 0)
        COLOR_REG  = addr.get("led_target", 5)
        BRIGHT_REG = addr.get("led_bright", 7)
        DOOR_REG   = addr.get("door", 4)
        FLOOR_REG  = addr.get("floor", 0)
        READY_REG  = addr.get("lift_status", 0)
        TARGET_REG = addr.get("target", 6 if lift_type == 'A' else 16)

        target_floor_num = int(''.join(filter(str.isdigit, station_key)))
        self.log(f"MISSION START: {lift_type} -> Floor {target_floor_num}")
        mission_status = Sequence.IDLE
        start_pulsing_time = None
        
        try:
            r_pos = self.robot_client.read_holding_registers(FLOOR_REG, 1)
            current_f = r_pos[0] if r_pos else 0

            all_floors = [int(''.join(filter(str.isdigit, k))) for k in self.stations.keys()]
            lowest_floor = min(all_floors) if all_floors else 1
            highest_floor = max(all_floors) if all_floors else 4
            
            if current_f == lowest_floor:
                action_cmd = 1  
                led_cmd = 3     
                
            elif current_f == highest_floor:
                action_cmd = 2  
                led_cmd = 4     
            else:
                action_cmd = 1 if target_floor_num >= current_f else 2
                led_cmd = 3 if target_floor_num >= current_f else 4


            while mission_status not in [Sequence.DONE, Sequence.ERROR]:
                match mission_status:
                    case Sequence.IDLE:
                        mission_status = Sequence.MOVING

                    case Sequence.MOVING:
                        self.write_modbus(station_key, BRIGHT_REG, self.bright)
                        self.write_modbus(station_key, COLOR_REG, LedColor.BLUE)
                        self.write_modbus(station_key, CMD_REG, action_cmd)
                        time.sleep(0.4)
                        self.write_modbus(station_key, CMD_REG, led_cmd)
                        mission_status = Sequence.ARRIVED

                    case Sequence.ARRIVED:
                        r_pos_now = self.robot_client.read_holding_registers(FLOOR_REG, 1)
                        d_stat = self.read_modbus(station_key, DOOR_REG)
                        if (r_pos_now and r_pos_now[0] == target_floor_num) and (d_stat and d_stat[0] == 1):
                            self.log(f"{lift_type} Arrived at Floor {target_floor_num}")
                            mission_status = Sequence.PULSING
                        time.sleep(0.5)

                    case Sequence.PULSING:
                        if start_pulsing_time is None: 
                            start_pulsing_time = time.time()
                            self.robot_tracks = {reg: False for reg in self.robot_dir_regs}
                        with self.lock:
                            snapshot_data = list(self.robot_live_data)

                        any_robot_active = False  
                        all_cleared = True      
                        active_registers = self.robot_dir_regs

                        for reg in active_registers:
                            current_dir = snapshot_data[reg] if reg < len(snapshot_data) else 0
                            if current_dir in [1, 2]:
                                self.robot_tracks[reg] = True
                                any_robot_active = True   
                            if self.robot_tracks[reg] == True and current_dir != 0:
                                all_cleared = False

                        if not any(self.robot_tracks.values()):
                            all_cleared = False

                        door_check = self.read_modbus(station_key, DOOR_REG)
                        door_val = door_check[0] if door_check else 2
                        self.sync_to_robot(DOOR_REG, 1 if door_val == 1 else 2)

                        if any_robot_active: 
                            self.log(f"goooo!!!!")
                            self.write_modbus(station_key, COLOR_REG, LedColor.PINK)
                            self.write_modbus(station_key, CMD_REG, action_cmd)
                            time.sleep(0.5)
                        elif all_cleared:
                            self.log("✅ Success! All active robots have cleared the area -> [Solenoid STOP]")
                            self.write_modbus(station_key, CMD_REG, 0) 
                            mission_status = Sequence.IDLE             
                            break
                        else:
                            if door_val == 1:
                                self.log(f"⚡ Pulse doorOPEN")
                                self.write_modbus(station_key, COLOR_REG, LedColor.PINK)
                                self.write_modbus(station_key, CMD_REG, 0)
                                time.sleep(1)
                                self.write_modbus(station_key, CMD_REG, action_cmd)
                            else: 
                                self.log(f"!!!!{lift_type} Door CLOSED - Resetting Timer!!!!!")
                                self.write_modbus(station_key, COLOR_REG, LedColor.RED)
                                self.write_modbus(station_key, CMD_REG, 0)
                                time.sleep(1)
                                self.write_modbus(station_key, CMD_REG, action_cmd)
                           
                        if time.time() - start_pulsing_time >= self.max_timeout:
                            self.write_modbus(station_key, CMD_REG, 0) 
                            mission_status = Sequence.ERROR
                            continue
                        time.sleep(0.2)
            
            self.log(f"MISSION END: {station_key}")
        except Exception as e:
            self.log(f"❌ {lift_type} Mission Error: {e}")
            mission_status = Sequence.ERROR
        finally:
            # มั่นใจได้ว่ามีตัวแปรส่งเข้า cleanup_mission เสมอ
            self.cleanup_mission(station_key, lift_type, TARGET_REG, READY_REG, CMD_REG, COLOR_REG)
            
    def cleanup_mission(self, station_key, lift_type, target_reg, ready_reg, cmd_reg, color_reg):
        try:
            self.write_modbus(station_key, cmd_reg, 0)
            self.write_modbus(station_key, color_reg, LedColor.GREEN)
            self.sync_to_robot(target_reg, 0)
            self.sync_to_robot(ready_reg, 0)
        except Exception as e:
            self.log(f"Cleanup error: {e}")

        time.sleep(1.0)
        self.lifts[lift_type]["busy"] = False

    def restart_lift_service(self, station_key):
        if station_key not in self.stations: return
        ip = self.stations[station_key]['ip']
        self.log(f"Self-Healing: Restarting service on {station_key} ({ip})...")
        
        ssh_cmd = f"ssh -o ConnectTimeout=5 arduino@{ip} 'sudo systemctl restart lift-service.service'"
        
        try:
            subprocess.Popen(ssh_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.stations[station_key]['last_update_time'] = time.time() + 15.0 
        except Exception as e:
            self.log(f"❌ SSH Restart Failed: {e}")

if __name__ == '__main__':
    master = MasterSystem()
    while True: time.sleep(1)