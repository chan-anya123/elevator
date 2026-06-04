import time
import threading
import requests
from pyModbusTCP.client import ModbusClient
from enum import IntEnum
import json
import subprocess
import socket
import os
from flask import Flask, jsonify, request, render_template_string

CONFIG_FILE = 'lift_config.json'

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

DEFAULT_CONFIG = {
    "robot_ip": "192.168.10.5",
    "settings": {
        "brightness": 100,
        "max_timeout": 20, 
        "robot_dir_registers": [8, 18, 28, 38]
    },
    "lifts": [
        {"ip": "192.168.20.70", "mac": "14:B5:CD:10:55:63", "reg_offset": 0, "comment": "Lift A1"},
        {"ip": "192.168.20.39", "mac": "14:B5:CD:0A:87:61", "reg_offset": 0, "comment": "Lift A2"},
        {"ip": "192.168.20.42", "mac": "14:B5:CD:E1:47:A7", "reg_offset": 0, "comment": "Lift A3"},
        {"ip": "192.168.20.52", "mac": "14:B5:CD:0F:94:35", "reg_offset": 0, "comment": "Lift A4"},
        {"ip": "192.168.20.66", "mac": "14:B5:CD:0F:65:0F", "reg_offset": 10, "comment": "Lift B1"},
        {"ip": "192.168.20.43", "mac": "14:B5:CD:11:90:51", "reg_offset": 10, "comment": "Lift B2"},
        {"ip": "192.168.20.38", "mac": "14:B5:CD:0F:45:4F", "reg_offset": 10, "comment": "Lift B3"},
        {"ip": "192.168.20.37", "mac": "14:B5:CD:E1:41:EF", "reg_offset": 10, "comment": "Lift B4"}
    ]
}

master_node = None

class MasterSystem:
    def __init__(self, config_path=CONFIG_FILE):
        self.config_path = config_path
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
        
        # โหลดคอนฟิก
        self.load_config(self.config_path)
        
        max_reg = max(self.robot_dir_regs) if self.robot_dir_regs else 38
        self.robot_live_data = [0] * (max_reg + 5)
        self.lift_addrs = {"A": {}, "B": {}}
        self.robot_client = ModbusClient(host=self.robot_ip, port=502, auto_open=True, timeout=1.0)
        
        # เริ่มการสแกนและลูปควบคุม (แยกเป็น Background Threads)
        threading.Thread(target=self.init_and_discover, daemon=True).start()

    def load_config(self, path):
        config = DEFAULT_CONFIG
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    print(">>> Load Local Config Successfully! <<<")
            except Exception as e:
                print(f"!!!!! Local Config File Error: {e} -> Using Default !!!!!")
        else:
            # ถ้าไม่มีไฟล์ ให้สร้างไฟล์เริ่มต้นขึ้นมา
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            except:
                pass

        # กระจายค่าเข้าสู่ตัวแปรระบบ
        self.config_lifts = config['lifts']
        self.robot_ip = config['robot_ip']
        self.bright = config['settings']['brightness']
        self.max_timeout = config['settings']['max_timeout']
        self.robot_dir_regs = config['settings'].get('robot_dir_registers', [8, 18, 28, 38])
        print(f"Config Initialized -> Robot IP: {self.robot_ip}, Total Stations: {len(self.config_lifts)}")

    def init_and_discover(self):
        # รันหลังจากทำโครงสร้างเบื้องต้นเสร็จ เพื่อไม่ให้กวนการเปิด Flask API
        self.discover_stations()
        threading.Thread(target=self.main_control_loop, daemon=True).start()
        threading.Thread(target=self.mission_scanner_loop, daemon=True).start()
        self.log(f"Master System Control Core Started.")

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

    def log(self, msg):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

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

    def sync_to_robot(self, reg, val):
        with self.lock:
            try:
                if not self.robot_client.is_open: self.robot_client.open()
                return self.robot_client.write_single_register(reg, val)
            except: return None

    def main_control_loop(self):
        while True:
            try:
                read_count = len(self.robot_live_data)
                robot_data = self.robot_client.read_holding_registers(0, read_count)
                if not robot_data:
                    time.sleep(1); continue
                
                with self.lock:
                    self.robot_live_data = robot_data

                for f_key, station in self.stations.items():
                    addr = station['addr']
                    l_type = station.get('lift_id', 'A')
                    current_station_floor = int(''.join(filter(str.isdigit, f_key)))
                    
                    mpu_data = self.read_modbus(f_key, 0, 20)
                    
                    if not mpu_data:
                        station['fail_count'] = station.get('fail_count', 0) + 1
                        if station['fail_count'] >= 5 and station.get('is_active', True):
                            self.log(f"!!!!! Floor {f_key} OFFLINE. Attempting Recovery... !!!!!")
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
                                self.log(f"!!!!! Floor {f_key} Heartbeat Frozen! Triggering Restart... !!!!!")
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
        if not station: return
        addr = station['addr']
        
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
                action_cmd = 1; led_cmd = 3     
            elif current_f == highest_floor:
                action_cmd = 2; led_cmd = 4     
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
                            self.log(f"----- {lift_type} Arrived at Floor {target_floor_num} -----")
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
                            time.sleep(1)
                        elif all_cleared:
                            self.log("----- Success -----")
                            self.write_modbus(station_key, CMD_REG, 0) 
                            mission_status = Sequence.IDLE             
                            break
                        else:
                            if door_val == 1:
                                self.log(f"⚡ Pulse doorOPEN")
                                self.write_modbus(station_key, COLOR_REG, LedColor.PINK)
                                self.write_modbus(station_key, CMD_REG, 0)
                                time.sleep(5)
                                self.write_modbus(station_key, CMD_REG, action_cmd)
                            else: 
                                self.log(f"!!!! {lift_type} Door CLOSED - Resetting Timer !!!!!")
                                self.write_modbus(station_key, COLOR_REG, LedColor.RED)
                                self.write_modbus(station_key, CMD_REG, 0)
                                time.sleep(5)
                                self.write_modbus(station_key, CMD_REG, action_cmd)
                           
                        if time.time() - start_pulsing_time >= self.max_timeout:
                            self.write_modbus(station_key, CMD_REG, 0) 
                            mission_status = Sequence.ERROR
                            continue
                        time.sleep(0.2)
            self.log(f"----- MISSION END: {station_key} -----")
        except Exception as e:
            self.log(f"!!!!! {lift_type} Mission Error: {e} !!!!!")
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
        ssh_cmd = f"ssh -o ConnectTimeout=5 arduino@{ip} 'sudo systemctl restart lift-service.service'"
        try:
            subprocess.Popen(ssh_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.stations[station_key]['last_update_time'] = time.time() + 15.0 
        except Exception as e:
            self.log(f"!!!!! SSH Restart Failed: {e} !!!!!")

app = Flask(__name__)
@app.route('/', methods=['GET'])
def index():
    return render_template_string('<script>window.location.href="/admin";</script>')

@app.route('/status', methods=['GET'])
def board_status():
    global master_node
    if master_node:
        return jsonify({
            "status": "online",
            "lifts": master_node.lifts
        })
    return jsonify({"status": "master_starting"}), 503

@app.route('/api/get_lift_config', methods=['GET'])
def get_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify(DEFAULT_CONFIG)

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    global master_node
    msg = ""
    if request.method == 'POST':
        try:
            raw_json = request.form.get('json_data')
            parsed_json = json.loads(raw_json)
            
            if "robot_ip" in parsed_json and "lifts" in parsed_json and "settings" in parsed_json:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                
                if master_node:
                    master_node.load_config(CONFIG_FILE)
                
                msg = '<div style="color: #2ecc71; font-weight: bold; margin-bottom: 15px;">💾 save success!</div>'
            else:
                msg = '<div style="color: #e74c3c; font-weight: bold; margin-bottom: 15px;">❌ save failed: missing required keys in JSON data</div>'
        except Exception as e:
            msg = f'<div style="color: #e74c3c; font-weight: bold; margin-bottom: 15px;">❌ JSON format error: {e}</div>'

    current_data = DEFAULT_CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                current_data = json.load(f)
        except: pass

    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Lift Master Config Dashboard</title>
        <meta charset="utf-8">
        <style>
            body { font-family: 'Segoe UI', sans-serif; background-color: #f5f6fa; margin: 0; padding: 20px; }
            .container { max-width: 900px; background: white; margin: 0 auto; padding: 30px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border-radius: 8px; }
            h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; margin-top: 0; }
            textarea { width: 100%; height: 500px; font-family: 'Courier New', monospace; font-size: 14px; padding: 15px; border-radius: 4px; box-sizing: border-box; background-color: #1e272e; color: #f5f6fa; resize: vertical; }
            .btn { background-color: #3498db; color: white; border: none; padding: 12px 25px; font-size: 16px; border-radius: 4px; cursor: pointer; font-weight: bold; margin-top: 15px; }
            .btn:hover { background-color: #2980b9; }
            p { color: #7f8c8d; font-size: 14px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Lift Master System Config (All-in-One File)</h2>
            {{ msg|safe }}
            <form method="POST">
                <textarea name="json_data">{{ json_string }}</textarea>
                <input type="submit" class="btn" value="save and update">
            </form>
        </div>
    </body>
    </html>
    """
    return render_template_string(
        html_template, 
        json_string=json.dumps(current_data, indent=4, ensure_ascii=False), 
        msg=msg
    )

if __name__ == '__main__':
    master_node = MasterSystem()
    print(">>> Starting Integrated Flask Server on Port 5000... <<<")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)