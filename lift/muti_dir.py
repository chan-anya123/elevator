import time
import threading
import requests
from pyModbusTCP.client import ModbusClient
from enum import IntEnum
import json
import subprocess
import socket

# =========================================================
# STATE DEFINITIONS
# =========================================================
class Sequence(IntEnum):
    IDLE = 0
    MOVING = 1
    ARRIVED = 2
    PULSING = 3
    DONE = 4
    ERROR = 5

class LedColor(IntEnum):
    GREEN = 1
    BLUE = 2
    PURPLE = 3
    RED = 4
    OFF = 5
    YELLOW = 6
    ORANGE = 7
    PINK = 8
    WHITE = 9

# =========================================================
# MASTER SYSTEM
# =========================================================
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
        self.robot_live_data = [0] * (max_reg + 10)
        self.lift_addrs = {"A": {}, "B": {}}
        self.robot_client = ModbusClient(host=self.robot_ip, port=502,auto_open=True, timeout=1.0)
        self.discover_stations()
        threading.Thread(target=self.main_control_loop, daemon=True).start()
        threading.Thread(target=self.mission_scanner_loop, daemon=True).start()
        self.log(f"🚀 Master Started | Robot={self.robot_ip}")

    # =====================================================
    # UTIL
    # =====================================================
    def log(self, msg):
        print(msg)

    def get_my_subnet(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 1))
            my_ip = s.getsockname()[0]
            subnet = '.'.join(my_ip.split('.')[:-1]) + '.'
            return subnet
        except:
            return "192.168.1."
        finally:
            s.close()

    # =====================================================
    # CONFIG
    # =====================================================
    def load_config(self, path):
        try:
            with open(path, 'r') as f:
                config = json.load(f)
                self.config_lifts = config["lifts"]
                self.robot_ip = config["robot_ip"]
                self.bright = config["settings"]["brightness"]
                self.max_timeout = config["settings"]["max_timeout"]
                self.robot_dir_regs = config["settings"].get("robot_dir_registers", [8, 18, 28, 38])
        except Exception as e:
            print(f"!!!!! Load Config Error: {e} !!!!!")
            exit(1)

    # =====================================================
    # MODBUS
    # =====================================================
    def read_modbus(self, station_key, reg, count=1):
        if station_key not in self.stations:
            return None
        client = self.stations[station_key]["client"]
        with self.lock:
            try:
                if not client.is_open:
                    client.open()
                res = client.read_holding_registers(reg, count)
                if res is None:
                    client.close()
                return res
            except:
                client.close()
                return None

    def write_modbus(self, station_key, reg, val):
        if station_key not in self.stations:
            return None
        client = self.stations[station_key]["client"]
        with self.lock:
            try:
                if not client.is_open:
                    client.open()
                res = client.write_single_register(reg, val)
                if res is None:
                    client.close()
                else:
                    time.sleep(0.02)
                return res
            except:
                client.close()
                return None

    def sync_to_robot(self, reg, val):
        with self.lock:
            try:
                if not self.robot_client.is_open:
                    self.robot_client.open()
                return self.robot_client.write_single_register(reg, val)
            except:
                return None

    # =====================================================
    # DISCOVERY
    # =====================================================
    def discover_stations(self):
        base_ip = self.get_my_subnet()
        target_ips = set()
        for item in self.config_lifts:
            target_ips.add(item["ip"])
        for item in self.config_lifts:
            cfg_subnet = '.'.join(item["ip"].split('.')[:-1]) + '.'
            for i in range(1, 255):
                target_ips.add(f"{cfg_subnet}{i}")
        for i in range(1, 255):
            target_ips.add(f"{base_ip}{i}")
        threads = []
        for ip in target_ips:
            t = threading.Thread(target=self.scan_worker,args=(ip,))
            t.daemon = True
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=0.05)
        for k in sorted(self.stations.keys()):
            self.log(f"Ready: {k} {self.stations[k]['ip']}")

    def scan_worker(self, ip):
        try:
            r = requests.get(f"http://{ip}:5000/status",timeout=0.5)
            if r.status_code != 200:
                return
            data = r.json()
            floor_id = int(data.get("floor"))
            lift_id = data.get("lift_id", "A")
            addr_map = data.get("addr", {})
            station_key = f"{lift_id}:{floor_id}"
            with self.lock:
                self.lift_addrs[lift_id] = addr_map
                if lift_id not in self.lifts:
                    self.lifts[lift_id] = {
                        "floor": -1,
                        "door": "CLOSED",
                        "busy": False
                    }
                self.stations[station_key] = {
                    "ip": ip,
                    "client": ModbusClient(host=ip,port=1502,auto_open=True,timeout=0.5),
                    "lift_id": lift_id,
                    "floor": floor_id,
                    "addr": addr_map,
                    "last_heartbeat": -1,
                    "last_update_time": time.time(),
                    "fail_count": 0,
                    "is_active": True
                }
        except:
            pass

    # =====================================================
    # DASHBOARD
    # =====================================================
    def print_dashboard_on_change(self):
        st = self.lifts
        pr = self.prev_status
        changed = (
            st["A"]["floor"] != pr["A"]["floor"] or
            st["A"]["door"] != pr["A"]["door"] or
            st["B"]["floor"] != pr["B"]["floor"] or
            st["B"]["door"] != pr["B"]["door"]
        )
        if changed:
            print(
                f"--- UPDATE --- "
                f"A: F{st['A']['floor']} ({st['A']['door']}) | "
                f"B: F{st['B']['floor']} ({st['B']['door']})"
            )
            self.prev_status["A"] = st["A"].copy()
            self.prev_status["B"] = st["B"].copy()

    # =====================================================
    # MAIN LOOP
    # =====================================================
    def main_control_loop(self):
        while True:
            try:
                read_count = len(self.robot_live_data)
                robot_data = self.robot_client.read_holding_registers(0,read_count)
                if not robot_data:
                    time.sleep(1)
                    continue
                with self.lock:
                    self.robot_live_data = robot_data
                for station_key, station in self.stations.items():
                    addr = station["addr"]
                    lift_type = station["lift_id"]
                    floor_num = station["floor"]
                    mpu_data = self.read_modbus(station_key,0,20)
                    if not mpu_data:
                        station["fail_count"] += 1
                        if station["fail_count"] >= 5 and station["is_active"]:
                            self.log(f"!!!!! {station_key} OFFLINE !!!!!")
                            station["is_active"] = False
                            self.restart_lift_service(station_key)
                        continue
                    station["fail_count"] = 0
                    val_door = mpu_data[addr.get("door", 4)]
                    val_hb = mpu_data[addr.get("heartbeat", 1)]
                    if val_door == 1:
                        self.lifts[lift_type]["door"] = "OPEN"
                        self.lifts[lift_type]["floor"] = floor_num
                        self.sync_to_robot(addr.get("door"),1)
                        self.sync_to_robot(addr.get("floor"),floor_num)
                        self.sync_to_robot(addr.get("lift_status"),1)
                    else:
                        self.lifts[lift_type]["door"] = "CLOSED"

                    if val_hb != station["last_heartbeat"]:
                        station["last_heartbeat"] = val_hb
                        station["last_update_time"] = time.time()
                        if not station["is_active"]:
                            self.log(f"----- {station_key} ACTIVE -----")
                            station["is_active"] = True
                    else:
                        if time.time() - station["last_update_time"] > 10.0:
                            if station["is_active"]:
                                self.log(f"!!!!! {station_key} HEARTBEAT FREEZE !!!!!")
                                station["is_active"] = False
                                self.restart_lift_service(station_key)

                # self.print_dashboard_on_change()

            except Exception as e:
                print(f"Main Loop Error: {e}")
            time.sleep(0.4)

    # =====================================================
    # MISSION SCAN
    # =====================================================
    def mission_scanner_loop(self):
        while True:
            for lift_id in list(self.lifts.keys()):
                if self.lifts[lift_id]["busy"]:
                    continue

                if not self.lift_addrs.get(lift_id):
                    continue

                addr = self.lift_addrs[lift_id]
                target_reg = addr.get("target",6 if lift_id == "A" else 16)
                t_val = self.robot_client.read_holding_registers(target_reg,1)
                if not t_val:
                    continue

                target_floor = t_val[0]
                if target_floor <= 0:
                    continue

                station_key = f"{lift_id}:{target_floor}"
                if station_key not in self.stations:
                    # self.log(f" Station Not Found: {station_key}")
                    continue

                self.lifts[lift_id]["busy"] = True
                self.sync_to_robot(target_reg, 0)
                threading.Thread(
                    target=self.run_mission,
                    args=(station_key, lift_id),
                    daemon=True
                ).start()
            time.sleep(0.5)

    # =====================================================
    # MISSION
    # =====================================================
    def run_mission(self, station_key, lift_type):
        station = self.stations.get(station_key)
        if not station:
            return

        addr = station["addr"]
        floor_num = station["floor"]
        CMD_REG = addr.get("command", 3)
        COLOR_REG = addr.get("led_target", 5)
        BRIGHT_REG = addr.get("led_bright", 7)
        DOOR_REG = addr.get("door", 4)
        self.log(f"----- START {station_key} -----")
        mission_status = Sequence.IDLE
        start_pulsing_time = None

        try:
            current_floor = self.lifts[lift_type]["floor"]
            if floor_num >= current_floor:
                action_cmd = 1
                led_cmd = 3
            else:
                action_cmd = 2
                led_cmd = 4

            while mission_status not in [Sequence.DONE,Sequence.ERROR]:
                match mission_status:
                    case Sequence.IDLE:
                        mission_status=Sequence.MOVING

                    case Sequence.MOVING:
                        self.write_modbus(station_key,BRIGHT_REG,self.bright)
                        self.write_modbus(station_key,COLOR_REG,LedColor.BLUE)
                        self.write_modbus(station_key,CMD_REG,action_cmd)
                        time.sleep(0.4)
                        self.write_modbus(station_key,CMD_REG,led_cmd)
                        mission_status=Sequence.ARRIVED

                    case Sequence.ARRIVED:
                        current_pos=self.lifts[lift_type]["floor"]
                        d_stat=self.read_modbus(station_key,DOOR_REG)
                        if current_pos==floor_num and d_stat and d_stat[0]==1:
                            self.log(f"----- {lift_type} Arrived F{floor_num} ------")
                            mission_status=Sequence.PULSING
                        time.sleep(0.5)

                    case Sequence.PULSING:
                        if start_pulsing_time is None:
                            start_pulsing_time=time.time()
                            self.robot_tracks={reg:False for reg in self.robot_dir_regs}
                        with self.lock:
                            snapshot_data=list(self.robot_live_data)
                        any_robot_active=False
                        all_cleared=True
                        for reg in self.robot_dir_regs:
                            current_dir=snapshot_data[reg] if reg<len(snapshot_data) else 0
                            if current_dir in [1,2]:
                                self.robot_tracks[reg]=True
                                any_robot_active=True
                            if self.robot_tracks[reg] and current_dir!=0:
                                all_cleared=False
                        if not any(self.robot_tracks.values()):
                            all_cleared=False

                        door_check=self.read_modbus(station_key,DOOR_REG)
                        door_val=door_check[0] if door_check else 2
                        case_active=any_robot_active
                        case_clear=all_cleared

                        if case_active:
                            self.write_modbus(station_key,COLOR_REG,LedColor.PINK)
                            self.write_modbus(station_key,CMD_REG,action_cmd)
                            time.sleep(8)

                        elif case_clear:
                            self.log(f"----- COMPLETE {station_key} -----")
                            self.write_modbus(station_key,CMD_REG,0)
                            mission_status=Sequence.DONE

                        else:
                            if door_val==1:
                                self.write_modbus(station_key,COLOR_REG,LedColor.PINK)
                            else:
                                self.write_modbus(station_key,COLOR_REG,LedColor.RED)

                            self.write_modbus(station_key,CMD_REG,0)
                            time.sleep(1)
                            self.write_modbus(station_key,CMD_REG,action_cmd)

                        if time.time()-start_pulsing_time>=self.max_timeout:
                            self.log(f"----- TIMEOUT {station_key} -----")
                            self.write_modbus(station_key,CMD_REG,0)
                            mission_status=Sequence.ERROR
                        time.sleep(0.2)

                    case Sequence.DONE:
                        break

                    case Sequence.ERROR:
                        self.log(f"ERROR {station_key}")
                        break
                
        except Exception as e:
            self.log(f"Mission Error {station_key}: {e}")
        finally:
            self.cleanup_mission(station_key,lift_type,CMD_REG,COLOR_REG)

    # =====================================================
    # CLEANUP
    # =====================================================
    def cleanup_mission(self,station_key,lift_type,cmd_reg,color_reg):
        try:
            self.write_modbus(station_key,cmd_reg,0)
            self.write_modbus(station_key,color_reg,LedColor.GREEN)
        except Exception as e:
            self.log(f"Cleanup Error: {e}")
        time.sleep(1.0)
        self.lifts[lift_type]["busy"] = False
        self.log(f"END {station_key}")

    # =====================================================
    # SELF HEAL
    # =====================================================
    def restart_lift_service(self, station_key):
        if station_key not in self.stations:
            return

        ip = self.stations[station_key]["ip"]
        self.log(f"---- Restart Service: {station_key} ({ip}) ----")
        ssh_cmd = (
            f"ssh -o ConnectTimeout=5 "
            f"arduino@{ip} "
            f"'sudo systemctl restart lift-service.service'"
        )
        try:
            subprocess.Popen(
                ssh_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.stations[station_key]["last_update_time"] = time.time() + 15.0
        except Exception as e:
            self.log(f"!!!!! Restart Fail: {e} !!!!!!")

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    master = MasterSystem()
    while True:
        time.sleep(1)