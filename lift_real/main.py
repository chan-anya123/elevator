from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import time, json, os, threading, socket, subprocess
import msgpack
from pyModbusTCP.server import ModbusServer 
import logging

app = Flask(__name__)
CORS(app)
SOCKET_PATH = "/var/run/arduino-router.sock"
CONFIG_PATH = 'config.json'
HOTSPOT_NAME = "MyLiftHotspot"

# --- Shared State & Locks ---
data_lock = threading.Lock()
rpc_lock = threading.Lock() 
last_led_state = ""
cached_config = {"floor": "1"}
heartbeat_counter = 0
arduino_data = {
    "door": "CLOSED",
    "floor": "1",
    "hb": "0"
}

last_move_time = 0
MOVE_DEBOUNCE_TIME = 1.0  
server = ModbusServer(host="0.0.0.0", port=502, no_block=True)
last_sent_cmd = None
last_led_button_cmd = None  

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 1))
        IP = s.getsockname()[0]
        s.close()
        return IP
    except: return '127.0.0.1'

def get_mac():
    for interface in ['wlan0', 'eth0', 'enp0s']:
        try:
            path = f'/sys/class/net/{interface}/address'
            if os.path.exists(path):
                with open(path, 'r') as f:
                    return f.read().strip().lower()
        except: continue
    return "00:00:00:00:00:00"

def send_rpc(method, params):
    if not isinstance(params, list): params = [params]
    with rpc_lock:
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            client.connect(SOCKET_PATH)
            client.sendall(msgpack.packb([0, 1, method, params], use_bin_type=True))
            response = client.recv(4096)
            if response:
                res = msgpack.unpackb(response)
                return res[3] if isinstance(res, list) and len(res) >= 4 else None
        except (socket.timeout, ConnectionRefusedError, BrokenPipeError) as e:
            # print(f"🔌 RPC Connection Error: {e}")
            return "ERROR"
        except Exception as e:
            return "ERROR"
        finally: 
            try:
                client.close()
            except: pass
    return "ERROR"

def modbus_sync_loop():
    global last_led_state, last_move_time, heartbeat_counter, last_sent_cmd, last_led_button_cmd
    
    while True:
        try:
            # get Address from Config
            addr = cached_config.get("addr", {})
            if not addr:
                time.sleep(1)
                continue

            with data_lock:
                is_ok = arduino_data.get("is_bridge_ok", True)
                door_val = 1 if "OPEN" in arduino_data.get("door", "CLOSED").upper() else 2
                current_floor = int(cached_config.get("floor_name", 1))
                
            # --- Heartbeat ---
            if is_ok:
                heartbeat_counter = (heartbeat_counter + 1) % 65536
            # --- Update Modbus ---
            server.data_bank.set_holding_registers(addr["floor"], [current_floor])
            server.data_bank.set_holding_registers(addr["heartbeat"], [heartbeat_counter])
            server.data_bank.set_holding_registers(addr["lift_status"], [1 if is_ok else 0])
            server.data_bank.set_holding_registers(addr["door"], [door_val])
            # --- WS2812B LED Control---
            target_list = server.data_bank.get_holding_registers(addr["led_target"], 1)
            bright_list = server.data_bank.get_holding_registers(addr["led_bright"], 1)
            if target_list and bright_list:
                color_val = target_list[0]
                bright_val = bright_list[0]
                if bright_val == 0: bright_val = 100 # if sent 0 replace 10 auto
                bright_val = min(max(bright_val, 0), 10) # min 0 max 100 
                color_map = {
                    1: "GREEN", 
                    2: "BLUE", 
                    3: "PURPLE", 
                    4: "RED", 
                    5: "OFF", 
                    6: "YELLOW", 
                    7: "ORANGE", 
                    8: "PINK", 
                    9: "WHITE"
                }
                c_str = color_map.get(color_val)
                if c_str:
                    payload = f"{c_str},{bright_val}"
                    if payload != last_led_state:
                        res = send_rpc("set_led", [payload])
                        if res != "ERROR":
                            last_led_state = payload
                            print(f"🌈 WS2812 ({cached_config.get('lift_id','A')}): {payload}")

            # --- Lift Command Logic---
            cmd_reg = server.data_bank.get_holding_registers(addr["command"], 1)
            if cmd_reg:
                new_cmd = cmd_reg[0]
                if new_cmd != 99:
                    current_time = time.time()
                    # --- solinoid control(1, 2, 5, 0) ---
                    if new_cmd in [1, 2, 5, 0]:
                        if new_cmd != last_sent_cmd: # safty for dubble command
                            if new_cmd in [5, 0] or (current_time - last_move_time) > MOVE_DEBOUNCE_TIME:
                                res = send_rpc("move", [str(new_cmd)])
                                if res != "ERROR":
                                    print(f"📡 Solenoid CMD: {new_cmd}")
                                    last_sent_cmd = new_cmd
                                    if new_cmd in [1, 2]: last_move_time = current_time

                    # --- led bouuton control (3, 4, 6) ---
                    elif new_cmd in [3, 4, 6]:
                        if new_cmd != last_led_button_cmd:
                            res = send_rpc("move", [str(new_cmd)])
                            if res != "ERROR":
                                print(f"💡 Button LED CMD: {new_cmd}")
                                last_led_button_cmd = new_cmd

                    server.data_bank.set_holding_registers(addr["command"], [99])
                    last_sent_cmd = 99
                    last_led_button_cmd = 99
                
        except Exception as e:
            print(f"⚠️ Modbus Sync Error: {e}")
        time.sleep(0.3)

def update_status_loop():
    time.sleep(5) 
    fail_count = 0
    while True:
        try:
            res = send_rpc("status", [""])
            if res and res != "ERROR":
                fail_count = 0
                raw = str(res).upper()
                parts = raw.split('|')
                updates = {}
                for p in parts:
                    if ':' in p:
                        k, v = p.split(':', 1)
                        clean_key = k.strip().replace('A','').lower()
                        updates[clean_key] = v.strip()
                
                with data_lock:
                    arduino_data.update(updates)
                    arduino_data["is_bridge_ok"] = True
            else:
                fail_count += 1
                if fail_count >= 5:
                    print("🚨 Bridge Connection Lost. Triggering Local Recovery...")
                    with data_lock:
                        arduino_data["door"] = "ERROR"
                        arduino_data["is_bridge_ok"] = False
                    # Restart local router service
                    subprocess.Popen("sudo systemctl restart arduino-router.service", shell=True)
                    fail_count = 0
                    time.sleep(10)

        except Exception as e:
            print(f"Update Status Loop Error: {e}")
        time.sleep(0.8)

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html', 
                           floor=cached_config.get('floor_name', '1'), 
                           ip=get_ip())

@app.route('/status')
def status_api():
    return jsonify({
        "arduino": arduino_data, 
        "floor": cached_config.get("floor_name", "1"),
        "lift_id": cached_config.get("lift_id", "A"),
        "addr": cached_config.get("addr", {}),
        "mac": get_mac(),
        "ip": get_ip()
    })

@app.route('/command', methods=['POST'])
def web_command():
    action = request.json.get('action') 
    res = send_rpc("move", [action])
    return jsonify({"result": res})

# --- Network & Config ---
@app.route('/save_config', methods=['POST'])
def save_config():
    new_data = request.get_json()
    if not new_data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    with data_lock:
        if "lift_id" in new_data: cached_config["lift_id"] = new_data["lift_id"]
        if "floor_name" in new_data: cached_config["floor_name"] = new_data["floor_name"]
        if "addr" in new_data:
            if "addr" not in cached_config:
                cached_config["addr"] = {}
            cached_config["addr"].update(new_data["addr"])
        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump(cached_config, f, indent=4)
            print(f"Config Updated: ID={cached_config.get('lift_id')} FL={cached_config.get('floor_name')}")
            return jsonify({"status": "success", "config": cached_config})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/change_wifi', methods=['POST'])
def change_wifi():
    data = request.get_json()
    ssid = data.get('ssid')
    pw = data.get('password')
    if not ssid:
        return jsonify({"status": "error", "message": "SSID missing"}), 400
    NMCLI = "/usr/bin/nmcli"
    cmd = (f"{NMCLI} con delete '{ssid}' > /dev/null 2>&1 || true; "
           f"{NMCLI} con add type wifi con-name '{ssid}' ifname wlan0 ssid '{ssid}' -- "
           f"802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk '{pw}'; "
           f"sleep 2; {NMCLI} con up '{ssid}';")
    subprocess.Popen(cmd, shell=True)
    return jsonify({"status": "switching"})

@app.route('/reset_to_hotspot', methods=['POST'])
def reset_to_hotspot():
    cmd = f"nmcli con up {HOTSPOT_NAME}"
    subprocess.Popen(cmd, shell=True)
    return jsonify({"status": "resetting"})

if __name__ == '__main__':
    logging.getLogger("pyModbusTCP.server").setLevel(logging.WARNING)

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f: 
                cached_config = json.load(f)
        except: pass
    
    default_needed = False
    if "addr" not in cached_config:
        cached_config["addr"] = {
            "floor": 0, "heartbeat": 1, "lift_status": 2, 
            "command": 3, "door": 4, "led_target": 5, "led_bright": 6
        }
        default_needed = True
    if "lift_id" not in cached_config: 
        cached_config["lift_id"] = "A"
        default_needed = True
        
    if default_needed:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(cached_config, f, indent=4)

    # Start Threads
    threading.Thread(target=update_status_loop, daemon=True).start()
    threading.Thread(target=modbus_sync_loop, daemon=True).start()
    try:
        server.start()
        print(f"🚀 MPU System Ready | ID: {cached_config.get('lift_id')} | IP: {get_ip()}")
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nsystem will close...")
    except Exception as e:
        print(f"System Error: {e}")
    finally:
        # print("🧹 Cleaning up resources...")
        server.stop()
        print("Server stopped.")