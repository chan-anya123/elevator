# Elevator Control System (Master & Station)

A Python-based cross-platform elevator control and monitoring system designed for industrial automation and robotic integration (AGVs/AMRs).

## 🚀 Overview

This project provides a comprehensive communication and control interface for elevator systems, allowing external clients such as robots, AGVs, AMRs, or automation systems to:

- **Call elevators** via Modbus TCP.
- **Select target floors** dynamically.
- **Monitor elevator status** (Door status, current floor, heartbeats).
- **Receive real-time feedback** via a web interface or REST API.
- **Self-heal** and rediscover disconnected lift stations automatically.

---

## 🏗️ Architecture

The system is divided into two primary layers:

### 1. Lift Master Server (`lift_server/`)
The central brain that orchestrates multiple lifts and integrates with the robot's PLC/Controller.
- **Runs on**: Linux / Windows (Python 3.x)
- **Primary Role**: Mission management, Robot synchronization, and central configuration.
- **Interfaces**: Modbus TCP (Server/Client), Flask Web UI, REST API.

### 2. Lift Station (`arduino_uno_q/`)
The physical controller connected to each elevator.
- **Runs on**: Arduino UNO Q (Dual-processor: Linux MPU + Zephyr MCU)
- **Primary Role**: Real-time I/O control (solenoids, buttons, sensors) and LED feedback.
- **Interfaces**: Modbus TCP (Port 1502), REST API (Port 5000), MessagePack RPC.

```text
+-----------------------+
|  Central Cloud / Web  | <--- OTA Update (patch.zip / lift_version.txt)
+-----------+-----------+
            │
            ▼
+-----------------------+
|      Robot / AMR      | <--- PLC / Controller (Modbus TCP Client)
+-----------+-----------+ 
            │ 
            ▼ Port 502
+-----------------------+
|  Lift Master Server   | <--- (Runs lift_single_ui.py)
+-----------+-----------+
            │ 
            ▼ Port 1502
+-----------------------+
|     Lift Station      | <--- (Arduino UNO Q - lift.ino)
+-----------------------+
```

---

## ✨ Features

### Master Server
- **Auto-Discovery**: Automatically scans and connects to lift stations defined in `lift_config.json`.
- **Robot Sync**: Bi-directional synchronization with Robot PLC via Modbus registers.
- **Web Admin Panel**: JSON-based configuration editor with Hot-Reload support.
- **Self-Healing**: Automatically restarts disconnected lift stations via SSH if communication fails.
- **OTA Updates**: Built-in `update.py` for downloading and applying patches from a central server.

### Lift Station
- **Safety Logic**: Press-lock protection (5s) and solenoid auto-release (800ms).
- **RGB Feedback**: WS2812B LED strip for status (Moving, Arrived, Error).
- **Door Monitoring**: Real-time door status reporting.
- **Real-time OS**: Built on Zephyr RTOS for deterministic I/O handling.

---

## 📁 Project Structure

```text
/
├── arduino_uno_q/          # Lift Station (Slave)
│   ├── arduino_code/       # MCU Firmware (Zephyr/C++)
│   ├── main.py             # MPU Controller (Python/Flask/Modbus)
│   └── config.json         # Station settings
├── lift_server/            # Master Server
│   ├── lift_single_ui.py   # Master logic with Web UI
│   ├── lift_config.json    # System-wide configuration
│   └── update.py           # OTA Update tool
└── README.md
```

---

## 🛠️ Installation & Execution

### 🐧 Linux (Ubuntu)
1. Install dependencies:
   ```bash
   pip install pyModbusTCP Flask requests
   ```
2. Run the master server:
   ```bash
   python3 lift_server/lift_single_ui.py
   ```

### 🪟 Windows
1. **Option A: Python Script** (Same as Linux)
   - Install Python 3.x and dependencies.
   - Run `python lift_server/lift_single_ui.py`.
2. **Option B: Executable (.exe)**
   - Use the pre-compiled `lift_single_ui.exe` (if available).
   - This version is portable and does not require Python to be installed.
   - The `update.py` tool will automatically manage and restart the `.exe` process during updates.

---

## 🔄 OTA Updates

The `update.py` script is cross-platform and handles updates differently based on the OS:

- **On Windows**: It manages `lift_single_ui.exe` (terminates and restarts the process).
- **On Linux**: It manages the `lift_single_ui.py` script via `pkill`.

To check for updates:
```bash
python3 lift_server/update.py
```

---

## 📝 License
MIT License
