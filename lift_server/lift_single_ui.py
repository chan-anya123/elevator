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
from datetime import datetime
import platform
from concurrent.futures import ThreadPoolExecutor

CONFIG_FILE = 'lift_config.json'
VERSION = "1.0.0"

DEFAULT_CONFIG = {
    "robot_ip": "192.168.10.5",
    "settings": {
        "brightness": 100,
        "max_timeout": 20, 
        "robot_dir_registers": [8, 18, 28, 38]
    },
    "lifts": [
        {"ip": "192.168.20.70", "reg_offset": 0, "comment": "Lift A1"},
        {"ip": "192.168.20.39", "reg_offset": 0, "comment": "Lift A2"},
        {"ip": "192.168.20.42", "reg_offset": 0, "comment": "Lift A3"},
        {"ip": "192.168.20.52", "reg_offset": 0, "comment": "Lift A4"},
        {"ip": "192.168.20.66", "reg_offset": 10, "comment": "Lift B1"},
        {"ip": "192.168.20.43", "reg_offset": 10, "comment": "Lift B2"},
        {"ip": "192.168.20.38", "reg_offset": 10, "comment": "Lift B3"},
        {"ip": "192.168.20.37", "reg_offset": 10, "comment": "Lift B4"}
    ]
}

master_node = None

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
        
        self.load_config(self.config_path)
        
        max_reg = max(self.robot_dir_regs) if self.robot_dir_regs else 38
        self.robot_live_data = [0] * (max_reg + 5)
        self.lift_addrs = {"A": {}, "B": {}}
        self.robot_client = ModbusClient(host=self.robot_ip, port=502, auto_open=True, timeout=1.0)
        
        threading.Thread(target=self.init_and_discover, daemon=True).start()

    def load_config(self, path):
        config = DEFAULT_CONFIG
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    print("----- Load Local Config Successfully! -----")
            except Exception as e:
                print(f"!!!!! Local Config File Error: {e} -> Using Default !!!!!")
        else:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
            except:
                pass

        self.config_lifts = config['lifts']
        self.robot_ip = config['robot_ip']
        self.bright = config['settings']['brightness']
        self.max_timeout = config['settings']['max_timeout']
        self.robot_dir_regs = config['settings'].get('robot_dir_registers', [8, 18, 28, 38])
        print(f"Config Initialized -> Robot IP: {self.robot_ip}, Total Stations: {len(self.config_lifts)}")

    def init_and_discover(self):
        self.discover_stations()
        threading.Thread(target=self.main_control_loop, daemon=True).start()
        threading.Thread(target=self.mission_scanner_loop, daemon=True).start()
        # --- [เพิ่มระบบสแกนซ้ำเบื้องหลังอัตโนมัติ] ---
        threading.Thread(target=self.rediscover_loop, daemon=True).start()
        
        self.log(f"Master System Control Core Started.")

    def rediscover_loop(self):
        while True:
            time.sleep(300)  # auto-scan every 10 minutes
            self.log("Auto-Scanning for new or reconnected lift stations...")
            self.discover_stations()

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
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            executor.map(self.scan_worker, target_ips)

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
                    # --- [จุดเซฟตี้ใหม่: ป้องกันการโหลดสัญญานซ้ำ] ---
                    if station_key in self.stations:
                        # ถ้าลิฟต์ตัวนี้หลุดไปก่อนหน้านี้ ให้ปลุกกลับมาทำงานต่อได้เลย
                        if not self.stations[station_key]['is_active']:
                            self.log(f"Floor {station_key} reconnected and revived via Auto-Scan.")
                            self.stations[station_key]['is_active'] = True
                            self.stations[station_key]['last_update_time'] = time.time()
                            self.stations[station_key]['fail_count'] = 0
                        return  # ข้ามการสร้าง Modbus Client ใหม่เพื่อความปลอดภัย
                    # --------------------------------------------------
                    
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
                    self.log(f"Ready: Floor {station_key} at {ip}")
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

                for f_key, station in list(self.stations.items()):
                    if not station.get('is_active'): continue  # ข้ามถ้าสถานีนั้นออฟไลน์อยู่
                    
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

                        if station_key in self.stations and self.stations[station_key].get('is_active'):
                            self.lifts[lid]["busy"] = True
                            self.sync_to_robot(target_reg, 0) 
                            threading.Thread(target=self.run_mission, 
                                             args=(station_key, lid), 
                                             daemon=True).start()
            time.sleep(0.5)

    def run_mission(self, station_key, lift_type):
        station = self.stations.get(station_key)
        if not station or not station.get('is_active'): return
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

            all_floors = [int(''.join(filter(str.isdigit, k))) for k, v in self.stations.items() if v.get('is_active')]
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
                if not station.get('is_active'):
                    self.log(f"!!!!! Mission Aborted: Floor {station_key} went offline during execution !!!!!")
                    mission_status = Sequence.ERROR
                    break

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
        
        ssh_cmd = [
            "ssh",
            "-o", "ConnectTimeout=5",
            f"arduino@{ip}",
            "sudo systemctl restart lift-service.service"
        ]
        
        try:
            subprocess.Popen(ssh_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            "version": VERSION,
            "lifts": master_node.lifts
        })
    return jsonify({"status": "master_starting"}), 503

@app.route('/api/get_lift_config', methods=['GET'])
def get_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify(DEFAULT_CONFIG)

@app.route('/api/upload_patch', methods=['POST'])
def upload_patch():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400

    if file and file.filename.endswith('.zip'):
        # บันทึกไฟล์เป็น patch.zip เพื่อให้ update.py ตรวจเจอ
        save_path = os.path.join(os.getcwd(), 'patch.zip')
        file.save(save_path)

        # ฟังก์ชันสั่งรันตัวอัปเดตหลังจากส่ง Response กลับไปแล้ว
        def trigger_update():
            time.sleep(2)
            if platform.system() == "Windows":
                if os.path.exists("update.exe"):
                    subprocess.Popen(["update.exe"])
                else:
                    subprocess.Popen(["python", "update.py"])
            else:
                subprocess.Popen(["python3", "update.py"])

        threading.Thread(target=trigger_update).start()

        return jsonify({
            "status": "success", 
            "message": "Upload successful! The system will restart and update in 2 seconds. Please refresh the page later."
        }), 200

    return jsonify({"status": "error", "message": "Invalid file type. Please upload a .zip file."}), 400

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    global master_node
    msg = ""
    # ... (ส่วนเดิมของ POST logic) ...
    if request.method == 'POST' and 'json_data' in request.form:
        try:
            raw_json = request.form.get('json_data')
            parsed_json = json.loads(raw_json)
            if "robot_ip" in parsed_json and "lifts" in parsed_json and "settings" in parsed_json:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                if master_node:
                    master_node.load_config(CONFIG_FILE)
                msg = '<div style="color: #2ecc71; font-weight: bold; margin-bottom: 15px;">💾 Save success & Hot-Reload applied!</div>'
            else:
                msg = '<div style="color: #e74c3c; font-weight: bold; margin-bottom: 15px;">❌ Save failed: missing keys</div>'
        except Exception as e:
            msg = f'<div style="color: #e74c3c; font-weight: bold; margin-bottom: 15px;">❌ JSON error: {e}</div>'

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
            .section { margin-bottom: 30px; padding: 20px; border: 1px solid #eee; border-radius: 8px; }
            textarea { width: 100%; height: 400px; font-family: 'Courier New', monospace; font-size: 14px; padding: 15px; border-radius: 4px; box-sizing: border-box; background-color: #1e272e; color: #f5f6fa; resize: vertical; }
            .btn { background-color: #3498db; color: white; border: none; padding: 12px 25px; font-size: 16px; border-radius: 4px; cursor: pointer; font-weight: bold; margin-top: 15px; }
            .btn-update { background-color: #e67e22; }
            .btn:hover { opacity: 0.8; }
            p { color: #7f8c8d; font-size: 14px; }
            #upload-status { margin-top: 10px; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Lift Master System Management</h2>

            <div class="section">
                <h3>📦 System Update</h3>
                <p>Upload <b>patch.zip</b> to update the system (Code and Executables).</p>
                <input type="file" id="patch-file" accept=".zip">
                <button class="btn btn-update" onclick="uploadPatch()">Upload and Update</button>
                <div id="upload-status"></div>
            </div>

            <div class="section">
                <h3>⚙️ Configuration</h3>
                <p>Update system-wide configuration (Hot-Reload supported)</p>
                {{ msg|safe }}
                <form method="POST">
                    <textarea name="json_data">{{ json_string }}</textarea>
                    <input type="submit" class="btn" value="Save and Update Config">
                </form>
            </div>
        </div>

        <script>
            function uploadPatch() {
                const fileInput = document.getElementById('patch-file');
                const status = document.getElementById('upload-status');

                if (fileInput.files.length === 0) {
                    alert('Please select a patch.zip file first.');
                    return;
                }

                const formData = new FormData();
                formData.append('file', fileInput.files[0]);

                status.innerHTML = "⏳ Uploading... please wait.";
                status.style.color = "#3498db";

                fetch('/api/upload_patch', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.status === "success") {
                        status.innerHTML = "✅ " + data.message;
                        status.style.color = "#2ecc71";
                        setTimeout(() => { location.reload(); }, 5000);
                    } else {
                        status.innerHTML = "❌ Error: " + data.message;
                        status.style.color = "#e74c3c";
                    }
                })
                .catch(error => {
                    status.innerHTML = "❌ Upload failed: " + error;
                    status.style.color = "#e74c3c";
                });
            }
        </script>
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
    print("----- Starting Server... -----")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
