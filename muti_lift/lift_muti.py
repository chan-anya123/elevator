import time, threading, requests, json, subprocess
from pyModbusTCP.client import ModbusClient
from enum import IntEnum

# --- State & Color Definitions ---
class Sequence(IntEnum):
    IDLE = 0; MOVING = 1; ARRIVED = 2; PULSING = 3; DONE = 4; ERROR = 5

class LedColor(IntEnum):
    GREEN = 1; BLUE = 2; PURPLE = 3; RED = 4; OFF = 5; PINK = 8

class Tcp_address(IntEnum):
    HEARTBEAT       = 1     
    COMMAND_LIFT    = 3     
    LED_COLOR       = 5     
    LED_BRIGHT      = 7     
    DIRECT          = 8    

    CURRENT_FLOOR_A = 0     
    LIFT_STATUS_A   = 2     
    DOOR_STATUS_A   = 4     
    TARGET_A        = 6     

    CURRENT_FLOOR_B = 10    
    LIFT_STATUS_B   = 12    
    DOOR_STATUS_B   = 14    
    TARGET_B        = 16    

class MasterSystem:
    def __init__(self):
        self.config_path = 'lift_config.json'
        self.load_config(self.config_path)
        self.stations = {}
        
        # --- [NEW] Dashboard State ---
        self.current_status = {
            "A": {"floor": 0, "door": "UNKNOWN"},
            "B": {"floor": 0, "door": "UNKNOWN"}
        }
        self.prev_status = {
            "A": {"floor": -1, "door": ""},
            "B": {"floor": -1, "door": ""}
        }
        
        self.is_busy = {"A": False, "B": False}
        self.mission_status = {"A": Sequence.IDLE, "B": Sequence.IDLE}
        self.modbus_lock = threading.Lock()
        self.robot_client = ModbusClient(host=self.robot_ip, port=502, auto_open=True)
        
        self.discover_stations()
        # รัน Monitoring Loop เป็นเบื้องหลัง
        threading.Thread(target=self.global_monitor_loop, daemon=True).start()

    def load_config(self, path):
        try:
            with open(path, 'r') as f:
                config = json.load(f)
                self.config_lifts = config['lifts']
                self.robot_ip = config['robot_ip']
                self.bright = config['settings']['brightness']
                self.max_timeout = config['settings']['max_timeout']
        except Exception as e: print(f"Load Config Error: {e}")

    def log(self, msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def discover_stations(self):
        for item in self.config_lifts:
            ip, expected_mac = item['ip'], item['mac'].lower()
            try:
                r = requests.get(f"http://{ip}:5000/status", timeout=1.5)
                if r.status_code == 200:
                    remote_data = r.json()
                    if remote_data.get("mac", "").lower() == expected_mac:
                        floor = int(remote_data.get("floor", 0))
                        self.stations[floor] = {
                            'ip': ip,
                            'client': ModbusClient(host=ip, port=502, auto_open=True, timeout=2.0),
                            'last_heartbeat': -1, 'last_update_time': time.time(),
                            'is_active': False, 'fail_count': 0 
                        }
                        self.log(f"✅ Floor {floor} mapped to {ip}")
            except: self.log(f"❌ Cannot reach board at {ip}")

    def read_modbus(self, floor_id, reg, count=1):
        if floor_id not in self.stations: return None
        client = self.stations[floor_id]['client']
        with self.modbus_lock:
            try:
                if not client.is_open: client.open()
                res = client.read_holding_registers(reg, count)
                if res is None: client.close()
                return res
            except: return None

    def write_modbus(self, floor_id, reg, val):
        if floor_id not in self.stations: return None
        client = self.stations[floor_id]['client']
        with self.modbus_lock:
            try:
                if not client.is_open: client.open()
                return client.write_single_register(reg, val)
            except: return None

    def write_led(self, floor_id, color_id, bright=None):
        val_bright = bright if bright is not None else self.bright
        self.write_modbus(floor_id, Tcp_address.LED_BRIGHT, val_bright)
        self.write_modbus(floor_id, Tcp_address.LED_COLOR, color_id)

    def global_monitor_loop(self):
        """Monitor Loop: จัดการทั้ง Heartbeat, Robot Sync และ Physical Button"""
        while True:
            for f_num, station in self.stations.items():
                data = self.read_modbus(f_num, 0, 15)
                if data:
                    station['fail_count'] = 0
                    if data[Tcp_address.HEARTBEAT] != station['last_heartbeat']:
                        station['last_heartbeat'] = data[Tcp_address.HEARTBEAT]
                        station['last_update_time'] = time.time()
                        if not station['is_active']: station['is_active'] = True
                    elif time.time() - station['last_update_time'] > 7.0:
                        if station['is_active']:
                            self.log(f"⚠️ Floor {f_num} FROZEN. Restarting...")
                            self.restart_lift_service(f_num)
                    
                    # Sync ข้อมูลและเช็ค Physical Button
                    self.sync_robot_feedback(data, f_num)
                else:
                    station['fail_count'] += 1
                    if station['fail_count'] >= 3 and station['is_active']:
                        station['is_active'] = False
                        self.restart_lift_service(f_num)

            self.print_dashboard_on_change()
            self.check_missions()
            time.sleep(0.4)

    def sync_robot_feedback(self, data, f_num):
        """ data คือข้อมูลที่อ่านมาจาก MPU ประจำชั้น f_num """
        try:
            if not self.robot_client.is_open: self.robot_client.open()
            
            # --- [ลิฟต์ A] ---
            # data[0] คือ CURRENT_FLOOR_A ที่ MPU ส่งมา
            # data[4] คือ DOOR_STATUS_A (1=OPEN, 2=CLOSED)
            if data[4] == 1: # ประตูเปิดที่ชั้นนี้
                self.current_status["A"]["floor"] = f_num
                self.current_status["A"]["door"] = "OPEN"
                # เขียนกลับไปบอก Robot
                self.robot_client.write_single_register(Tcp_address.CURRENT_FLOOR_A, f_num)
                self.robot_client.write_single_register(Tcp_address.DOOR_STATUS_A, 1)
                

            # --- [ลิฟต์ B] ---
            # data[10] คือ CURRENT_FLOOR_B ที่ MPU ส่งมา
            # data[14] คือ DOOR_STATUS_B (1=OPEN, 2=CLOSED)
            if data[14] == 1: # ประตูเปิดที่ชั้นนี้
                self.current_status["B"]["floor"] = f_num
                self.current_status["B"]["door"] = "OPEN"
                # เขียนกลับไปบอก Robot (ที่ Register 10 และ 14)
                self.robot_client.write_single_register(Tcp_address.CURRENT_FLOOR_B, f_num)
                self.robot_client.write_single_register(Tcp_address.DOOR_STATUS_B, 1)
                
            # กรณีประตูปิด (เพื่ออัปเดต Dashboard)
            if data[4] == 2 and self.current_status["A"]["floor"] == f_num:
                self.current_status["A"]["door"] = "CLOSED"
            if data[14] == 2 and self.current_status["B"]["floor"] == f_num:
                self.current_status["B"]["door"] = "CLOSED"

        except Exception as e:
            pass

    def restart_lift_service(self, floor_id):
        ip = self.stations[floor_id]['ip']
        subprocess.Popen(f"ssh arduino@{ip} 'sudo systemctl restart lift-service.service'", 
                         shell=True, stdout=subprocess.DEVNULL)
        self.stations[floor_id]['last_update_time'] = time.time() + 15.0

    def print_dashboard_on_change(self):
        st, pr = self.current_status, self.prev_status
        changed = (st["A"]["floor"] != pr["A"]["floor"] or st["A"]["door"] != pr["A"]["door"] or
                   st["B"]["floor"] != pr["B"]["floor"] or st["B"]["door"] != pr["B"]["door"])
        if changed:
            print(f"--- [UPDATE] --- A: F{st['A']['floor']} ({st['A']['door']}) | B: F{st['B']['floor']} ({st['B']['door']})")
            pr["A"] = st["A"].copy(); pr["B"] = st["B"].copy()

    def check_missions(self):
        for lift in ["A", "B"]:
            if not self.is_busy[lift]:
                reg = Tcp_address.TARGET_A if lift == "A" else Tcp_address.TARGET_B
                try:
                    res = self.robot_client.read_holding_registers(reg, 1)
                    if res and res[0] > 0:
                        self.is_busy[lift] = True
                        threading.Thread(target=self.run_mission, args=(res[0], lift), daemon=True).start()
                except: pass

    def run_mission(self, target, lift_type):
        self.log(f"🚀 {lift_type} MISSION START -> Floor {target}")
        target_reg = Tcp_address.TARGET_A if lift_type == "A" else Tcp_address.TARGET_B
        floor_reg = Tcp_address.CURRENT_FLOOR_A if lift_type == "A" else Tcp_address.CURRENT_FLOOR_B
        d_idx = 4 if lift_type == "A" else 14 

        try:
            cur = self.robot_client.read_holding_registers(floor_reg, 1)
            current_f = cur[0] if cur else 0
            action_cmd = 1 if target >= current_f else 2
            led_cmd = 3 if target >= current_f else 4 
            
            self.mission_status[lift_type] = Sequence.IDLE
            while self.mission_status[lift_type] not in [Sequence.DONE, Sequence.ERROR]:
                if not self.stations[target]['is_active']: break

                match self.mission_status[lift_type]:
                    case Sequence.IDLE: self.mission_status[lift_type] = Sequence.MOVING
                    case Sequence.MOVING:
                        self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                        time.sleep(0.1); self.write_modbus(target, Tcp_address.COMMAND_LIFT, led_cmd)
                        self.write_led(target, LedColor.BLUE)
                        self.mission_status[lift_type] = Sequence.ARRIVED
                    case Sequence.ARRIVED:
                        data = self.read_modbus(target, 0, 15)
                        if data and data[d_idx] == 1: self.mission_status[lift_type] = Sequence.PULSING
                        time.sleep(0.5)
                    case Sequence.PULSING:
                        res_dir = self.robot_client.read_holding_registers(Tcp_address.DIRECT, 1)
                        if res_dir and res_dir[0] in [1, 2]:
                            for c in range(1, 4):
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, LedColor.PINK); time.sleep(1.0)
                            self.write_modbus(target, Tcp_address.COMMAND_LIFT, 0)
                            self.robot_client.write_single_register(target_reg, 0)
                            while True:
                                door_check = self.read_modbus(target, 0, 15)
                                if door_check and door_check[d_idx] == 2: break
                                time.sleep(1.0)
                            self.mission_status[lift_type] = Sequence.DONE
                            continue
                        
                        door_data = self.read_modbus(target, 0, 15)
                        if door_data:
                            if door_data[d_idx] == 1:
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, LedColor.PINK); time.sleep(0.8)
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, 5); time.sleep(0.5)
                            else:
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, LedColor.RED); time.sleep(1.5)
        except Exception as e: self.log(f"❌ {lift_type} Mission Error: {e}")
        finally:
            self.write_modbus(target, Tcp_address.COMMAND_LIFT, 0)
            self.write_led(target, LedColor.GREEN)
            try: self.robot_client.write_single_register(target_reg, 0)
            except: pass
            self.is_busy[lift_type] = False

if __name__ == '__main__':
    master = MasterSystem()
    while True: time.sleep(1)