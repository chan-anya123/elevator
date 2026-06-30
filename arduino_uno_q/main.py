from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import os
import json
import time
import socket
import threading
import subprocess
import logging
import msgpack
from pyModbusTCP.server import ModbusServer
import shlex

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)
CORS(app)

# =========================================================
# CONFIG
# =========================================================
SOCKET_PATH = "/var/run/arduino-router.sock"
CONFIG_PATH = "config.json"
HOTSPOT_NAME = "MyLiftHotspot"

# =========================================================
# GLOBAL STATE
# =========================================================
data_lock = threading.Lock()
rpc_lock = threading.Lock()

cached_config = {
    "floor_name": "1",
    "lift_id": "A",
    "network_mode": "dhcp",
    "static_ip": "",
    "gateway": "",
    "subnet": "24",
    "addr": {}
}

arduino_data = {
    "door": "CLOSED",
    "floor": "1",
    "is_bridge_ok": False
}

heartbeat_counter = 0
last_move_time = 0
MOVE_DEBOUNCE_TIME = 1.0

last_led_state = None
last_solenoid_cmd = None
last_button_led_cmd = None

# =========================================================
# MODBUS SERVER
# =========================================================
server = ModbusServer(host="0.0.0.0", port=1502, no_block=True)

# =========================================================
# UTILITIES
# =========================================================
def get_ip():
    try:
        if cached_config.get("network_mode") == "static":
            return cached_config.get("static_ip", "0.0.0.0")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "0.0.0.0"

def get_mac():
    for interface in ["wlan0", "eth0", "enp0s0"]:
        try:
            path = f"/sys/class/net/{interface}/address"
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read().strip().lower()
        except:
            continue
    return "00:00:00:00:00:00"

# ================= NETWORK APPLY =================
def apply_network_config():
    """
    ฟังก์ชันสำหรับนำค่า Network Config จาก cached_config ไปใช้กับระบบจริงผ่าน nmcli
    """
    mode = cached_config.get("network_mode", "dhcp")
    try:
        active_con = subprocess.check_output("nmcli -t -f NAME,DEVICE connection show --active | grep wlan0 | cut -d: -f1", shell=True).decode().strip()
    except Exception:
        active_con = ""

    # 2. ถ้าไม่เจอ Active ให้ใช้ค่าที่บันทึกไว้ล่าสุด หรือค่า Default
    if not active_con:
        active_con = cached_config.get("current_ssid", "lift_wifi")

    print(f"----- Applying Network Config to Profile: {active_con} (Mode: {mode}) -----")

    try:
        if mode == "dhcp":
            # กรณี DHCP: ต้องเคลียร์ค่า manual เก่าทิ้งให้หมด
            cmd_mod = (
                f"nmcli con mod '{active_con}' "
                f"ipv4.method auto "
                f"ipv4.addresses '' "
                f"ipv4.gateway '' "
                f"ipv4.dns ''"
            )
        else:
            # กรณี Static IP: ดึงค่าจาก config
            ip = cached_config.get("static_ip")
            gw = cached_config.get("gateway")
            subnet = cached_config.get("subnet", "24")
            
            if not ip or not gw:
                print("!!!!! Static IP or Gateway is missing. Skipping apply. !!!!!")
                return

            cmd_mod = (
                f"nmcli con mod '{active_con}' "
                f"ipv4.method manual "
                f"ipv4.addresses {ip}/{subnet} "
                f"ipv4.gateway {gw} "
                f"ipv4.dns '8.8.8.8 8.8.4.4'"
            )

        subprocess.run(cmd_mod, shell=True, check=True)
        print("----- Connection modified successfully. !!!!!")
        subprocess.Popen(f"nmcli con up '{active_con}'", shell=True)
        print(f"-----  Reconnecting to {active_con}... !!!!!")
    except subprocess.CalledProcessError as e:
        print(f"!!!!! Failed to apply network config: {e} !!!!!")
    except Exception as e:
        print(f"!!!!! Unexpected error: {e} !!!!!")

# =========================================================
# RPC CLIENT
# =========================================================
def send_rpc(method, params):
    if not isinstance(params, list):
        params = [params]
    with rpc_lock:
        client = None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            client.connect(SOCKET_PATH)
            payload = [0, 1, method, params]
            client.sendall(msgpack.packb(payload, use_bin_type=True))
            response = client.recv(4096)
            if not response:
                return "ERROR"
            unpacked = msgpack.unpackb(response)
            if isinstance(unpacked, list) and len(unpacked) >= 4:
                return unpacked[3]
            return "ERROR"
        except Exception:
            return "ERROR"
        finally:
            if client:
                try:
                    client.close()
                except:
                    pass

# =========================================================
# MODBUS SYNC THREAD
# =========================================================
def modbus_sync_loop():
    global heartbeat_counter
    global last_move_time
    global last_led_state
    global last_solenoid_cmd
    global last_button_led_cmd

    while True:
        try:
            with data_lock:
                addr = cached_config.get("addr", {})
                floor_name = cached_config.get("floor_name", "1")
                bridge_ok = arduino_data.get("is_bridge_ok", False)
                door_status = arduino_data.get("door", "CLOSED")

            if not addr:
                time.sleep(1)
                continue

            # HEARTBEAT
            if bridge_ok:
                heartbeat_counter = (heartbeat_counter + 1) % 65535
            # DOOR
            door_val = 1 if "OPEN" in door_status.upper() else 2
            # FLOOR
            try:
                floor_val = int(floor_name)
            except:
                floor_val = 1

            # WRITE MODBUS
            server.data_bank.set_holding_registers(addr["floor"], [floor_val])
            server.data_bank.set_holding_registers(addr["heartbeat"], [heartbeat_counter])
            server.data_bank.set_holding_registers(addr["lift_status"], [1 if bridge_ok else 0])
            server.data_bank.set_holding_registers(addr["door"], [door_val])

            # LED CONTROL
            led_target = server.data_bank.get_holding_registers(addr["led_target"], 1)
            led_bright = server.data_bank.get_holding_registers(addr["led_bright"], 1)

            if led_target and led_bright:
                color_val = led_target[0]
                bright_val = min(max(led_bright[0], 0), 100)
                if bright_val == 0:
                    bright_val = 100

                color_map = {
                    1: "GREEN", 2: "BLUE", 3: "PURPLE", 4: "RED",
                    5: "OFF", 6: "YELLOW", 7: "ORANGE",
                    8: "PINK", 9: "WHITE"
                }

                color_name = color_map.get(color_val)
                if color_name:
                    payload = f"{color_name},{bright_val}"
                    if payload != last_led_state:
                        res = send_rpc("set_led", [payload])
                        if res != "ERROR":
                            last_led_state = payload
                            print(f"----- WS2812: {payload} -----")

            # COMMAND CONTROL
            cmd_reg = server.data_bank.get_holding_registers(addr["command"], 1)
            if cmd_reg:
                cmd = cmd_reg[0]
                if cmd != 99:
                    server.data_bank.set_holding_registers(addr["command"], [99])
                    now = time.time()
                    # SOLENOID
                    if cmd in [0, 1, 2, 5]:
                        allow = (cmd in [0, 5]) or ((now - last_move_time) > MOVE_DEBOUNCE_TIME)
                        if allow and cmd != last_solenoid_cmd:
                            res = send_rpc("move", [str(cmd)])
                            if res != "ERROR":
                                print(f"----- Solenoid CMD: {cmd} -----")
                                last_solenoid_cmd = cmd
                                if cmd in [1, 2]:
                                    last_move_time = now
                    # BUTTON LED
                    elif cmd in [3, 4, 6]:
                        if cmd != last_button_led_cmd:
                            res = send_rpc("move", [str(cmd)])
                            if res != "ERROR":
                                print(f"----- Button LED CMD: {cmd} -----")
                                last_button_led_cmd = cmd

        except Exception as e:
            print(f"!!!!! Modbus Sync Error: {e}")
        time.sleep(0.3)

# =========================================================
# STATUS LOOP
# =========================================================
def update_status_loop():
    time.sleep(5)
    fail_count = 0
    while True:
        try:
            res = send_rpc("status", [""])
            if res and res != "ERROR":
                fail_count = 0
                raw = str(res).strip().upper()
                updates = {}
                for p in raw.split("|"):
                    if ":" in p:
                        k, v = p.split(":", 1)
                        updates[k.strip().lower()] = v.strip()
                with data_lock:
                    arduino_data.update(updates)
                    arduino_data["is_bridge_ok"] = True
            else:
                fail_count += 1
                if fail_count >= 5:
                    print("!!!!! Bridge Lost -> Restarting Service !!!!!")
                    with data_lock:
                        arduino_data["door"] = "ERROR"
                        arduino_data["is_bridge_ok"] = False
                    subprocess.Popen( "sudo systemctl restart arduino-router.service", shell=True)
                    fail_count = 0
                    time.sleep(10)

        except Exception as e:
            print(f"Update Loop Error: {e}")
        time.sleep(0.8)

# =========================================================
# LED_ws
# =========================================================
def init_led_state():
    try:
        addr = cached_config.get("addr", {})
        led_target = addr.get("led_target")
        led_bright = addr.get("led_bright")
        if led_target is None or led_bright is None:
            return

        server.data_bank.set_holding_registers(led_target, [2])
        server.data_bank.set_holding_registers(led_bright, [10])

    except Exception as e:
        print(f"LED init error: {e}")

# =========================================================
# FLASK ROUTES
# =========================================================
@app.route("/")
def index():
    return render_template(
        "index.html",
        floor=cached_config.get("floor_name", "1"),
        ip=get_ip()
    )

@app.route("/status")
def status():
    with data_lock:
        return jsonify({
            "ip": get_ip(),
            "mac": get_mac(),
            "lift_id": cached_config["lift_id"],
            "floor": cached_config["floor_name"],
            "network_mode": cached_config["network_mode"],
            "arduino": arduino_data,
            "addr": cached_config.get("addr", {})
        })

@app.route("/command", methods=["POST"])
def web_command():
    action = request.json.get("action")
    res = send_rpc("move", [action])
    return jsonify({"result": res})

@app.route("/save_config", methods=["POST"])
def save_config():
    data = request.json

    with data_lock:
        cached_config["lift_id"] = data.get("lift_id", "A")
        cached_config["floor_name"] = data.get("floor_name", "1")
        cached_config["addr"] = data.get("addr", cached_config.get("addr", {}))

        with open(CONFIG_PATH, "w") as f:
            json.dump(cached_config, f, indent=4)
    return jsonify({"status": "ok"})

@app.route("/set_network_mode", methods=["POST"])
def set_network_mode():
    data = request.json
    with data_lock:
        cached_config["network_mode"] = data.get("mode", "dhcp")
        cached_config["static_ip"] = data.get("static_ip", "")
        cached_config["gateway"] = data.get("gateway", "")
        cached_config["subnet"] = data.get("subnet", "24")
        with open(CONFIG_PATH, "w") as f:
            json.dump(cached_config, f, indent=4)
    apply_network_config()
    return jsonify({"status": "ok"})

@app.route('/change_wifi', methods=['POST'])
def change_wifi():
    data = request.json
    ssid = data.get('ssid')
    pw = data.get('password')

    if not ssid:
        return jsonify({"status": "error", "message": "SSID is required"})
        
    # เข้ารหัสตัวแปรให้ปลอดภัยจาก Command Injection
    safe_ssid = shlex.quote(ssid)
    safe_pw = shlex.quote(pw)
    
    mode = cached_config.get("network_mode", "dhcp")
    cmd = (
        f"nmcli con delete {safe_ssid} > /dev/null 2>&1 || true; "
        f"nmcli con add type wifi con-name {safe_ssid} ifname wlan0 ssid {safe_ssid} "
        f"connection.autoconnect-priority 100 "
        f"802-11-wireless-security.key-mgmt wpa-psk "
        f"802-11-wireless-security.psk {safe_pw}; "
    )
    if mode == "static":
        ip = cached_config.get("static_ip")
        gw = cached_config.get("gateway")
        subnet = cached_config.get("subnet", "24")
        if ip and gw:
            # แก้ไขเป็นแบบนี้ค่ะ
            cmd += f"nmcli con mod {safe_ssid} ipv4.method manual ipv4.addresses {ip}/{subnet} ipv4.gateway {gw} ipv4.dns '8.8.8.8 8.8.4.4'; "

    cmd += f"nmcli con up {safe_ssid}"
    subprocess.Popen(cmd, shell=True)
    return jsonify({"status": "switching", "target": ssid})

@app.route('/reset_to_hotspot', methods=['POST'])
def reset_to_hotspot():
    subprocess.Popen(f"nmcli con up {HOTSPOT_NAME}", shell=True)
    return jsonify({"status": "ok"})

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    logging.getLogger("pyModbusTCP.server").setLevel(logging.WARNING)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cached_config = json.load(f)
        except:
            pass

    if "addr" not in cached_config:
        cached_config["addr"] = {
            "floor": 0,
            "heartbeat": 1,
            "lift_status": 2,
            "command": 3,
            "door": 4,
            "led_target": 5,
            "led_bright": 7
        }

    if "lift_id" not in cached_config:
        cached_config["lift_id"] = "A"

    with open(CONFIG_PATH, "w") as f:
        json.dump(cached_config, f, indent=4)

    init_led_state()
    threading.Thread(target=update_status_loop, daemon=True).start()
    threading.Thread(target=modbus_sync_loop, daemon=True).start()

    try:
        server.start()
        print(f"----- Lift Ready | ID={cached_config.get('lift_id')} | IP={get_ip()} -----")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nSystem stopped")
    finally:
        server.stop()
        print("Modbus server stopped")