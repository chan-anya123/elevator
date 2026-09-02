# Elevator Control System (Arduino UNO Q Controller)

A high-reliability, Python-based elevator station controller and Modbus communication bridge designed for industrial automation and Autonomous Mobile Robot (AMR / AGV) integration, implemented on the **Arduino UNO Q** dual-processor platform.

---

## 🚀 Overview

The system runs on the **Arduino UNO Q** (Linux MPU + Zephyr RTOS MCU) using [`main2.py`](file:///home/cookies/lift/arduino_uno_q/main2.py), providing:

- **Dual-Modbus Bridge Architecture**:
  - **Modbus TCP Client (Port 502)**: Connects directly to the Robot/AGV PLC or Central Controller to read mission calls and synchronize elevator telemetry.
  - **Modbus TCP Server (Port 1502)**: Operates locally on `0.0.0.0:1502` for local diagnostics, SCADA integration, and direct station control.
- **Autonomous Mission State Machine & Scanner**:
  - Actively scans for robot floor calls (`call_target`) matching the station floor.
  - Automatically executes the full mission sequence: button actuation, door open verification, pulsing cycles, and state cleanup.
  - Built-in **60-second Mission Watchdog** for auto-recovery from stuck states.
- **Selective Edge-Triggered Telemetry Sync**:
  - Writes floor, door, and status data to the robot PLC only when doors open or immediately upon closing at the active floor, eliminating bus collisions across multi-station deployments.
- **Rich Status Feedback**:
  - WS2812B Addressable RGB LED Strip signaling mission phases, door state, errors, and idle status.
- **Web Management Dashboard & REST API (Port 5000)**:
  - Interactive HTML5 web console (`ui.html`).
  - REST API for status reporting, configuration hot-update, manual relay control, and network management.
- **Zero-Configuration mDNS & WiFi Management**:
  - Auto-assigns hostname based on MAC address (`http://lift<last4_mac>.local:5000`).
  - Dynamic WiFi switching with automatic fallback to recovery hotspot (`MyLiftHotspot`).

---

## 🏗️ System Architecture

```text
               +-------------------------------------------------------------+
               |            Robot / AMR PLC / Central Controller             |
               +------------------------------+------------------------------+
                                              |
                                              | Modbus TCP (Port 502)
                                              v
+-------------------------------------------------------------------------------------------+
| Linux MPU Layer (Python 3 / Flask / pyModbusTCP - main2.py)                               |
|                                                                                           |
|  +---------------------------------+             +-------------------------------------+  |
|  | Modbus TCP Client (Port 502)    |             | Modbus TCP Server (Port 1502)       |  |
|  | - Connects to robot_ip:502      |             | - Listens on 0.0.0.0:1502           |  |
|  | - Scans call_target registers   |             | - Exposes station telemetry         |  |
|  | - Selective edge-triggered sync |             | - Accepts direct SCADA/PLC commands |  |
|  +----------------+----------------+             +------------------+------------------+  |
|                   |                                                 |                     |
|                   +-----------------------+-------------------------+                     |
|                                           |                                               |
|  +----------------------------------------v--------------------------------------------+  |
|  | Mission Scanner & Autonomous State Machine (Sequence & 60s Watchdog)                |  |
|  +----------------------------------------+--------------------------------------------+  |
|                                           |                                               |
|  +----------------------------------------v--------------------------------------------+  |
|  | Flask HTTP REST API & Web Dashboard (Port 5000) / Zeroconf mDNS Broadcast          |  |
|  +----------------------------------------+--------------------------------------------+  |
|                                           |                                               |
|                                           | MessagePack RPC Client                        |
|                                           | (/var/run/arduino-router.sock)                |
+-------------------------------------------|-----------------------------------------------+
                                            |
                                            v
+-------------------------------------------------------------------------------------------+
| MCU Layer (Arduino UNO Q / Zephyr RTOS Firmware - arduino_code/)                          |
|  - Low-level deterministic I/O relay control (UP / DOWN Solenoids)                        |
|  - Physical Button LED indicators (UP / DOWN)                                             |
|  - Hardware Door Sensor inputs (Pin A1 for standard floors, Pin A4 for B1)                |
|  - WS2812B Addressable RGB LED strip driver (16 LEDs)                                     |
|  - Safety debouncing (5000ms press lock, 800ms solenoid auto-release)                     |
+-------------------------------------------------------------------------------------------+
```

---

## ⚡ Key Features

1. **Hybrid Bridge Communication**:
   - Acts as a **Modbus Client** querying the AMR PLC (`robot_ip:502`) while simultaneously hosting a **Modbus Server** (`0.0.0.0:1502`).
2. **Autonomous Mission Sequencing**:
   - State transition pipeline: `IDLE` $\rightarrow$ `MOVING` $\rightarrow$ `ARRIVED` $\rightarrow$ `PULSING` $\rightarrow$ `DONE` $\rightarrow$ `IDLE`.
3. **Fail-Safe Watchdog Protection**:
   - Automatically aborts and resets any mission exceeding 60 seconds of execution, restoring the station to `IDLE` and status LED to `BLUE`.
4. **Collision-Free Multi-Lift Register Sync**:
   - Only writes to Robot Port 502 when the elevator door is open or just closed at this station's floor. Idle/closed stations do not overwrite registers.
5. **Configurable Lift Offsets**:
   - Supports **Lift A** (base offset 0) and **Lift B** (base offset 10) with automatic register correction.
6. **Hardware-Level Safety**:
   - Press-lock timeout (5000ms) prevents button hammering.
   - Solenoid auto-release timeout (800ms) protects physical actuators.
7. **Comprehensive Web Console**:
   - Embedded single-page dashboard for status monitoring, configuration adjustment, manual override, and WiFi provisioning.

---

## 🔄 Mission State Machine & Sequence Flow

### Mission States (`Sequence` Enum)

| Value | State | Description |
| :---: | :--- | :--- |
| `0` | `IDLE` | Standby state. Actively monitoring `call_target` on Robot Port 502. |
| `1` | `MOVING` | Elevator call triggered. Solenoids activated, direction indicator turned ON. |
| `2` | `ARRIVED` | Elevator arrived at floor. Waiting for physical door sensor to open (`door == OPEN`). |
| `3` | `PULSING` | Door open. Solenoid pulsed for `pulse_count` cycles to hold doors open for robot ingress/egress. |
| `4` | `DONE` | Mission completed. Indicators cleared, outputs released, registers reset. |
| `5` | `ERROR` | Mission or bridge communication error. Fault LED indicated. |

### Mission Execution Flowchart

```text
       +-------------------------------------------------------------------+
       |                       [0] IDLE / STANDBY                          |
       |  - Modbus Client constantly scans call_target register on Port 502|
       |  - WS2812 LED: BLUE (Ready)                                      |
       +---------------------------------+---------------------------------+
                                         |
                       call_target == current_floor (1..99)
                                         |
                                         v
       +-------------------------------------------------------------------+
       |                       [1] MOVING PHASE                            |
       |  - Write robot: lift_status = 2 (BUSY), door = 2 (CLOSED)         |
       |  - WS2812 LED: GREEN (Moving)                                     |
       |  - Trigger UP/DOWN Solenoid (0.4s) -> Stop (0.2s)                 |
       |  - Turn ON Button Direction Indicator (UP/DOWN)                   |
       +---------------------------------+---------------------------------+
                                         |
                                         v
       +-------------------------------------------------------------------+
       |                       [2] ARRIVED PHASE                           |
       |  - Wait for physical door sensor: door_val == 1 (OPEN)            |
       |  - Write robot: door = 1, lift_status = 1 (READY), call_target = 0|
       |  - WS2812 LED: PINK (Arrived & Door Open)                         |
       +---------------------------------+---------------------------------+
                                         |
                                     Door Open
                                         |
                                         v
       +-------------------------------------------------------------------+
       |                       [3] PULSING PHASE                           |
       |  - Loop for pulse_count cycles (default: 5 cycles):               |
       |      * Read physical door sensor status                           |
       |      * Sync door state & lift_status to Robot Port 502            |
       |      * LED: PINK (if door open) / RED (if door closed)            |
       |      * Pulse Solenoid: ON for 1.0s -> OFF for 0.5s                |
       +---------------------------------+---------------------------------+
                                         |
                               Pulsing Complete
                                         |
                                         v
       +-------------------------------------------------------------------+
       |                       [4] DONE & CLEANUP                          |
       |  - Stop outputs (cmd 0), Clear indicators (cmd 6), Release (cmd 5)|
       |  - Reset robot registers: call_target = 0, status = 2, door = 2   |
       |  - Restore WS2812 LED: BLUE                                       |
       |  - Return to [0] IDLE state                                       |
       +-------------------------------------------------------------------+
```

---

## 📊 Modbus Register Specifications

The system utilizes offset-based Modbus addressing configured by `lift_id`:
- **Lift A**: Base offset = `0`
- **Lift B**: Base offset = `10`

### 1. Robot Modbus TCP Client (Port 502)

Communicates with the Robot PLC/Controller at `robot_ip:502`.

| Lift A Reg | Lift B Reg | Type | Access | Register Name | Description / Values |
| :---: | :---: | :---: | :---: | :--- | :--- |
| `0` | `10` | Holding | R/W | `floor` | Current floor number reported by station (e.g. `1`, `2`, `4`). |
| `2` | `12` | Holding | R/W | `lift_status` | `1` = Ready / Arrived, `2` = Busy / Moving. |
| `4` | `14` | Holding | R/W | `door` | `1` = Open, `2` = Closed. |
| `6` | `16` | Holding | R/W | `call_target` | Target floor requested by robot (`1`–`99`). Cleared to `0` upon completion. |

### 2. Local Board Modbus TCP Server (Port 1502)

Operates on `0.0.0.0:1502` on the Arduino UNO Q Linux MPU for telemetry and local control.

| Lift A Reg | Lift B Reg | Access | Register Name | Description |
| :---: | :---: | :---: | :--- | :--- |
| `0` | `10` | R | `floor` | Current station floor. |
| `1` | `11` | R | `heartbeat` | 16-bit rolling counter (0–65535) incremented every sync tick while bridge is healthy. |
| `2` | `12` | R | `lift_status` | `1` = MCU Bridge Healthy, `0` = MCU Bridge Offline. |
| `3` | `13` | R/W | `command` | Action command register (`1`=UP, `2`=DOWN, etc.). Auto-resets to `99` after execution. |
| `4` | `14` | R | `door` | `1` = Open, `2` = Closed. |
| `5` | `15` | R/W | `led_target` | Set WS2812 status color (`1`–`9`, see LedColor table). |
| `7` | `17` | R/W | `led_bright` | Set WS2812 brightness percentage (`1`–`100%`). |

### 3. Selective Edge-Triggered Sync Logic

To prevent register clobbering when multiple Lift Stations share the same Robot Modbus Server:
- **Door OPEN at this floor**: The station actively writes `floor`, `door=1 (OPEN)`, and `lift_status=1 (READY)` to the robot.
- **Door CLOSED transition**: When the door closes at this floor, the station writes `door=2 (CLOSED)` and `lift_status=2 (BUSY)` **once**.
- **Door CLOSED & Idle**: The station does **not** write to Robot Port 502, leaving the robot's registers open for other active stations.

---

## 💡 WS2812B RGB Status Feedback

The controller manages a 16-LED WS2812B RGB strip to communicate system state:

| Code | `LedColor` Enum | Color | System State / Usage |
| :---: | :--- | :---: | :--- |
| `1` | `GREEN` | 🟢 Green | Elevator call active / Mission moving (`MOVING` state). |
| `2` | `BLUE` | 🔵 Blue | System Idle / Standby / Bridge healthy. |
| `3` | `PURPLE` | 🟣 Purple | Custom / Auxiliary state. |
| `4` | `RED` | 🔴 Red | MCU bridge communication loss / Door closed during pulsing / Fault. |
| `5` | `OFF` | ⚫ Off | LEDs disabled. |
| `6` | `YELLOW` | 🟡 Yellow | Warning state / Pre-movement alert. |
| `7` | `ORANGE` | 🟠 Orange | Auxiliary notification. |
| `8` | `PINK` | 🌸 Pink | Elevator arrived and door is open (`ARRIVED` & `PULSING` states). |
| `9` | `WHITE` | ⚪ White | High-intensity illumination. |

---

## 🌐 HTTP REST API Reference

Base URL: `http://<device-ip>:5000` or `http://<hostname>.local:5000`

### Endpoints Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Renders the HTML5 Web Dashboard (`ui.html`). |
| `GET` | `/status` | Returns full station telemetry, network info, mission state, and Modbus registers. |
| `POST` | `/command` | Executes direct relay or indicator commands via MessagePack RPC. |
| `POST` | `/save_config` | Saves configuration to `lift_config.json` and updates robot client IP dynamically. |
| `POST` | `/set_network_mode` | Updates network settings (DHCP / Static IP). |
| `POST` | `/change_wifi` | Connects to a WiFi SSID with automatic rollback to `MyLiftHotspot`. |
| `POST` | `/reset_to_hotspot` | Forces WiFi interface to switch to recovery hotspot mode. |
| `POST` | `/reset_bridge` | Resets the UNIX socket connection to the MCU Zephyr bridge. |

---

### Endpoint Details & Payloads

#### 1. `GET /status`
Returns complete diagnostic and runtime status.

**Example Response:**
```json
{
  "ip": "192.168.20.60",
  "mac": "04:bf:1b:b5:a5:b8",
  "hostname": "lift55b8",
  "mdns_url": "http://lift55b8.local:5000",
  "lift_id": "A",
  "floor": "1",
  "robot_ip": "192.168.20.42",
  "network_mode": "dhcp",
  "modbus_port_outside": 502,
  "modbus_port_inside": 1502,
  "modbus_port": 1502,
  "pulse_count": 5,
  "brightness": 100,
  "settings": {
    "pulse_count": 5,
    "brightness": 100
  },
  "mission_busy": false,
  "mission_state": 0,
  "arduino": {
    "door": "CLOSED",
    "floor": "1",
    "is_bridge_ok": true
  },
  "addr": {
    "robot_port_502": {
      "floor": 0,
      "lift_status": 2,
      "door": 4,
      "call_target": 6
    },
    "board_port_1502": {
      "floor": 0,
      "heartbeat": 1,
      "lift_status": 2,
      "command": 3,
      "door": 4,
      "led_target": 5,
      "led_bright": 7
    }
  }
}
```

#### 2. `POST /command`
Sends manual relay and LED control commands.

**Payload:**
```json
{
  "action": "1"
}
```

| Action Code | Function |
| :---: | :--- |
| `"0"` | Stop all outputs / release |
| `"1"` | Trigger UP Solenoid Relay |
| `"2"` | Trigger DOWN Solenoid Relay |
| `"3"` | Turn ON UP Button Indicator LED |
| `"4"` | Turn ON DOWN Button Indicator LED |
| `"5"` | Release Solenoid Lock |
| `"6"` | Clear All Button Indicator LEDs |

#### 3. `POST /save_config`
Saves updated parameters to `lift_config.json`. Automatically reconfigures the Modbus client connection if `robot_ip` changes.

**Payload:**
```json
{
  "lift_id": "A",
  "floor_name": "1",
  "robot_ip": "192.168.20.42",
  "pulse_count": 5,
  "brightness": 100,
  "addr": {
    "robot_port_502": { "floor": 0, "lift_status": 2, "door": 4, "call_target": 6 },
    "board_port_1502": { "floor": 0, "heartbeat": 1, "lift_status": 2, "command": 3, "door": 4, "led_target": 5, "led_bright": 7 }
  }
}
```

#### 4. `POST /change_wifi`
Connects to a new WiFi access point asynchronously in the background.

**Payload:**
```json
{
  "ssid": "Warehouse_WiFi",
  "password": "FactorySecureKey"
}
```

---

## 🔌 MessagePack RPC Interface (MPU ↔ MCU)

The Linux MPU (`main2.py`) communicates with the Zephyr RTOS MCU firmware over a UNIX Domain Socket at `/var/run/arduino-router.sock` using MessagePack binary RPC.

### Message Structure
```python
# Format: [msg_type, msg_id, method_name, params_array]
[0, 1, "method_name", ["param1", "param2"]]
```

### Supported RPC Methods

| Method | Parameter | Response Example | Description |
| :--- | :--- | :--- | :--- |
| `status` | `[""]` | `"DOOR:CLOSED\|FLOOR:1"` | Queries real-time door sensor and floor status. |
| `move` | `["1"]` | `"OK"` | Controls solenoid relays and indicator outputs (`0`–`6`). |
| `set_led` | `["GREEN,100"]` | `"OK"` | Sets WS2812 color name and brightness percentage. |
| `reset` | `[""]` | `"OK"` | Resets MCU communication bridge socket. |

---

## ⚙️ Configuration Schema (`lift_config.json`)

The controller dynamically generates and maintains `lift_config.json`:

```json
{
    "lift_id": "A",
    "floor_name": "1",
    "robot_ip": "192.168.20.42",
    "network_mode": "dhcp",
    "static_ip": "",
    "gateway": "",
    "subnet": "24",
    "settings": {
        "pulse_count": 5,
        "brightness": 100
    },
    "addr": {
        "robot_port_502": {
            "floor": 0,
            "lift_status": 2,
            "door": 4,
            "call_target": 6
        },
        "board_port_1502": {
            "floor": 0,
            "heartbeat": 1,
            "lift_status": 2,
            "command": 3,
            "door": 4,
            "led_target": 5,
            "led_bright": 7
        }
    }
}
```

---

## 🧰 Hardware Specifications & Pinout

### Arduino UNO Q Pin Mapping

| Pin | Type | Function | Notes |
| :---: | :---: | :--- | :--- |
| **A1** | Input | Door Sensor (Standard) | Used on all floors except B1. Active LOW / Pull-up. |
| **A4** | Input | Door Sensor (Floor B1) | Specific door sensor input for basement floor B1. |
| **A2** | Input | UP Elevator Button | Manual floor call button input. |
| **A3** | Input | DOWN Elevator Button | Manual floor call button input. |
| **D11** | Output | UP Solenoid Relay | Actuates physical elevator UP call button. |
| **D7** | Output | DOWN Solenoid Relay | Actuates physical elevator DOWN call button. |
| **D8** | Output | UP Button Indicator LED | Illuminates the physical UP button LED. |
| **D12** | Output | DOWN Button Indicator LED | Illuminates the physical DOWN button LED. |
| **D6** | Output | WS2812B RGB Data | 16-LED Addressable NeoPixel Strip. |

### Built-in Safety Parameters
- **Press Lock Protection**: Repeated button presses are debounced and blocked for **5000 ms**.
- **Solenoid Auto-Release**: Actuator outputs automatically disengage after **800 ms** to prevent solenoid burn-out.

---

## 📁 Repository Structure

```text
/
├── arduino_uno_q/                  # Arduino UNO Q Controller (Station)
│   ├── arduino_code/               # MCU Zephyr Firmware
│   │   ├── lift.ino                # Low-level I/O, safety tasks & RPC server
│   │   └── ws2812b-bitbang.h       # WS2812 bit-banging driver
│   ├── templates/                  # Web Dashboard UI
│   │   ├── index.html
│   │   └── ui.html                 # Modern glassmorphism dashboard
│   ├── lift_config.json            # Runtime station configuration
│   ├── main.py                     # Legacy MPU controller
│   ├── main2.py                    # State-of-the-art Dual-Modbus Bridge Controller
│   ├── MANUAL.md                   # Operational & Technical System Manual
│   ├── update_mcu.sh               # Remote firmware build & flash script
│   └── update_mcu_v2.sh            # Enhanced deployment script
├── lift_server/                    # Central Master Server (Optional Orchestrator)
│   ├── lift_single_ui.py           # Multi-lift coordination & UI
│   ├── lift_config.json            # System configuration
│   └── update.py                   # Automatic patch update tool
└── README.md                       # Main Documentation (This file)
```

---

## 🚀 Installation & Quick Start

### 1. Install Dependencies on Arduino UNO Q Linux MPU

```bash
sudo apt update
sudo apt install -y python3-pip python3-flask python3-flask-cors python3-msgpack network-manager
pip3 install pyModbusTCP zeroconf --break-system-packages
```

### 2. Run the Controller

```bash
cd /home/arduino/lift/arduino_uno_q
python3 main2.py
```

### 3. Deploy as a Systemd Service

Create `/etc/systemd/system/lift-service.service`:

```ini
[Unit]
Description=Elevator Station Controller Service (main2.py)
After=network.target arduino-router.service
Requires=arduino-router.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/arduino/lift/arduino_uno_q
ExecStart=/usr/bin/python3 /home/arduino/lift/arduino_uno_q/main2.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable lift-service.service
sudo systemctl start lift-service.service
```

### 4. Updating MCU Firmware (`update_mcu.sh`)

Deploy and flash firmware directly from your development machine:

```bash
cd arduino_uno_q
chmod +x update_mcu.sh
./update_mcu.sh
```

---

## 🔍 Diagnostics & Verification

### Test MessagePack RPC Bridge
```bash
python3 -c "import socket,msgpack;s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.connect('/var/run/arduino-router.sock');s.sendall(msgpack.packb([0,1,'status',[]]));print(msgpack.unpackb(s.recv(4096))[3])"
```
*Expected output: `DOOR:CLOSED|FLOOR:1`*

### Check Service Logs
```bash
sudo journalctl -u lift-service.service -f
```

### Access Web Console
Open your browser and navigate to:
```text
http://<board-ip>:5000
# or via mDNS:
http://lift<last4_mac>.local:5000
```



