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
        
        # Store global address maps per lift type (populated during discovery)
        self.lift_addrs = {"A": {}, "B": {}}

        self.robot_client = ModbusClient(host=self.robot_ip, port=502, auto_open=True, timeout=1.0)
        self.discover_stations()
        
        threading.Thread(target=self.main_control_loop, daemon=True).start()
        threading.Thread(target=self.mission_scanner_loop, daemon=True).start()
        # self.log(f"🚀 Master System Started (Robot: {self.robot_ip})")

    def get_my_subnet(self):
        # ดึง IP ของตัว Server เองออกมา
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # ไม่จำเป็นต้องเชื่อมต่อจริง แค่ใช้หา Outgoing IP
            s.connect(('8.8.8.8', 1))
            my_ip = s.getsockname()[0] # เช่น "192.168.20.45"
            subnet = '.'.join(my_ip.split('.')[:-1]) + '.' # จะได้ "192.168.20."
            return subnet
        except Exception:
            return "192.168.1." # ค่า Default ถ้าหาไม่เจอ
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
        # self.log(f"🔍 Local Network: {base_ip}x")
        
        # 1. รวบรวม IP ทั้งหมดที่น่าจะเป็นไปได้
        # เพิ่ม IP จาก Config (เช่น 20.18) เข้าไปในลิสต์ที่จะสแกน
        target_ips = set() 
        for item in self.config_lifts:
            target_ips.add(item['ip'])
            
        # 2. เพิ่มการสแกนรอบๆ วงที่ Config ระบุ (เผื่อ IP เปลี่ยนเล็กน้อย)
        for item in self.config_lifts:
            cfg_subnet = '.'.join(item['ip'].split('.')[:-1]) + '.'
            for i in range(1, 255): # ถ้าเน็ตช้า ให้ลดช่วงนี้ลงเหลือเฉพาะช่วงที่ใช้จริง
                target_ips.add(f"{cfg_subnet}{i}")

        # 3. เพิ่มการสแกนวงปัจจุบันที่ Server อยู่
        for i in range(1, 255):
            target_ips.add(f"{base_ip}{i}")

        # self.log(f"⚡ Scanning {len(target_ips)} possible addresses...")
        
        threads = []
        for ip in target_ips:
            t = threading.Thread(target=self._scan_worker, args=(ip,))
            t.daemon = True # ใช้ .daemon แทน setDaemon()
            t.start()
            threads.append(t)

        # รอสแกนเสร็จ
        for t in threads:
            t.join(timeout=0.05) # ปรับ timeout การ join ให้ไวขึ้น
            
        # self.log(f"📊 Discovery Finished: Found {len(self.stations)} stations.")
        for k in sorted(self.stations.keys()):
            self.log(f"Ready: Floor {k} at {self.stations[k]['ip']}")

    def _scan_worker(self, ip):
        try:
            # ลด timeout ลงเหลือ 0.5 เพื่อความไวในการสแกนวงกว้าง
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
                    # self.log(f"✅ Found: Floor {f_id}{l_id} at {ip}")
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

    def main_control_loop(self):
        while True:
            try:
                # 1. Read Baseline Data from Robot PLC
                robot_data = self.robot_client.read_holding_registers(0, 20)
                if not robot_data:
                    time.sleep(1); continue
                
                # 2. Iterate through stations
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

                    # Dynamic Register Targeting
                    val_door = mpu_data[addr.get("door", 4)] 
                    val_hb   = mpu_data[addr.get("heartbeat", 1)]
                    f_robot  = robot_data[addr.get("floor", 0)]

                    # --- [LOGIC SYNC: No more hardcoded A vs B logic] ---
                    if val_door == 1: # Real door is OPEN
                        self.lifts[l_type]["door"] = "OPEN"
                        self.lifts[l_type]["floor"] = current_station_floor
                        self.sync_to_robot(addr.get("door"), 1)
                        self.sync_to_robot(addr.get("floor"), current_station_floor)
                        self.sync_to_robot(addr.get("lift_status"), 1)
                    
                    elif f_robot == current_station_floor: # Real door CLOSED
                        self.lifts[l_type]["door"] = "CLOSED"
                        self.lifts[l_type]["floor"] = current_station_floor
                        self.sync_to_robot(addr.get("door"), 2)
                        if not self.lifts[l_type]["busy"]:
                            self.sync_to_robot(addr.get("lift_status"), 0)

                    # --- [SELF-HEALING (Heartbeat)] ---
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
            
            # self.print_dashboard_on_change()
            time.sleep(0.4)

    def mission_scanner_loop(self):
        while True:
            # วนลูปเช็คทุกลิฟต์ที่สแกนเจอ (A, B, ...)
            for lid in list(self.lifts.keys()):
                if not self.lifts[lid]["busy"] and self.lift_addrs.get(lid):
                    addr = self.lift_addrs[lid]
                    
                    # เลือก Register ตามประเภทลิฟต์ (A: 6, B: 16)
                    target_reg = addr.get("target", 6 if lid == "A" else 16)
                    
                    t_val = self.robot_client.read_holding_registers(target_reg, 1)
                    if t_val and t_val[0] > 0:
                        target_floor = t_val[0]
                        station_key = f"{target_floor}{lid}"

                        if station_key in self.stations:
                            self.lifts[lid]["busy"] = True
                            self.sync_to_robot(target_reg, 0) # เคลียร์คำสั่งทันที
                            threading.Thread(target=self.run_mission, 
                                             args=(station_key, lid), 
                                             daemon=True).start()
            time.sleep(0.5)

    def run_mission(self, station_key, lift_type):
        station = self.stations.get(station_key)
        addr = station['addr']
        
        # Dynamically load ALL required registers from the station's 'addr' map
        CMD_REG    = addr.get("command")
        COLOR_REG  = addr.get("led_target")
        BRIGHT_REG = addr.get("led_bright")
        DOOR_REG   = addr.get("door")
        FLOOR_REG  = addr.get("floor")
        READY_REG  = addr.get("lift_status")
        TARGET_REG = addr.get("target", 6 if lift_type == 'A' else 16)

        target_floor_num = int(''.join(filter(str.isdigit, station_key)))
        self.log(f"MISSION START: {lift_type} -> Floor {target_floor_num}")
        mission_status = Sequence.IDLE
        start_pulsing_time = None
        
        try:
            r_pos = self.robot_client.read_holding_registers(FLOOR_REG, 1)
            current_f = r_pos[0] if r_pos else 0
            
            action_cmd = 1 if target_floor_num >= current_f else 2
            led_cmd = 3 if target_floor_num >= current_f else 4

            while mission_status not in [Sequence.DONE, Sequence.ERROR]:
                match mission_status:
                    case Sequence.IDLE:
                        mission_status = Sequence.MOVING

                    case Sequence.MOVING:
                        # 1. เปลี่ยนสีไฟสถานะเป็นรับคำสั่ง
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
                        if start_pulsing_time is None: start_pulsing_time = time.time()

                        if time.time() - start_pulsing_time >= self.max_timeout:
                            mission_status = Sequence.DONE
                            continue

                        door_check = self.read_modbus(station_key, DOOR_REG)
                        if door_check:
                            door_val = door_check[0]
                            self.sync_to_robot(DOOR_REG, 1 if door_val == 1 else 2)
                            
                            if door_val == 1:
                                self.log(f"⚡ Pulse Triggering")
                                self.write_modbus(station_key, COLOR_REG, LedColor.PINK)
                                
                                # --- [เพิ่ม 2 บรรทัดนี้ เพื่อล้างสมองบอร์ดก่อน] ---
                                self.write_modbus(station_key, CMD_REG, 0)
                                time.sleep(10) # รอให้บอร์ดรีเซ็ต last_solenoid_cmd เป็น 0
                                self.write_modbus(station_key, CMD_REG, action_cmd) # ยิง 1 ซ้ำได้แล้ว!
                                time.sleep(1)
                            else: 
                                self.log(f"!!!!{lift_type} Door CLOSED - Resetting Timer!!!!!")
                                self.write_modbus(station_key, COLOR_REG, LedColor.RED)
                                
                                # เคลียร์สมองบอร์ดเช่นกัน กรณีประตูปิดก่อนกำหนด
                                self.write_modbus(station_key, CMD_REG, 0)
                                time.sleep(10)
                                self.write_modbus(station_key, CMD_REG, action_cmd)
                                start_pulsing_time = time.time() 
                                time.sleep(1)
                                
            self.log(f"MISSION END: {station_key}")
        except Exception as e:
            self.log(f"❌ {lift_type} Mission Error: {e}")
            mission_status = Sequence.ERROR
        finally:
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
        
        # แก้ไขชื่อตรงนี้ให้เป็น lift-service.service ตามที่คุณแจ้ง
        ssh_cmd = f"ssh -o ConnectTimeout=5 arduino@{ip} 'sudo systemctl restart lift-service.service'"
        
        try:
            subprocess.Popen(ssh_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # ให้เวลาบอร์ด Reboot service ประมาณ 15 วินาทีก่อนจะเริ่มเช็ค Heartbeat ใหม่
            self.stations[station_key]['last_update_time'] = time.time() + 15.0 
        except Exception as e:
            self.log(f"❌ SSH Restart Failed: {e}")

if __name__ == '__main__':
    master = MasterSystem()
    while True: time.sleep(1)