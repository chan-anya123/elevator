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
|  Central Cloud / Web  | <--- OTA Update (patch.zip / version.txt)
+-----------+-----------+
            │
            ▼
+-----------------------+
|      Robot / AMR      | <--- Controller (Modbus TCP Client)
+-----------+-----------+ 
            │ 
            ▼ Port 502
+-----------------------+
|  Lift Master Server   | <--- (Runs lift_single_ui)
+-----------+-----------+
            │ 
            ▼ Port 1502
+-----------------------+
|     Lift Station      | <--- (Arduino UNO Q)
+-----------------------+
```

---

## ✨ Features

### Master Server
- **Auto-Discovery**: Automatically scans and connects to lift stations defined in `lift_config.json`.
- **Robot Sync**: Bi-directional synchronization with Robot PLC via Modbus registers.
- **Web Management Dashboard**: Central UI for configuration and system updates.
- **Auto-Update & Restart**: Integrated update system that handles process termination, file replacement, and automatic restart.
- **Self-Healing**: Automatically restarts disconnected lift stations via SSH if communication fails.

### Lift Station
- **Safety Logic**: Press-lock protection (5s) and solenoid auto-release (800ms).
- **RGB Feedback**: WS2812B LED strip for status (Moving, Arrived, Error).
- **Door Monitoring**: Real-time door status reporting.
- **Real-time OS**: Built on Zephyr RTOS for deterministic I/O handling.
- **B1 Floor Pin Configuration**: Employs pin `A4` for the door sensor on floor B1, whereas other floors utilize `A1`.

---

## 📁 Project Structure

```text
/
├── arduino_uno_q/          # Lift Station (Slave)
│   ├── arduino_code/       # MCU Firmware (Zephyr/C++)
│   ├── main.py             # MPU Controller (Python/Flask/Modbus)
│   ├── update_mcu.sh       # Remote compilation & deployment script
│   └── config.json         # Station settings
├── lift_server/            # Master Server
│   ├── lift_single_ui.py   # Master logic with Web UI
│   ├── lift_config.json    # System-wide configuration
│   ├── update.py           # Smart Update tool
│   └── version.txt         # Current version identifier
└── README.md
```

---

## 🛠️ Installation & Execution

### 🐧 Linux (Ubuntu)
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the master server:
   ```bash
   python3 lift_server/lift_single_ui.py
   ```
3. *(Recommended)* Set up as a **systemd** service with `Restart=always` for maximum reliability.

### 🪟 Windows
1. **Executable Mode (.exe)**:
   - Run `lift_single_ui.exe`. This is portable and includes all dependencies.
   - Ensure `update.exe` is in the same folder to allow web-based updates.
2. **Python Mode**:
   - Install Python 3.x.
   - Install dependencies: `pip install -r requirements.txt`
   - Run `python lift_server/lift_single_ui.py`.

---

## 📡 API & Communication

The system provides multiple interfaces for integration with Robots, PLCs, and external monitoring tools.

### 1. REST API (Master Server - Port 5000)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/status` | Get overall system status, version, and lift data. |
| `GET` | `/get_lift_config` | Retrieve current `lift_config.json`. |
| `GET` | `/download_log` | Download the current `lift_server.log` file (safely handled via temporary files to avoid access lock issues). |
| `POST` | `/upload_patch` | Upload `patch.zip` to trigger a system update. |
| `GET` | `/admin` | Access the Web Management Dashboard. |

**Example Response (`/status`):**
```json
{
    "lifts": {
        "A": {
            "busy": false,
            "door": "CLOSED",
            "floor": 1
        },
        "B": {
            "busy": false,
            "door": "CLOSED",
            "floor": 2
        }
    },
    "status": "online",
    "version": "1.0.1"
}

```

### 2. Modbus TCP (Robot Integration - Port 502)

The Master Server acts as a Modbus Client/Server bridge to sync data with the Robot PLC. By default, **Lift A** starts at register **0**, and **Lift B** starts at register **10**.

| Address | Type | Name | Description |
|---|---|---|---|
| `offset + 0` | R | Current Floor | Real-time floor position reported by the lift. |
| `offset + 2` | R | Lift Status | 1 = Lift is at target floor and ready for robot. (Maps to `lift_status` in code) |
| `offset + 4` | R | Door Status | 1 = Open, 2 = Closed. |
| `offset + 6` | R/W | Mission Target | Write target floor number here to call the lift (A=6, B=16). |
| *Config List* | R/W | Robot Direction | Signal robot activity: **1** = Moving IN, **2** = Moving OUT, **0** = Clear. |

### 🤖 Robot Direction Handshake Logic

The Master Server employs a specific handshake logic to ensure the robot has safely entered/exited the lift before completing a mission. This system supports **Multi-Robot Synchronization** by monitoring a pool of registers.

**Handshake Steps:**
1. **Waiting for Activity**: Once the lift arrives at the target floor and doors open, the Master Server monitors all registers in the `robot_dir_registers` list (e.g., [8, 18, 28, 38]).
2. **Detection & Tracking**: 
   - When a robot writes **1** (Entering) or **2** (Exiting) to any register in the list, the Master Server begins "tracking" that specific register.
   - Multiple robots can be tracked simultaneously if they write to different registers in the list.
3. **Pulsing State**: While at least one robot is active (value 1 or 2) or being tracked, the Master Server maintains the mission active, periodically sending pulses to the lift station to keep doors open.
4. **Completion (The "All-Clear")**: The mission is only marked as **SUCCESS** after:
   - At least one robot was detected and tracked.
   - **AND** all tracked registers have returned to **0** (Clear).

*Note:*
- If no activity (1 or 2) is detected across any configured register within `max_timeout`, the mission ends with an **ERROR**.
- This logic prevents the lift from moving if one robot has finished but another is still entering/exiting.

### 3. Modbus TCP (Lift Station - Port 1502)

Each individual Lift Station (Arduino) exposes these registers for the Master Server to poll.

| Address | Access | Name | Description |
|---|---|---|---|
| 0 | R | Floor | Current Floor reported by sensors. |
| 1 | R | Heartbeat | Counter that increments every 1s. |
| 2 | R | Status | 0 = Offline, 1 = Ready. |
| 3 | R/W | Command | 0=Stop, 1=Move Up, 2=Move Down. |
| 4 | R | Door | 1 = Open, 2 = Closed. |
| 5 | R/W | LED Color | Select RGB status color (1-9). |
| 7 | R/W | LED Brightness| 0-100% brightness level. |

---

## 🛡️ Reliability & Self-Healing

The system is designed for high-availability in industrial environments:

- **Aggressive Auto-Discovery**: The Master server scans the local subnet and all configured lift subnets every 120 seconds to find new or reconnected stations.
- **Heartbeat Monitoring**: Master monitors a heartbeat counter from each station. If it freezes for >10 seconds, the station is marked offline.
- **SSH Self-Healing**: If a station goes offline, the Master attempts to automatically restart the `lift-service.service` on the station via SSH.
- **Log Rotation**: Logs are saved to `lift_server.log` with a 5MB rotation limit and 5-file history to prevent disk saturation.
- **Hot-Reload**: Configuration changes made via the Admin UI are applied instantly without restarting the Master process.

---

## 🔄 Updating the System (Web-Based)

The system supports seamless updates via the Web Dashboard. No physical access or USB drive is required.

### 1. Prepare the Update (`patch.zip`)
On your developer machine, create a ZIP file named **`patch.zip`**.
- **For Windows**: Include `lift_single_ui.exe`.
- **For Ubuntu**: Include `lift_single_ui.py` and `version.txt`.
- *Note: You can ZIP the files directly or within a folder; the Smart Update tool handles both.*

### 2. Upload via Dashboard
1. Open the Admin Panel: `http://<master-ip>:5000/admin`
2. Locate the **📦 System Update** section.
3. Select your `patch.zip` and click **Upload and Update**.

### 3. Automatic Process
The system will autonomously perform the following:
- **Upload**: Receive and save the patch file.
- **Terminate**: Safely close the running `lift_single_ui` process to unlock files.
- **Extract**: Unpack the ZIP, flatten the structure, and replace old files (protecting `lift_config.json`).
- **Restart**: Automatically launch the new version:
  - **Windows**: Restarts `lift_single_ui.exe` or `.py` as a detached process.
  - **Ubuntu**: Restarts `lift_single_ui.py` in a new session.
- **Cleanup**: Delete the `patch.zip` after a successful installation.


