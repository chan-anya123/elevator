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
    CURRENT_FLOOR   = 0     # 1, 2, ..         
    HEARTBEAT       = 1     # Pulse count (0-65535)
    LIFT_STATUS     = 2     # Ready Status (1: Ready, 0: Not Ready)
    COMMAND_LIFT    = 3     # 1 (Up), 2 (Down), 0 (Stop)      
    DOOR_STATUS     = 4     # (1: OPEN, 2: CLOSED)
    LED_COLOR       = 5     # 1=G, 2=B, 3=P, 4=R, 5=S
    TARGET          = 6     # 1, 2, ..
    LED_BRIGHT      = 7     # 1-100

class MasterSystem:
    def __init__(self):
        self.config_path = 'lift_config.json'
        self.load_config(self.config_path)
        self.stations = {}
        self.lift_current_floor = -1 
        self.lift_door_status = -1            
        self.is_busy = False
        self.mission_status = Sequence.IDLE
        self.modbus_lock = threading.Lock()
        self.robot_client = ModbusClient(host=self.robot_ip, port=502, auto_open=True)
        self.discover_stations()
        self.initial_sync()
        threading.Thread(target=self.global_monitor_loop, daemon=True).start()
        # threading.Thread(target=self.idle_led_animation, daemon=True).start()

    def load_config(self, path):
        try:
            with open(path, 'r') as f:
                config = json.load(f)
                self.config_lifts = config['lifts']
                self.robot_ip = config['robot_ip']
                self.bright = config['settings']['brightness']
                self.max_timeout = config['settings']['max_timeout']
                # self.log(f"Config loaded: {len(self.config_lifts)} floors.")
        except Exception as e:
            self.log(f"Load Config Error: {e}")

    def log(self, msg):
        print(f"{msg}")

    def discover_stations(self):
        # self.log("Scanning and Mapping floors from boards...")
        for item in self.config_lifts:
            ip = item['ip']
            expected_mac = item['mac'].lower()
            try:
                r = requests.get(f"http://{ip}:5000/status", timeout=1.5)
                if r.status_code == 200:
                    remote_data = r.json()
                    actual_mac = remote_data.get("mac", "").lower()
                    assigned_floor = int(remote_data.get("floor", 0))
                    
                    if actual_mac == expected_mac:
                        self.stations[assigned_floor] = {
                            'ip': ip,
                            'client': ModbusClient(host=ip, port=502, auto_open=True, timeout=2.0),
                            'last_heartbeat': -1,
                            'last_update_time': time.time(),
                            'is_active': False,
                            'fail_count': 0 
                        }
                        self.log(f"Floor {assigned_floor} mapped to {ip}")
            except:
                self.log(f"Cannot reach board at {ip}")

    # --- [Thread-Safe Modbus Methods] ---
    def read_modbus(self, floor_id, reg, count=1):
        if floor_id not in self.stations: return None
        station = self.stations[floor_id]
        client = station['client']
        
        with self.modbus_lock:
            try:
                # ถ้าหลุด ให้พยายามเปิดใหม่แค่ครั้งเดียว ไม่ต้องวนลูปถี่ๆ
                if not client.is_open:
                    client.open()
                
                res = client.read_holding_registers(reg, count)
                return res
            except Exception as e:
                # self.log(f"Modbus Read Error at Floor {floor_id}: {e}")
                return None

    def write_modbus(self, floor_id, reg, val):
        if floor_id not in self.stations: return None
        station = self.stations[floor_id]
        client = station['client']
        
        with self.modbus_lock:
            try:
                if not client.is_open:
                    client.open()
                    
                res = client.write_single_register(reg, val)
                # เพิ่ม Delay นิดหน่อยหลังเขียนเพื่อความเสถียรของบอร์ด
                time.sleep(0.05) 
                return res
            except Exception as e:
                # self.log(f"Modbus Write Error at Floor {floor_id}: {e}")
                return None

    def write_led(self, floor_id, color_id, bright=None):
        val_bright = bright if bright is not None else self.bright
        self.write_modbus(floor_id, Tcp_address.LED_BRIGHT, val_bright)
        self.write_modbus(floor_id, Tcp_address.LED_COLOR, color_id)

    def sync_to_robot(self, floor, door):
        try:
            if not self.robot_client.is_open: 
                self.robot_client.open()
            
            self.robot_client.write_single_register(Tcp_address.CURRENT_FLOOR, floor)
            self.robot_client.write_single_register(Tcp_address.DOOR_STATUS, door)
            ready_val = 1 if door == 1 else 0
            self.robot_client.write_single_register(Tcp_address.LIFT_STATUS, ready_val)
            
            # self.log(f"🤖 Robot Updated: Floor {floor}, Door {door}")
        except Exception as e:
            self.log(f"❌ Sync to Robot Failed: {e}")

    def initial_sync(self):
        for f_num in self.stations:
            data = self.read_modbus(f_num, 0, 5)
            if data:
                self.lift_current_floor = data[0]
                self.lift_door_status = data[4]
                self.sync_to_robot(self.lift_current_floor, self.lift_door_status)
                break

    def global_monitor_loop(self):
        last_sent_floor, last_sent_door = None, None
        while True:
            if not self.stations:
                time.sleep(1)
                continue
            for f_num in list(self.stations.keys()):
                station = self.stations[f_num]              
                # Try to read: CurrentFloor, Heartbeat, LiftStatus, Command, DoorStatus, LedColor
                data = self.read_modbus(f_num, 0, 6)
                if data:
                    station['fail_count'] = 0
                    
                    # --- HEARTBEAT CHECK ---
                    current_hb = data[1]
                    if current_hb != station['last_heartbeat']:
                        station['last_heartbeat'] = current_hb
                        station['last_update_time'] = time.time()
                        
                        if not station['is_active']:
                            self.log(f"Floor {f_num} is now ACTIVE")
                            station['is_active'] = True
                    else:
                        # Heartbeat is STUCK. Check how long.
                        # If stuck > 7 seconds, we consider it "Frozen"
                        if time.time() - station['last_update_time'] > 1.0:
                            if station['is_active']:
                                self.log(f"ALERT: Floor {f_num} HEARTBEAT FROZEN. Triggering Restart...")
                                station['is_active'] = False
                                self.restart_lift_service(f_num)

                    # --- REAL-TIME DATA UPDATE ---
                    # Update global lift status based on sensor data from the boards
                    if data[4] == 1: # If any door is OPEN, update the master's known position
                        self.lift_current_floor = data[0]
                        self.lift_door_status = 1
                    elif f_num == self.lift_current_floor:
                        self.lift_door_status = data[4]

                else:
                    # --- CONNECTION LOST (Modbus Timeout) ---
                    station['fail_count'] += 1
                    
                    # If we fail 3 times in a row (~1.5 to 2 seconds of silence)
                    if station['fail_count'] >= 2:
                        if station['is_active']:
                            self.log(f"⚠️ Floor {f_num} OFFLINE (Connection Lost). Attempting recovery...")
                            station['is_active'] = False
                            self.restart_lift_service(f_num)

            # --- SYNC TO ROBOT ---
            # Only send update to Robot if values actually changed to save bandwidth
            if (self.lift_current_floor != last_sent_floor or self.lift_door_status != last_sent_door):
                self.sync_to_robot(self.lift_current_floor, self.lift_door_status)
                last_sent_floor, last_sent_door = self.lift_current_floor, self.lift_door_status

            # --- MISSION TRIGGER ---
            # If not busy, check if the Robot has written a new Target to the PLC registers
            if not self.is_busy:
                self.check_for_target()

            # Loop frequency (adjust based on how responsive you want the UI/Robot to be)
            time.sleep(0.5)

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

    def check_for_target(self):
        try:
            res = self.robot_client.read_holding_registers(Tcp_address.TARGET, 1)
            if res and res[0] > 0:
                target = res[0]
                threading.Thread(target=self.run_mission, args=(target,), daemon=True).start()
        except: pass

    def run_mission(self, target):
        self.is_busy = True
        
        # เช็คความพร้อมของชั้นเป้าหมาย
        if target not in self.stations or not self.stations[target]['is_active']:
            self.log(f"Mission Aborted: Floor {target} is NOT READY")
            self.is_busy = False
            return

        self.mission_status = Sequence.IDLE
        self.log(f"MISSION START -> Target Floor: {target}")
        
        try:
            start_pulsing_time = None
            action_cmd = 1 if target >= self.lift_current_floor else 2
            led_cmd = 3 if target >= self.lift_current_floor else 4

            while self.mission_status not in [Sequence.DONE, Sequence.ERROR]:
                # ระหว่างรัน Mission ถ้า MPU ตาย ให้หยุดทันที
                if not self.stations[target]['is_active']:
                    self.log(f"🚨 MISSION INTERRUPTED: Floor {target} went DEAD!")
                    self.sync_to_robot(self.lift_current_floor, 2)
                    self.mission_status = Sequence.ERROR
                    break

                match self.mission_status:
                    case Sequence.IDLE:
                        self.mission_status = Sequence.MOVING

                    case Sequence.MOVING:
                        self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                        time.sleep(0.3)
                        self.write_modbus(target, Tcp_address.COMMAND_LIFT, led_cmd)
                        self.write_led(target, LedColor.BLUE)
                        self.mission_status = Sequence.ARRIVED 

                    case Sequence.ARRIVED:
                        data = self.read_modbus(target, 0, 5)
                        if data and data[0] == target and data[4] == 1:
                            self.mission_status = Sequence.PULSING
                        time.sleep(0.3)

                    case Sequence.PULSING:
                        
                        # 1. กำหนดเวลาเริ่มต้นเพื่อทำ Timeout ป้องกันลิฟต์ค้าง
                        if start_pulsing_time is None: 
                            start_pulsing_time = time.time()
                        
                        elapsed = time.time() - start_pulsing_time
                        if elapsed >= self.max_timeout:
                            self.log(f"Pulse Timeout on Floor {target}")
                            self.mission_status = Sequence.DONE
                            continue

                        # 2. อ่านสถานะประตูจาก MPU
                        door_check = self.read_modbus(target, Tcp_address.DOOR_STATUS, 1)
                        if door_check:
                            self.lift_door_status = door_check[0]
                            self.sync_to_robot(self.lift_current_floor, self.lift_door_status)
                            
                            # --- กรณีที่ 1: ประตูเปิดอยู่ (ทำงานตามปกติ) ---
                            if self.lift_door_status == 1:
                                self.log(f"⚡ Pulse Triggering")
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, LedColor.PINK)
                                # time.sleep(0.5) 
                                # self.write_modbus(target, Tcp_address.COMMAND_LIFT, 5)
                                time.sleep(1.0)

                            # --- กรณีที่ 2: ประตูเผลอปิด (ลิฟต์อาจจะยังมาไม่ถึงหรือมีคนดึงปิด) ---
                            else:
                                self.log(f"⚠️ Door CLOSED - Holding Solenoid & Red LED")
                                self.write_modbus(target, Tcp_address.COMMAND_LIFT, action_cmd)
                                self.write_led(target, LedColor.RED)
                                start_pulsing_time = time.time() 
                                time.sleep(1.5)

            self.log(f"MISSION END: Floor {target}")
        except Exception as e:
            self.log(f"Mission Error: {e}")
            self.mission_status = Sequence.ERROR
        finally:
            self.cleanup_mission(target)

    def cleanup_mission(self, floor_id):
        self.write_modbus(floor_id, Tcp_address.COMMAND_LIFT, 0)
        self.write_led(floor_id, LedColor.GREEN)
        self.robot_client.write_single_register(Tcp_address.TARGET, 0)
        time.sleep(0.5)
        self.is_busy = False

    def idle_led_animation(self):
        idle_colors = [LedColor.ORANGE, LedColor.BLUE, LedColor.GREEN]
        color_index = 0
        while True:
            try: 
                if not self.is_busy and self.lift_current_floor in self.stations:
                    target_f = self.lift_current_floor
                    if self.stations[target_f]['is_active']:
                        current_color = idle_colors[color_index]
                        self.write_led(target_f, current_color)
                        color_index = (color_index + 1) % len(idle_colors)
            except Exception as e:
                self.log(f"Idle Animation Thread Error: {e}")

            time.sleep(5.0)

if __name__ == '__main__':
    master = MasterSystem()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Master...")