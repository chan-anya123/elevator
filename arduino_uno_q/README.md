# Arduino UNO Q
Arduino UNO Q is a dual-processor elevator controller designed for elevator automation and robotic integration.
The system consists of:
* **MPU Layer** (Linux-based Controller)
* **MCU Layer** (Arduino + Zephyr RTOS)
The MPU handles networking, Modbus TCP communication, web configuration, and device management, while the MCU handles real-time elevator I/O control.

---

# System Architecture
```text
  External Client
       │
       │ Modbus TCP
       ▼
+--------------------------+
| MPU Layer                |
|--------------------------|
| Flask Web UI            |
| Modbus TCP Server       |
| MessagePack RPC Client  |
| WiFi / Hotspot Manager  |
| Configuration Storage   |
+------------+------------+
             │
             │ UNIX Socket
             ▼
+--------------------------+
| MCU Layer               |
|--------------------------|
| Zephyr RTOS             |
| Elevator Button Logic   |
| Door Monitoring         |
| Solenoid Control        |
| Button Indicator LEDs   |
| WS2812 RGB LEDs         |
+--------------------------+
```

---

# MPU Responsibilities
The MPU provides:
* Modbus TCP Server (Port 1502)
* Web Configuration Interface
* WiFi Management
* Hotspot Recovery
* MessagePack RPC Bridge
* Configuration Storage
* System Monitoring

### Web Interface
```text
http://<device-ip>:5000
```
### Network Features
* DHCP Mode
* Static IP Mode
* WiFi Switching
* Hotspot Mode
### MessagePack RPC
Communication with the MCU is performed through:
```text
/var/run/arduino-router.sock
```
Supported RPC methods:
```text
status
move
set_led
```
---

# MCU Responsibilities

The MCU is responsible for all real-time elevator control functions.

### Inputs
| Pin | Function    |
| --- | ----------- |
| A1  | Door Sensor |
| A2  | Up Button   |
| A3  | Down Button |

### Outputs
| Pin | Function              |
| --- | --------------------- |
| 11  | Up Solenoid           |
| 7   | Down Solenoid         |
| 8   | Up Button Indicator   |
| 12  | Down Button Indicator |

### RGB Lighting
WS2812B RGB Strip
```text
16 LEDs
```
Supported colors:

```text
RED
GREEN
BLUE
YELLOW
ORANGE
PURPLE
PINK
WHITE
OFF
```
Brightness:
```text
0 - 100 %
```

---

# MCU Software Architecture
The MCU runs multiple Zephyr RTOS threads.

### Safety Task
Responsibilities:
* Door monitoring
* Button edge detection
* Anti-bounce logic
* Press lock protection
* Automatic indicator reset

### Motor Task
Responsibilities:
* Solenoid pulse generation
* Relay control
* Button indicator control

### LED Task
Responsibilities:
* WS2812 updates
* Brightness control
* Color control

### Bridge Task
Responsibilities:
* MessagePack RPC handling
* Command processing
* Status reporting

---

# Safety Features
### Press Lock
Repeated button presses are blocked for:
```text
5000 ms
```

### Solenoid Auto Release
Solenoid activation automatically expires after:
```text
800 ms
```

### Door Protection
When the door state changes:
* Button states are reset
* Indicator states are updated
* False triggers are prevented

---

# RPC Commands
### Status
Returns controller status:
```text
DOOR:CLOSED|FLOOR:2
```
---

### Move
| Command | Action            |
| ------- | ----------------- |
| 0       | Stop All          |
| 1       | Up Solenoid       |
| 2       | Down Solenoid     |
| 3       | Up Indicator ON   |
| 4       | Down Indicator ON |
| 5       | Stop Solenoids    |
| 6       | Clear Indicators  |

---

### Set LED
Example:
```text
GREEN,100
RED,50
BLUE,20
OFF,100
```

---

# Modbus Register Map
| Address | Name           | Description            |
| ------- | -------------- | ---------------------- |
| 0       | Floor          | Current Floor          |
| 1       | Heartbeat      | Communication Counter  |
| 2       | Lift Status    | 0 = Offline, 1 = Ready |
| 3       | Command        | Control Command        |
| 4       | Door           | 1 = Open, 2 = Closed   |
| 5       | LED Target     | Color Selection        |
| 7       | LED Brightness | 0-100                  |

---

# Features
* Elevator Button Control
* Door Monitoring
* Solenoid Relay Control
* RGB Status Lighting
* Modbus TCP Interface
* MessagePack RPC Interface
* WiFi Configuration
* Hotspot Recovery
* Zephyr RTOS Multithreading
* Automatic Fault Recovery

---

# Installation
Install required packages:

```bash
sudo apt update
sudo apt install python3-msgpack
sudo apt install python3-flask python3-pip -y
sudo pip3 install pyModbusTCP --break-system-packages
```
---

# Hotspot Setup
Create a recovery hotspot for initial setup and maintenance.
Create hotspot:

```bash
sudo nmcli device wifi rescan
sudo nmcli device wifi hotspot \
    ssid NextElevator_14B5CD0A8761 \
    password 12345678
sudo nmcli connection modify Hotspot \
    connection.id MyLiftHotspot
```
Enable automatic connection:
```bash
sudo nmcli connection modify \
    "MyLiftHotspot" \
    connection.autoconnect yes
sudo nmcli device set wlan0 autoconnect yes
```

Prioritize production WiFi network:
```bash
sudo nmcli connection modify \
    "Next" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100
```

---

# Service Setup

Create service file:
```bash
sudo nano /etc/systemd/system/lift-service.service
```
Reload systemd:
```bash
sudo systemctl daemon-reload
```
Enable automatic startup:
```bash
sudo systemctl enable lift-service.service
sudo systemctl start lift-service.service
```
---

# Diagnostics
## Check Arduino Bridge
```bash
sudo journalctl -u arduino-router -f
```
## Check Lift Service
```bash
sudo journalctl -u lift-service.service -f
```

