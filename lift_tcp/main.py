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
from pyModbusTCP.client import ModbusClient
import shlex
from enum import IntEnum
from zeroconf import Zeroconf, ServiceInfo

# =========================================================
# FLASK WEB APP & CONFIG
# =========================================================
app = Flask(__name__)
CORS(app)

SOCKET_PATH = "/var/run/arduino-router.sock"
CONFIG_PATH = "lift_config.json"
HOTSPOT_NAME = "MyLiftHotspot"

# =========================================================
# ENUMS
# =========================================================
class Sequence(IntEnum):
    IDLE    = 0
    MOVING  = 1
    ARRIVED = 2
    PULSING = 3
    DONE    = 4
    ERROR   = 5

class LedColor(IntEnum):
    GREEN, BLUE, PURPLE, RED, OFF, YELLOW, ORANGE, PINK, WHITE = 1, 2, 3, 4, 5, 6, 7, 8, 9

# =========================================================
# GLOBAL STATE
# =========================================================
data_lock = threading.Lock()
rpc_lock = threading.Lock()
wifi_lock = threading.Lock()

cached_config = {}
arduino_data = {"door": "CLOSED", "floor": "1", "is_bridge_ok": False}

heartbeat_counter = 0
last_move_time = 0

last_led_state = None
last_solenoid_cmd = None
last_button_led_cmd = None

server_1502 = None
is_mission_busy = False
current_mission_status = Sequence.IDLE
mission_start_time = None

# =========================================================
# UTILITIES
# =========================================================
def get_default_addr(lift_id="A"):
    is_b = str(lift_id).strip().upper() == "B"
    offset = 10 if is_b else 0
    return {
        "robot_port_502": {
            "floor": 0 + offset,
            "lift_status": 2 + offset,
            "door": 4 + offset,
            "call_target": 6 + offset
        },
        "board_port_1502": {
            "floor": 0 + offset,
            "heartbeat": 1 + offset,
            "lift_status": 2 + offset,
            "command": 3 + offset,
            "door": 4 + offset,
            "led_target": 5 + offset,
            "led_bright": 7 + offset
        }
    }

def get_ip():
    try:
        res = subprocess.run("ip -4 -o addr show scope global", shell=True, capture_output=True, text=True, timeout=1.5)
        lines = [l.strip() for l in res.stdout.strip().split('\n') if l.strip()]
        for l in lines:
            parts = l.split()
            if len(parts) >= 4:
                iface = parts[1]
                ip = parts[3].split('/')[0]
                if iface.startswith(('wl', 'wlan', 'en', 'eth')) and not iface.startswith(('docker', 'br-', 'veth')):
                    return ip
        if lines:
            return lines[0].split()[3].split('/')[0]
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip != "0.0.0.0" and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "0.0.0.0"

def send_rpc(method, params):
    if not isinstance(params, list): params = [params]
    with rpc_lock:
        client = None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            client.connect(SOCKET_PATH)
            client.sendall(msgpack.packb([0, 1, method, params], use_bin_type=True))
            response = client.recv(4096)
            if not response: return "ERROR"
            unpacked = msgpack.unpackb(response)
            if isinstance(unpacked, list) and len(unpacked) >= 4:
                return unpacked[3]
            return "ERROR"
        except Exception:
            return "ERROR"
        finally:
            if client:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    client.close()
                except Exception:
                    pass

def parse_door_is_open(door_status):
    if not door_status: return False
    return any(tok in str(door_status).strip().upper() for tok in ["OPEN", "1", "TRUE", "ON"])

def sync_local_reg(server_inst, reg_addr, val):
    if reg_addr is not None and server_inst is not None:
        try:
            server_inst.data_bank.set_holding_registers(reg_addr, [val])
            server_inst.data_bank.set_input_registers(reg_addr, [val])
        except Exception: pass

def write_robot_reg(robot_client, reg_addr, val):
    if reg_addr is not None and robot_client is not None:
        try:
            if not robot_client.is_open:
                robot_client.open()
            res = robot_client.write_single_register(reg_addr, val)
            if not res:
                robot_client.close()
                if robot_client.open():
                    robot_client.write_single_register(reg_addr, val)
        except Exception:
            try:
                robot_client.close()
            except Exception:
                pass

def get_config_brightness():
    if "settings" in cached_config and isinstance(cached_config["settings"], dict) and "brightness" in cached_config["settings"]:
        return int(cached_config["settings"]["brightness"])
    if "brightness" in cached_config:
        return int(cached_config["brightness"])
    return 100

def set_board_led(server_1502, color_enum, brightness=None):
    global last_led_state
    if server_1502 is None:
        return
    if brightness is None:
        brightness = get_config_brightness()

    addr_1502 = cached_config.get("addr", {}).get("board_port_1502", {})
    color_val = int(color_enum)

    sync_local_reg(server_1502, addr_1502.get("led_target", 15), color_val)
    sync_local_reg(server_1502, addr_1502.get("led_bright", 17), brightness)

    color_map = {1:"GREEN", 2:"BLUE", 3:"PURPLE", 4:"RED", 5:"OFF", 6:"YELLOW", 7:"ORANGE", 8:"PINK", 9:"WHITE"}
    color_name = color_map.get(color_val, 'OFF')
    payload = f"{color_name},{brightness}"
    send_rpc("set_led", [payload])
    last_led_state = payload
    print(f"[LED STATUS] Set LED: {color_name} (Brightness: {brightness}%)")

def set_board_solenoid_and_command(server_1502, cmd_val):
    addr_1502 = cached_config.get("addr", {}).get("board_port_1502", {})
    sync_local_reg(server_1502, addr_1502.get("command", 13), cmd_val)
    send_rpc("move", [str(cmd_val)])
    # print(f"[COMMAND] Executing command: {cmd_val}")

# =========================================================
# MODBUS TELEMETRY SYNC LOOP
# =========================================================
def modbus_sync_loop(robot_client, server_1502):
    global heartbeat_counter, last_move_time, last_led_state, last_solenoid_cmd, last_button_led_cmd

    print("----- Modbus Sync Loop Started (Client->Robot:502, Server->Local:1502) -----")

    last_reported_door_open = None

    while True:
        try:
            with data_lock:
                lift_id = cached_config.get("lift_id", "A")
                offset = 10 if str(lift_id).upper() == "B" else 0
                addr_502 = cached_config.get("addr", {}).get("robot_port_502", {})
                addr_1502 = cached_config.get("addr", {}).get("board_port_1502", {})
                floor_name = cached_config.get("floor_name", "1")
                bridge_ok = arduino_data.get("is_bridge_ok", False)
                door_status = arduino_data.get("door", "CLOSED")
            
            floor_reg_502 = addr_502.get("floor", 0 + offset)
            status_reg_502 = addr_502.get("lift_status", 2 + offset)
            door_reg_502 = addr_502.get("door", 4 + offset)

            floor_reg_1502 = addr_1502.get("floor", 0 + offset)
            heartbeat_reg = addr_1502.get("heartbeat", 1 + offset)
            status_reg_1502 = addr_1502.get("lift_status", 2 + offset)
            cmd_reg = addr_1502.get("command", 3 + offset)
            door_reg_1502 = addr_1502.get("door", 4 + offset)
            led_target_reg = addr_1502.get("led_target", 5 + offset)
            led_bright_reg = addr_1502.get("led_bright", 7 + offset)

            if bridge_ok:
                heartbeat_counter = (heartbeat_counter + 1) % 65535
            sync_local_reg(server_1502, heartbeat_reg, heartbeat_counter)
            sync_local_reg(server_1502, status_reg_1502, 1 if bridge_ok else 0)

            try: floor_val = int(floor_name)
            except Exception: floor_val = 1
            sync_local_reg(server_1502, floor_reg_1502, floor_val)

            is_open = parse_door_is_open(door_status)
            door_val = 1 if is_open else 2

            # อัปเดตข้อมูลเซิร์ฟเวอร์ท้องถิ่น (Port 1502) เสมอ
            sync_local_reg(server_1502, door_reg_1502, door_val)

            # --- ส่งข้อมูลไปยังหุ่นยนต์ (Port 502) เฉพาะเมื่อประตูเปิด หรือเพิ่งปิดที่ชั้นนี้เท่านั้น ---
            if is_open:
                # ประตูเปิดที่ชั้นนี้ -> อัปเดตเลขชั้น, door=1 (OPEN), status=1 (READY/ARRIVED) ไปยังหุ่นยนต์
                write_robot_reg(robot_client, floor_reg_502, floor_val)
                write_robot_reg(robot_client, door_reg_502, 1)
                if not is_mission_busy:
                    write_robot_reg(robot_client, status_reg_502, 1)
                last_reported_door_open = True
            elif last_reported_door_open is True:
                # ประตูเพิ่งปิดที่ชั้นนี้ -> ส่ง door=2 (CLOSED) ไปยังหุ่นยนต์เพียงครั้งเดียว
                write_robot_reg(robot_client, door_reg_502, 2)
                if not is_mission_busy:
                    write_robot_reg(robot_client, status_reg_502, 2)
                last_reported_door_open = False
            # (ถ้าประตูปิดอยู่ และไม่ใช่ชั้นที่เพิ่งปิด จะไม่ส่ง write_robot_reg ไปแย่งเขียน)

            if not is_mission_busy:
                led_target = server_1502.data_bank.get_holding_registers(led_target_reg, 1)
                led_bright = server_1502.data_bank.get_holding_registers(led_bright_reg, 1)

                if led_target and led_bright:
                    color_val = led_target[0]
                    bright_val = max(1, min(led_bright[0], 100))
                    color_map = {1:"GREEN", 2:"BLUE", 3:"PURPLE", 4:"RED", 5:"OFF", 6:"YELLOW", 7:"ORANGE", 8:"PINK", 9:"WHITE"}
                    
                    if color_val in color_map:
                        payload = f"{color_map[color_val]},{bright_val}"
                        if payload != last_led_state:
                            if send_rpc("set_led", [payload]) != "ERROR":
                                last_led_state = payload
                                print(f"[LED STATUS] Modbus updated LED: {color_map[color_val]} (Brightness: {bright_val}%)")

            if not is_mission_busy:
                cmd_data = server_1502.data_bank.get_holding_registers(cmd_reg, 1)
                if cmd_data and cmd_data[0] != 99:
                    cmd = cmd_data[0]
                    sync_local_reg(server_1502, cmd_reg, 99)
                    send_rpc("move", [str(cmd)])
                    # print(f"[COMMAND] Modbus received command: {cmd}")

        except Exception:
            pass
        time.sleep(0.1)

# =========================================================
# MISSION SCANNER & SEQUENCE LOGIC
# =========================================================
def mission_scanner_loop(robot_client, server_1502):
    global is_mission_busy, current_mission_status, mission_start_time

    print("----- Mission Scanner Loop Started (Constantly Checking Target & Auto-Recovering) -----")

    while True:
        try:
            # 1. Ensure robot Modbus TCP client auto-reconnects
            if not robot_client.is_open:
                try:
                    robot_client.open()
                except Exception:
                    pass

            # 2. Watchdog: Auto-recover if mission gets stuck for over 60 seconds
            if is_mission_busy and mission_start_time and (time.time() - mission_start_time > 60):
                print("⚠️ [Watchdog] Mission busy > 60s. Auto-recovering to IDLE state...")
                is_mission_busy = False
                mission_start_time = None
                current_mission_status = Sequence.IDLE
                if arduino_data.get("is_bridge_ok", True):
                    set_board_led(server_1502, LedColor.BLUE)

            # 3. Always check call_target register when not busy
            if not is_mission_busy and robot_client.is_open:
                lift_id = cached_config.get("lift_id", "A")
                offset = 10 if str(lift_id).upper() == "B" else 0
                addr_502 = cached_config.get("addr", {}).get("robot_port_502", {})
                target_reg = addr_502.get("call_target", 6 + offset)
                
                target_regs = robot_client.read_holding_registers(target_reg, 1)
                if target_regs is None:
                    # Read failed, force close client to trigger reconnect on next tick
                    robot_client.close()
                    val_h = 0
                else:
                    val_h = target_regs[0] if len(target_regs) > 0 else 0

                if 0 < val_h <= 99 and val_h != 99:
                    try: current_f = int(cached_config.get("floor_name", "1"))
                    except Exception: current_f = 1

                    # ตอบสนองโดยตรงเฉพาะเมื่อ call_target ตรงกับเลขชั้นของบอร์ดนี้เท่านั้น
                    if val_h == current_f:
                        is_mission_busy = True
                        mission_start_time = time.time()
                        print(f"=================================")
                        print(f"    [TARGET FLOOR] = {val_h}")
                        print(f"=================================")
                        threading.Thread(target=run_board_mission, args=(robot_client, server_1502, val_h), daemon=True).start()
        except Exception as e:
            print(f"[Mission Scanner Error] {e}")
        time.sleep(0.2)


def run_board_mission(robot_client, server_1502, target_floor_num):
    global is_mission_busy, current_mission_status, mission_start_time
    
    mission_start_time = time.time()
    lift_id = cached_config.get("lift_id", "A")
    offset = 10 if str(lift_id).upper() == "B" else 0
    addr_502 = cached_config.get("addr", {}).get("robot_port_502", {})
    addr_1502 = cached_config.get("addr", {}).get("board_port_1502", {})
    settings = cached_config.get("settings", {})
    
    door_reg_502 = addr_502.get("door", 4 + offset)
    door_reg_1502 = addr_1502.get("door", 4 + offset)
    status_reg_502 = addr_502.get("lift_status", 2 + offset)
    target_reg = addr_502.get("call_target", 6 + offset)
    floor_reg_502 = addr_502.get("floor", 0 + offset)

    try: current_f = int(cached_config.get("floor_name", "1"))
    except Exception: current_f = 1

    # --- LIFT DIRECTION LOGIC (UP=1, DOWN=2) ---
    if current_f == 1:
        action_cmd = 1; led_cmd = 3
    elif current_f == 4:
        action_cmd = 2; led_cmd = 4
    else:
        action_cmd = 1 if target_floor_num >= current_f else 2
        led_cmd = 3 if target_floor_num >= current_f else 4

    mission_status = Sequence.IDLE

    try:
        write_robot_reg(robot_client, status_reg_502, 2)
        write_robot_reg(robot_client, door_reg_502, 2)
        sync_local_reg(server_1502, door_reg_1502, 2)

        while mission_status not in [Sequence.DONE, Sequence.ERROR]:
            current_mission_status = mission_status

            if mission_status == Sequence.IDLE:
                mission_status = Sequence.MOVING

            elif mission_status == Sequence.MOVING:
                set_board_led(server_1502, LedColor.GREEN)
                set_board_solenoid_and_command(server_1502, action_cmd)
                time.sleep(0.4)
                set_board_solenoid_and_command(server_1502, 0)
                time.sleep(0.2)
                set_board_solenoid_and_command(server_1502, led_cmd)
                mission_status = Sequence.ARRIVED

            elif mission_status == Sequence.ARRIVED:
                with data_lock: door_stat = arduino_data.get("door", "CLOSED")
                door_val = 1 if parse_door_is_open(door_stat) else 2

                if door_val == 1:
                    print(f"----- Elevator Arrived at Floor {current_f} -----")
                    write_robot_reg(robot_client, door_reg_502, 1)
                    write_robot_reg(robot_client, status_reg_502, 1)
                    write_robot_reg(robot_client, floor_reg_502, current_f)
                    write_robot_reg(robot_client, target_reg, 0)
                    sync_local_reg(server_1502, door_reg_1502, 1)

                    set_board_led(server_1502, LedColor.PINK)
                    mission_status = Sequence.PULSING
                time.sleep(0.3)

            elif mission_status == Sequence.PULSING:
                pulse_count = settings.get("pulse_count", 5)
                print(f"----- Pulsing Phase: {pulse_count} cycles at Floor {current_f} -----")

                for i in range(1, pulse_count + 1):
                    with data_lock: door_stat = arduino_data.get("door", "CLOSED")
                    door_val = 1 if parse_door_is_open(door_stat) else 2

                    write_robot_reg(robot_client, door_reg_502, door_val)
                    write_robot_reg(robot_client, status_reg_502, 1 if door_val == 1 else 2)
                    sync_local_reg(server_1502, door_reg_1502, door_val)

                    if door_val == 1:
                        set_board_led(server_1502, LedColor.PINK)
                    else:
                        set_board_led(server_1502, LedColor.RED)

                    print(f"  [Pulse {i}/{pulse_count}]")
                    set_board_solenoid_and_command(server_1502, action_cmd)
                    time.sleep(1.0)
                    set_board_solenoid_and_command(server_1502, 0)
                    time.sleep(0.5)

                print(f"=======================================")
                print(f"Mission Completed at Floor {current_f}")
                print(f"=======================================")
                mission_status = Sequence.DONE
                break

    except Exception as e:
        print(f"Mission Error: {e}")
        set_board_led(server_1502, LedColor.RED)
        time.sleep(2)
        
    finally:
        set_board_solenoid_and_command(server_1502, 0)
        set_board_solenoid_and_command(server_1502, 6) # Clear indicator
        set_board_solenoid_and_command(server_1502, 5) # Release solenoid
        set_board_led(server_1502, LedColor.BLUE)
        
        write_robot_reg(robot_client, target_reg, 0)       
        write_robot_reg(robot_client, status_reg_502, 2)   
        write_robot_reg(robot_client, door_reg_502, 2)     
        sync_local_reg(server_1502, door_reg_1502, 2)
        
        time.sleep(1.0)
        current_mission_status = Sequence.IDLE
        mission_start_time = None
        is_mission_busy = False

def get_mac():
    for interface in ["wlan0", "wlp45s0", "eth0", "enp0s0", "enx04bf1bb5a5b8"]:
        try:
            path = f"/sys/class/net/{interface}/address"
            if os.path.exists(path):
                with open(path, "r") as f:
                    addr = f.read().strip().lower()
                    if addr and addr != "00:00:00:00:00:00":
                        return addr
        except Exception:
            continue
    try:
        net_dir = "/sys/class/net"
        if os.path.exists(net_dir):
            for iface in os.listdir(net_dir):
                if iface == "lo" or iface.startswith("docker") or iface.startswith("br-") or iface.startswith("veth"):
                    continue
                path = f"{net_dir}/{iface}/address"
                if os.path.exists(path):
                    with open(path, "r") as f:
                        addr = f.read().strip().lower()
                        if addr and addr != "00:00:00:00:00:00":
                            return addr
    except Exception:
        pass
    return "00:00:00:00:00:00"

def get_hostname():
    mac = get_mac().replace(":", "").replace("-", "").strip()
    last4 = mac[-4:].lower() if len(mac) >= 4 else "0000"
    return f"lift{last4}"

def get_hotspot_ssid():
    mac_clean = get_mac().replace(":", "").replace("-", "").strip().upper()
    if not mac_clean or mac_clean == "000000000000":
        return "NextElevator_AP"
    return f"NextElevator_{mac_clean}"

def ensure_hotspot_profile():
    try:
        hotspot_ssid = get_hotspot_ssid()
        safe_ssid = shlex.quote(hotspot_ssid)
        safe_name = shlex.quote(HOTSPOT_NAME)
        res = subprocess.run(f"nmcli -t -f NAME con show | grep -Fx {safe_name}", shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            subprocess.run(f"nmcli con mod {safe_name} 802-11-wireless.ssid {safe_ssid} connection.autoconnect-priority 0 > /dev/null 2>&1", shell=True)
        else:
            cmd = (
                f"nmcli con add type wifi con-name {safe_name} autoconnect no "
                f"ssid {safe_ssid} 802-11-wireless.mode ap 802-11-wireless.band bg "
                f"ipv4.method shared 802-11-wireless-security.key-mgmt wpa-psk "
                f"802-11-wireless-security.psk 12345678 connection.autoconnect-priority 0 > /dev/null 2>&1"
            )
            subprocess.run(cmd, shell=True)
        print(f"[Hotspot Config] Recovery Hotspot configured: SSID='{hotspot_ssid}', Profile='{HOTSPOT_NAME}'")
    except Exception as e:
        print(f"[Hotspot Config Warning] Could not configure hotspot profile: {e}")

def start_mdns(port=5000):
    hostname = get_hostname()
    try:
        subprocess.Popen(f"hostnamectl set-hostname {hostname}", shell=True)
    except Exception:
        pass

    try:
        local_ip = get_ip()
        zeroconf = Zeroconf()
        info = ServiceInfo(
            "_http._tcp.local.",
            f"{hostname}._http._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            properties={"path": "/"},
            server=f"{hostname}.local."
        )
        zeroconf.register_service(info)
        print(f"----- mDNS Service Registered: http://{hostname}.local:{port} ({local_ip}) -----")
        return zeroconf
    except Exception as e:
        print(f"[mDNS Warning] Could not start zeroconf: {e}")
        return None


# =========================================================
# FLASK HTTP REST API & WEB UI ROUTES
# =========================================================
@app.route("/")
def index():
    return render_template("ui.html")

@app.route("/status")
def status():
    with data_lock:
        lift_id = cached_config.get("lift_id", "A")
        defaults = get_default_addr(lift_id)
        
        addr_config = cached_config.get("addr", defaults)
        board_addr = addr_config.get("board_port_1502", defaults["board_port_1502"])
        robot_addr = addr_config.get("robot_port_502", defaults["robot_port_502"])

        combined_addr = dict(board_addr)
        combined_addr["robot_port_502"] = robot_addr
        combined_addr["board_port_1502"] = board_addr

        return jsonify({
            "ip": get_ip(),
            "mac": get_mac(),
            "hostname": get_hostname(),
            "hotspot_ssid": get_hotspot_ssid(),
            "mdns_url": f"http://{get_hostname()}.local:5000",
            "lift_id": lift_id,
            "floor": cached_config.get("floor_name", "1"),
            "robot_ip": cached_config.get("robot_ip", "192.168.20.42"),
            "network_mode": cached_config.get("network_mode", "dhcp"),
            "modbus_port_outside": 502,
            "modbus_port_inside": 1502,
            "modbus_port": 1502,
            "pulse_count": cached_config.get("settings", {}).get("pulse_count", 5),
            "brightness": cached_config.get("settings", {}).get("brightness", 100),
            "settings": cached_config.get("settings", {}),
            "mission_busy": is_mission_busy,
            "mission_state": int(current_mission_status),
            "arduino": arduino_data,
            "addr": combined_addr
        })

@app.route("/command", methods=["POST"])
def web_command():
    data = request.json or {}
    action = data.get("action", "0")
    print(f"[COMMAND] Web API received command: {action}")
    res = send_rpc("move", [str(action)])
    return jsonify({"result": res})

@app.route("/save_config", methods=["POST"])
def save_config():
    global robot_client
    data = request.json or {}

    with data_lock:
        old_lift_id = cached_config.get("lift_id", "A")
        old_robot_ip = cached_config.get("robot_ip", "192.168.20.42")

        new_lift_id = str(data.get("lift_id", old_lift_id)).strip().upper()
        new_robot_ip = str(data.get("robot_ip", old_robot_ip)).strip()

        cached_config["lift_id"] = new_lift_id
        cached_config["floor_name"] = str(data.get("floor_name", cached_config.get("floor_name", "1")))
        cached_config["robot_ip"] = new_robot_ip
        
        if "settings" not in cached_config:
            cached_config["settings"] = {}
        
        if "pulse_count" in data:
            cached_config["settings"]["pulse_count"] = int(data["pulse_count"])
        if "brightness" in data:
            cached_config["settings"]["brightness"] = int(data["brightness"])
        elif "settings" in data and isinstance(data["settings"], dict) and "brightness" in data["settings"]:
            cached_config["settings"]["brightness"] = int(data["settings"]["brightness"])

        defaults = get_default_addr(new_lift_id)

        if "addr" in data and isinstance(data["addr"], dict):
            cached_config["addr"] = data["addr"]
        else:
            if "addr" not in cached_config or not cached_config["addr"]:
                cached_config["addr"] = defaults
            elif old_lift_id != new_lift_id:
                cached_config["addr"]["robot_port_502"] = defaults["robot_port_502"]

        if "robot_port_502" not in cached_config["addr"]:
            cached_config["addr"]["robot_port_502"] = defaults["robot_port_502"]
        if "board_port_1502" not in cached_config["addr"]:
            cached_config["addr"]["board_port_1502"] = defaults["board_port_1502"]

        if new_robot_ip != old_robot_ip and robot_client is not None:
            try:
                robot_client.close()
                robot_client.host = new_robot_ip
            except Exception:
                pass

        with open(CONFIG_PATH, "w") as f:
            json.dump(cached_config, f, indent=4)

    return jsonify({"status": "ok"})

@app.route('/scan_wifi', methods=['GET'])
def scan_wifi():
    try:
        subprocess.run("nmcli device wifi rescan", shell=True, timeout=5)
        res = subprocess.run(
            "nmcli -t -f SSID,SIGNAL,SECURITY,IN-USE device wifi list",
            shell=True, capture_output=True, text=True, timeout=5
        )
        networks = []
        seen = set()
        for line in res.stdout.strip().split('\n'):
            if not line: continue
            parts = line.split(':')
            if len(parts) >= 3:
                ssid = parts[0].strip()
                if ssid and ssid not in seen:
                    seen.add(ssid)
                    networks.append({
                        "ssid": ssid,
                        "signal": int(parts[1]) if parts[1].isdigit() else 0,
                        "security": parts[2],
                        "active": parts[3] == '*' if len(parts) > 3 else False
                    })
        networks.sort(key=lambda x: x["signal"], reverse=True)
        return jsonify({"status": "ok", "networks": networks})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "networks": []})

@app.route("/set_network_mode", methods=["POST"])
def set_network_mode():
    data = request.json or {}
    mode = data.get("mode", "dhcp")
    static_ip = data.get("static_ip", "")
    gateway = data.get("gateway", "")
    subnet = data.get("subnet", "24")

    with data_lock:
        cached_config["network_mode"] = mode
        cached_config["static_ip"] = static_ip
        cached_config["gateway"] = gateway
        cached_config["subnet"] = subnet
        with open(CONFIG_PATH, "w") as f:
            json.dump(cached_config, f, indent=4)

    def apply_net():
        try:
            res = subprocess.run("nmcli -t -f NAME,TYPE connection show --active | grep ':802-11-wireless' | cut -d: -f1", shell=True, capture_output=True, text=True)
            active_con = res.stdout.strip().split('\n')[0] if res.stdout.strip() else ""
            if not active_con or active_con == HOTSPOT_NAME:
                return

            safe_con = shlex.quote(active_con)
            if mode == "static" and static_ip and gateway:
                cmd = f"nmcli con mod {safe_con} ipv4.method manual ipv4.addresses {static_ip}/{subnet} ipv4.gateway {gateway} ipv4.dns '8.8.8.8 8.8.4.4' && nmcli con up {safe_con}"
            else:
                cmd = f"nmcli con mod {safe_con} ipv4.method auto ipv4.addresses '' ipv4.gateway '' ipv4.dns '' && nmcli con up {safe_con}"
            subprocess.run(cmd, shell=True)
        except Exception as e:
            print(f"[Network Apply Error] {e}")

    threading.Thread(target=apply_net, daemon=True).start()
    return jsonify({"status": "ok"})

@app.route('/change_wifi', methods=['POST'])
def change_wifi():
    data = request.json or {}
    ssid = (data.get('ssid') or '').strip()
    pw = data.get('password', '')

    if not ssid:
        return jsonify({"status": "error", "message": "SSID is required"}), 400
        
    def do_connect(target_ssid, target_pw):
        if not wifi_lock.acquire(blocking=False):
            print("[WiFi] Connection already in progress. Ignoring duplicate request.")
            return

        try:
            safe_ssid = shlex.quote(target_ssid)
            hotspot_ssid = get_hotspot_ssid()
            safe_hotspot = shlex.quote(HOTSPOT_NAME)

            print(f"[WiFi] Preparing connection profile for '{target_ssid}'...")

            # 1. Check if profile for target_ssid already exists
            check_prof = subprocess.run(f"nmcli -t -f NAME con show | grep -Fx {safe_ssid}", shell=True, capture_output=True, text=True)
            
            if check_prof.returncode == 0:
                # Profile exists: update security/password directly
                if target_pw:
                    safe_pw = shlex.quote(target_pw)
                    subprocess.run(f"nmcli con mod {safe_ssid} 802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk {safe_pw} > /dev/null 2>&1", shell=True)
                else:
                    subprocess.run(f"nmcli con mod {safe_ssid} 802-11-wireless-security.key-mgmt none > /dev/null 2>&1", shell=True)
            else:
                # Profile does not exist: create it cleanly
                if target_pw:
                    safe_pw = shlex.quote(target_pw)
                    cmd_create = (
                        f"nmcli con add type wifi con-name {safe_ssid} ssid {safe_ssid} "
                        f"802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk {safe_pw} > /dev/null 2>&1"
                    )
                else:
                    cmd_create = f"nmcli con add type wifi con-name {safe_ssid} ssid {safe_ssid} > /dev/null 2>&1"
                subprocess.run(cmd_create, shell=True)

            # 2. Check Static IP mode configuration
            with data_lock:
                net_mode = cached_config.get("network_mode", "dhcp")
                static_ip = cached_config.get("static_ip")
                gateway = cached_config.get("gateway")
                subnet = cached_config.get("subnet", "24")

            if net_mode == "static" and static_ip and gateway:
                subprocess.run(f"nmcli con mod {safe_ssid} ipv4.method manual ipv4.addresses {static_ip}/{subnet} ipv4.gateway {gateway} ipv4.dns '8.8.8.8 8.8.4.4' > /dev/null 2>&1", shell=True)
            else:
                subprocess.run(f"nmcli con mod {safe_ssid} ipv4.method auto ipv4.addresses '' ipv4.gateway '' ipv4.dns '' > /dev/null 2>&1", shell=True)

            # 3. Configure robust reconnection parameters
            subprocess.run(f"nmcli con mod {safe_ssid} connection.autoconnect yes connection.autoconnect-retries 0 802-11-wireless.powersave 2 > /dev/null 2>&1", shell=True)

            # 4. Attempt activation with connection up (give 15s timeout)
            print(f"[WiFi] Activating connection '{target_ssid}'...")
            res = subprocess.run(f"nmcli connection up {safe_ssid}", shell=True, capture_output=True, text=True, timeout=15)

            # If connection up failed, try device wifi connect as secondary attempt (in case BSSID needed scan association)
            if res.returncode != 0:
                print(f"[WiFi Warning] Connection up failed ({res.stderr.strip()}), trying direct wifi connect...")
                if target_pw:
                    cmd_conn = f"nmcli device wifi connect {safe_ssid} password {shlex.quote(target_pw)}"
                else:
                    cmd_conn = f"nmcli device wifi connect {safe_ssid}"
                res = subprocess.run(cmd_conn, shell=True, capture_output=True, text=True, timeout=15)

            if res.returncode == 0:
                print(f"[WiFi Success] Connected to '{target_ssid}' successfully!")

                # Demote all other saved Wi-Fi connections to Priority 0
                try:
                    res_cons = subprocess.run("nmcli -t -f NAME,TYPE connection show | grep ':802-11-wireless' | cut -d: -f1", shell=True, capture_output=True, text=True)
                    for con_name in res_cons.stdout.strip().split('\n'):
                        con_name = con_name.strip()
                        if con_name and con_name != target_ssid and con_name != HOTSPOT_NAME:
                            subprocess.run(f"nmcli con mod {shlex.quote(con_name)} connection.autoconnect-priority 0 > /dev/null 2>&1", shell=True)
                except Exception:
                    pass

                # Set chosen Wi-Fi as exclusive top priority (100)
                subprocess.run(f"nmcli con mod {safe_ssid} connection.autoconnect-priority 100 > /dev/null 2>&1", shell=True)
            else:
                # Connection truly failed: restore Hotspot mode
                print(f"[WiFi Error] Failed to activate '{target_ssid}': {res.stderr.strip()}")
                print(f"[WiFi Fallback] Restoring Hotspot mode ('{hotspot_ssid}')...")
                ensure_hotspot_profile()
                subprocess.run(f"nmcli con up {safe_hotspot}", shell=True)

        except Exception as e:
            print(f"[WiFi Exception] {e}")
            ensure_hotspot_profile()
            subprocess.run(f"nmcli con up {safe_hotspot}", shell=True)
        finally:
            wifi_lock.release()

    threading.Thread(target=do_connect, args=(ssid, pw), daemon=True).start()
    return jsonify({"status": "switching", "target": ssid})

@app.route('/reset_to_hotspot', methods=['POST'])
def reset_to_hotspot():
    safe_hotspot = shlex.quote(HOTSPOT_NAME)
    subprocess.Popen(f"nmcli con up {safe_hotspot}", shell=True)
    return jsonify({"status": "ok"})

@app.route('/reset_bridge', methods=['POST'])
def reset_bridge():
    global server_1502
    print("[RESET] User requested MCU Bridge Reset via Web UI")
    send_rpc("reset", [""])
    time.sleep(0.2)
    res = send_rpc("status", [""])
    is_ok = False
    if res and res != "ERROR":
        updates = {k.strip().lower(): v.strip() for k, v in [p.split(":", 1) for p in str(res).strip().upper().split("|") if ":" in p]}
        with data_lock:
            arduino_data.update(updates)
            arduino_data["is_bridge_ok"] = True
        is_ok = True
    else:
        with data_lock:
            arduino_data["is_bridge_ok"] = False
            arduino_data["door"] = "RETRYING"
    
    if server_1502:
        if is_ok:
            set_board_led(server_1502, LedColor.BLUE)
        else:
            set_board_led(server_1502, LedColor.RED)

    return jsonify({"status": "ok", "bridge_ok": is_ok, "message": "MCU Bridge Reset Executed"})

# =========================================================
# STATUS LOOP
# =========================================================
def update_status_loop(server_1502):
    time.sleep(0.5)
    fail_count = 0
    was_error = False
    while True:
        try:
            res = send_rpc("status", [""])
            if res and res != "ERROR":
                fail_count = 0
                updates = {k.strip().lower(): v.strip() for k, v in [p.split(":", 1) for p in str(res).strip().upper().split("|") if ":" in p]}
                with data_lock:
                    arduino_data.update(updates)
                    arduino_data["is_bridge_ok"] = True

                if was_error:
                    print("===== Bridge Connection Recovered! Restoring Normal State =====")
                    was_error = False
                    if not is_mission_busy:
                        set_board_led(server_1502, LedColor.BLUE)
            else:
                fail_count += 1
                if fail_count >= 10:
                    if not was_error:
                        print("!!!!! Bridge Lost -> Displaying RED !!!!!")
                        was_error = True
                    with data_lock:
                        arduino_data["door"] = "ERROR"
                        arduino_data["is_bridge_ok"] = False
                    set_board_led(server_1502, LedColor.RED)
                    fail_count = 0
                    time.sleep(2)
        except Exception: 
            pass
        time.sleep(0.4)

# =========================================================
# MAIN ENTRY POINT
# =========================================================
class NoStatusFilter(logging.Filter):
    def filter(self, record):
        return '/status' not in record.getMessage()

if __name__ == "__main__":
    logging.getLogger("pyModbusTCP.server").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").addFilter(NoStatusFilter())

    try:
        with open("lift_config.json", "r") as f:
            cached_config = json.load(f)
    except Exception:
        cached_config = {}

    lift_id = str(cached_config.get("lift_id", "A")).strip().upper()
    cached_config["lift_id"] = lift_id
    defaults = get_default_addr(lift_id)

    if "addr" not in cached_config or not isinstance(cached_config["addr"], dict):
        cached_config["addr"] = defaults
    else:
        if "robot_port_502" not in cached_config["addr"]:
            cached_config["addr"]["robot_port_502"] = defaults["robot_port_502"]
        if "board_port_1502" not in cached_config["addr"]:
            cached_config["addr"]["board_port_1502"] = defaults["board_port_1502"]

    # Auto-correct default offset if it does not match lift_id
    if lift_id == "B" and cached_config["addr"]["robot_port_502"].get("floor") == 0:
        cached_config["addr"] = defaults
    elif lift_id == "A" and cached_config["addr"]["robot_port_502"].get("floor") == 10:
        cached_config["addr"] = defaults

    with open("lift_config.json", "w") as f:
        json.dump(cached_config, f, indent=4)

    robot_ip = cached_config.get("robot_ip", "192.168.20.42")
    robot_client = ModbusClient(host=robot_ip, port=502, auto_open=True, timeout=2.0)
    print(f"----- Modbus TCP CLIENT [ROBOT] Connecting to {robot_ip}:502 -----")

    try:
        server_1502 = ModbusServer(host="0.0.0.0", port=1502, no_block=True)
        server_1502.start()
        print("----- Modbus TCP SERVER [LOCAL] Started on Port 1502 -----")
    except Exception as e:
        print(f"Failed to start Port 1502: {e}")

    set_board_led(server_1502, LedColor.BLUE)

    threading.Thread(target=update_status_loop, args=(server_1502,), daemon=True).start()
    threading.Thread(target=modbus_sync_loop, args=(robot_client, server_1502), daemon=True).start()
    threading.Thread(target=mission_scanner_loop, args=(robot_client, server_1502), daemon=True).start()

    ensure_hotspot_profile()
    mdns_obj = start_mdns(5000)

    try:
        print(f"\n=== SYSTEM READY: Bridge Architecture (Client 502 -> Robot | Server 1502 -> Local) ===")
        print(f"=== mDNS URL: http://{get_hostname()}.local:5000 ===")
        print(f"=== Hotspot Recovery SSID: {get_hotspot_ssid()} ===")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        if mdns_obj:
            try: mdns_obj.close()
            except Exception: pass
        server_1502.stop()