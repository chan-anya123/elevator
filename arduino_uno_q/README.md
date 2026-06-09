# Arduino UNO Q
Arduino UNO Q is a dual-processor elevator controller designed for elevator automation and robotic integration.
The system consists of:

* **MPU Layer** (Linux-based Controller)
* **MCU Layer** (Arduino + Zephyr RTOS)

The MPU handles networking, Modbus TCP communication, web configuration, and device management, while the MCU handles real-time elevator I/O control.

---

# Contents
* System Architecture
* MPU Responsibilities
* API Interfaces
* MCU Responsibilities
* MCU Software Architecture
* Safety Features
* Features
* Installation
* Quick Start
* Hotspot Setup
* Service Setup
* Diagnostics
* Repository Structure

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

## Web Interface

```text
http://<device-ip>:5000
```

## Network Features

* DHCP Mode
* Static IP Mode
* WiFi Switching
* Hotspot Mode

## MessagePack RPC

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

# API Interfaces

Arduino UNO Q provides three communication interfaces.

| Interface       | Protocol      | Purpose                         |
| --------------- | ------------- | ------------------------------- |
| Web UI          | HTTP          | Configuration and Monitoring    |
| Modbus TCP      | TCP Port 1502 | External Automation Integration |
| MessagePack RPC | UNIX Socket   | MPU ↔ MCU Communication         |

---

## HTTP REST API

Base URL:

```text
http://<device-ip>:5000
```

| Method | Endpoint            | Description                   |
| ------ | ------------------- | ----------------------------- |
| GET    | `/`                 | Web Dashboard                 |
| GET    | `/status`           | Get current controller status |
| POST   | `/command`          | Execute lift command          |
| POST   | `/save_config`      | Save controller configuration |
| POST   | `/change_wifi`      | Change WiFi settings          |
| POST   | `/reset_to_hotspot` | Restore hotspot mode          |

### Example

```http
GET /status
```

Response:

```json
{
  "door": "CLOSED",
  "floor": 2,
  "lift_status": 1
}
```

---

## MessagePack RPC API

Socket:

```text
/var/run/arduino-router.sock
```

### Available Methods

| Method  | Parameter     | Description                  |
| ------- | ------------- | ---------------------------- |
| status  | []            | Read controller status       |
| move    | ["0"]         | Stop all outputs             |
| move    | ["1"]         | Trigger UP solenoid          |
| move    | ["2"]         | Trigger DOWN solenoid        |
| move    | ["3"]         | Enable UP indicator          |
| move    | ["4"]         | Enable DOWN indicator        |
| move    | ["5"]         | Stop solenoids               |
| move    | ["6"]         | Clear indicators             |
| set_led | ["GREEN,100"] | Set LED color and brightness |

### RPC Examples

| Request                         | Description                      |
| ------------------------------- | -------------------------------- |
| `[0,1,"status",[]]`             | Read controller status           |
| `[0,1,"move",["1"]]`            | Trigger UP relay                 |
| `[0,1,"move",["2"]]`            | Trigger DOWN relay               |
| `[0,1,"set_led",["GREEN,100"]]` | Set LED to green                 |
| `[0,1,"set_led",["RED,50"]]`    | Set LED to red at 50% brightness |

---

## Modbus TCP API

Default Port:

```text
1502
```

### Register Map

| Address | Access | Name           | Description            |
| ------- | ------ | -------------- | ---------------------- |
| 0       | R      | Floor          | Current Floor          |
| 1       | R      | Heartbeat      | Communication Counter  |
| 2       | R      | Lift Status    | 0 = Offline, 1 = Ready |
| 3       | R/W    | Command        | Lift Control Command   |
| 4       | R      | Door           | 1 = Open, 2 = Closed   |
| 5       | R/W    | LED Target     | LED Color Selection    |
| 7       | R/W    | LED Brightness | 0-100%                 |

### Command Register (Address 3)

| Value | Action    |
| ----- | --------- |
| 0     | Stop All  |
| 1     | Move Up   |
| 2     | Move Down |

### LED Target Register (Address 5)

| Value | Color  |
| ----- | ------ |
| 1     | Green  |
| 2     | Blue   |
| 3     | Purple |
| 4     | Red    |
| 5     | Off    |
| 6     | Yellow |
| 7     | Orange |
| 8     | Pink   |
| 9     | White  |

### LED Brightness Register (Address 7)

| Value | Description           |
| ----- | --------------------- |
| 0     | Off                   |
| 1-100 | Brightness Percentage |

---

# MCU Responsibilities

The MCU is responsible for all real-time elevator control functions.

## Inputs

| Pin | Function    |
| --- | ----------- |
| A1  | Door Sensor |
| A2  | Up Button   |
| A3  | Down Button |

## Outputs

| Pin | Function              |
| --- | --------------------- |
| D11 | Up Solenoid           |
| D7  | Down Solenoid         |
| D8  | Up Button Indicator   |
| D12 | Down Button Indicator |

## RGB Lighting

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

| Thread      | Responsibility                      |
| ----------- | ----------------------------------- |
| safety_task | Door monitoring and button handling |
| motor_task  | Relay and output control            |
| led_task    | WS2812 LED control                  |
| bridge_task | MessagePack RPC communication       |

## Safety Task

Responsibilities:

* Door monitoring
* Button edge detection
* Anti-bounce logic
* Press lock protection
* Automatic indicator reset

## Motor Task

Responsibilities:

* Solenoid pulse generation
* Relay control
* Button indicator control

## LED Task

Responsibilities:

* WS2812 updates
* Brightness control
* Color control

## Bridge Task

Responsibilities:

* MessagePack RPC handling
* Command processing
* Status reporting

---

# Safety Features

## Press Lock

Repeated button presses are blocked for:

```text
5000 ms
```

## Solenoid Auto Release

Solenoid activation automatically expires after:

```text
800 ms
```

## Door Protection

When the door state changes:

* Button states are reset
* Indicator states are updated
* False triggers are prevented

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

Install required packages using the unified `requirements.txt` from the project root:

```bash
sudo apt update
sudo apt install python3-pip -y

# Install all dependencies
pip3 install -r ../requirements.txt --break-system-packages
```

Alternatively, install individual packages:

```bash
sudo apt install python3-msgpack python3-flask python3-flask-cors -y
sudo pip3 install pyModbusTCP --break-system-packages
```

---

# Updating MCU Firmware

To update the Arduino (MCU layer) firmware directly from the MPU (Linux layer), use the provided `update_mcu.sh` script. This script automatically handles stopping the lift service, compiling the `.ino` file using `arduino-cli`, flashing the board via USB/Serial, and restarting the service.

**Prerequisites:**
You must have `arduino-cli` installed and the appropriate core (`arduino:renesas_uno`) configured on the MPU.

**Usage:**
```bash
cd arduino_uno_q
sudo ./update_mcu.sh
```

---

# Quick Start

Start the controller service:

```bash
sudo systemctl start lift-service.service
```

Verify MCU communication:

```bash
python3 -c "import socket,msgpack;s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.connect('/var/run/arduino-router.sock');s.sendall(msgpack.packb([0,1,'status',[]]));print(msgpack.unpackb(s.recv(4096))[3])"
```

Expected output:

```text
DOOR:CLOSED|FLOOR:2
```

Open the Web UI:

```text
http://<device-ip>:5000
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

## Recovery Hotspot

Default credentials:

```text
SSID     : NextElevator_14B5CD0A8761
Password : 12345678
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

## Test MessagePack RPC

```bash
python3 -c "
import socket, msgpack
s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/var/run/arduino-router.sock')
s.sendall(msgpack.packb([0,1,'status',[]]))
print(msgpack.unpackb(s.recv(4096))[3])
"
```

Expected output:

```text
DOOR:CLOSED|FLOOR:2
```

---

# Repository Structure

```text
arduino_uno_q/
├── arduino_code/
│   ├── lift.ino
│   └── ws2812b-bitbang.h
├── templates/
├── config.json
├── main.py
└── README.md
```
