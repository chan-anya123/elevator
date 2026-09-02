# Elevator Control System (Industrial Robotics & Automation)

A robust, multi-architecture elevator control and monitoring platform designed for industrial automation and Autonomous Mobile Robot (AMR / AGV) integration. This repository supports Modbus TCP, Node-RED, and the **Arduino UNO Q** dual-processor platform (Linux MPU + Zephyr RTOS MCU).

The codebase is organized into **three distinct deployment architectures** depending on system requirements and hardware topology:

---

## 📁 Repository Structure & Architectural Overview

```text
/home/cookies/lift/
├── arduino_uno_q/    # [Architecture 1] Floor station controller (Works with lift_server)
├── lift_server/      # [Architecture 1] Centralized master orchestrator (Works with arduino_uno_q)
├── lift_tcp/         # [Architecture 2] Standalone all-in-one controller (Runs directly on Arduino UNO Q)
├── wave_share/       # [Architecture 3] Node-RED automation flows & Waveshare Modbus relay configuration
├── requirements.txt  # Shared Python dependencies
└── README.md         # System documentation (This file)
```

### Architectural Comparison

| Architecture | Folder(s) | Hardware Platform | Core Function & Operational Role |
| :--- | :--- | :--- | :--- |
| **1. Master-Station System** | **`arduino_uno_q/`** + **`lift_server/`** | Central PC / Server + Arduino UNO Q per floor | **Cooperative Client-Server Architecture**: `arduino_uno_q` boards act as floor station nodes executing hardware I/O, while `lift_server` serves as the central orchestrator coordinating multi-floor / multi-lift calls with AMR robots and providing a centralized web dashboard. |
| **2. Standalone Controller** | **`lift_tcp/`** | Single Arduino UNO Q board per station | **Runs standalone directly on the Arduino UNO Q board without needing any server**: Implements a Dual-Modbus Bridge (direct connection to robot on Port 502 + local server on Port 1502), autonomous state machine, and embedded web UI on a single board. |
| **3. Node-RED Automation** | **`wave_share/`** | Waveshare Modbus TCP Relay + Node-RED Host | **Dedicated for Node-RED**: Controls elevator buttons and door sensors via an industrial Waveshare Modbus relay module, with business logic, robot synchronization, and event logging managed through Node-RED flows. |

---

## 1. Master-Station Architecture: `arduino_uno_q/` + `lift_server/`

Designed for **multi-floor buildings and multi-lift complexes** where centralized orchestration, aggregated telemetry, and multi-station health monitoring are required.

```text
                         +-----------------------------+
                         |      Robot (AGV / AMR)      |
                         +--------------+--------------+
                                        | Modbus TCP (Port 502)
                                        v
                         +-----------------------------+
                         |         lift_server         |
                         |  (Central Master Server)    |
                         |  - Orchestrates all lifts   |
                         |  - Subnet Auto-Discovery    |
                         |  - Central Admin Dashboard  |
                         +--------------+--------------+
                                        |
                 +----------------------+----------------------+
                 | Modbus TCP (1502) / REST API                | Modbus TCP (1502)
                 v                                             v
    +--------------------------+                  +--------------------------+
    |      arduino_uno_q       |                  |      arduino_uno_q       |
    | (Floor 1 Station Node)   |                  | (Floor 2 Station Node)   |
    | - Arduino UNO Q          |                  | - Arduino UNO Q          |
    | - Controls relays & LEDs |                  | - Controls relays & LEDs |
    +--------------------------+                  +--------------------------+
```

### 1.1 `arduino_uno_q/` (Station Node Controller)
Deployed on an **Arduino UNO Q** board installed at each floor station.
- **Key Responsibilities**:
  - Interacts directly with elevator hardware: call button solenoids (UP/DOWN), physical button LEDs, door sensors (Reed switches/proximity), and WS2812B RGB strip.
  - Hosts a **Modbus TCP Server on Port 1502** allowing `lift_server` to poll floor/door status, read heartbeat signals, and issue actuation commands.
  - Runs a **MessagePack RPC Client** over `/var/run/arduino-router.sock` to bridge Linux Python processes with the real-time MCU Zephyr firmware.
  - Includes a localized web configuration interface for testing relays and configuring station Wi-Fi.
- **Key Files**:
  - [`main.py`](arduino_uno_q/main.py): Primary station service (Modbus Server 1502 + Flask REST API).
  - [`arduino_code/lift.ino`](arduino_uno_q/arduino_code/lift.ino): Zephyr RTOS C++ firmware for real-time deterministic pin I/O and safety debouncing.
  - [`config.json`](arduino_uno_q/config.json): Floor identifier (`floor_name`), lift ID (`lift_id`), and network configuration.
  - [`update_mcu.sh`](arduino_uno_q/update_mcu.sh): Remote build and flash deployment script via SSH.

### 1.2 `lift_server/` (Central Master Orchestrator)
Runs on a central host machine, Mini PC, or Industrial PC.
- **Key Responsibilities**:
  - **Robot AMR/AGV Bridge**: Connects to the robot PLC via Modbus TCP Port 502 to receive target floor calls (`call_target`) and report lift availability.
  - **Station Lifecycle Management**:
    - Periodically scans subnets for new or reconnected `arduino_uno_q` station nodes.
    - Monitors station heartbeats; flags nodes as offline if heartbeats stall for >10 seconds.
    - **SSH Self-Healing**: Automatically triggers remote service restarts on stations when communication faults are detected.
  - **Centralized Web Admin Panel (Port 5000)**:
    - Real-time aggregated view of all elevators and floors.
    - Station IP mapping and register offset configuration editor (`lift_config.json`).
    - Live streaming log viewer (Server-Sent Events) and historical log downloads.
    - **OTA Patch Updater**: Allows uploading `patch.zip` to upgrade the server without manual shell intervention.
- **Key Files**:
  - [`lift_single_ui.py`](lift_server/lift_single_ui.py): Master server core engine with web dashboard.
  - [`lift_config.json`](lift_server/lift_config.json): Central station registry mapping IPs, offsets, and robot parameters.
  - [`update.py`](lift_server/update.py): Automated patch extractor and hot-restart utility.

---

## 2. Standalone Architecture: `lift_tcp/` (Plug & Play on Arduino UNO Q)

Engineered for **maximum simplicity and self-reliance**. This architecture runs **completely standalone on a single Arduino UNO Q board without requiring `lift_server`**.

```text
               +---------------------------------------------------+
               |            Robot / AMR PLC / Controller           |
               +-------------------------+-------------------------+
                                         |
                                         | Modbus TCP (Port 502)
                                         v
+---------------------------------------------------------------------------------+
| Arduino UNO Q Board (Linux MPU + Zephyr MCU) - lift_tcp/                        |
|                                                                                 |
|  [Modbus Client (Port 502)]  <---> Directly polls robot mission calls           |
|  [Modbus Server (Port 1502)] <---> Exposes local registers to SCADA / PLC       |
|  [Mission State Machine]     <---> Pipeline: IDLE -> MOVING -> ARRIVED -> PULSE |
|  [Web UI & REST API (5000)]  <---> Glassmorphism responsive dashboard           |
|  [mDNS & Recovery Hotspot]   <---> Access via http://lift<mac>.local:5000       |
|                                                                                 |
|  [MCU Firmware Layer (STM32)]<---> Drives solenoids, senses door, WS2812B LEDs  |
+---------------------------------------------------------------------------------+
```

### Key Advantages of `lift_tcp/`:
1. **Zero External Server Dependency (All-in-One)**:
   - The entire elevator controller, mission sequencer, safety watchdog, web interface, and hardware firmware reside on a single Arduino UNO Q board.
2. **Integrated Dual-Modbus Bridge**:
   - **Modbus Client (Port 502)**: Connects directly to `robot_ip:502`, monitors `call_target`, and synchronizes floor/door telemetry upon state changes.
   - **Modbus Server (Port 1502)**: Runs concurrently on `0.0.0.0:1502` to accept secondary commands from local automation or SCADA.
3. **Autonomous Mission State Machine & 60s Watchdog**:
   - Executes the complete floor call cycle: Solenoid actuation $\rightarrow$ Door open verification $\rightarrow$ Signal robot $\rightarrow$ Multi-pulse door-hold sequence $\rightarrow$ Reset.
   - If an elevator cycle becomes stalled for >60 seconds, the built-in watchdog automatically aborts the mission and resets the state to `IDLE`.
4. **WS2812B RGB Feedback**:
   - Visual status cues: **BLUE** = Ready/Idle, **GREEN** = Calling/Moving, **PINK** = Door Open/Holding, **RED** = Error/Fault.
5. **Zero-Configuration Networking**:
   - Automatic mDNS broadcast (`http://lift<last4_mac>.local:5000`).
   - Dynamic emergency Wi-Fi hotspot (`NextElevator_<MAC>`, password: `12345678`) if network connectivity fails.

### Key Files in `lift_tcp/`:
- [`main.py`](lift_tcp/main.py): Unified controller engine combining Modbus bridge, state machine, REST API, and network manager.
- [`arduino_code/lift.ino`](lift_tcp/arduino_code/lift.ino): MCU Zephyr RTOS firmware for deterministic I/O.
- [`arduino_code/ws2812b-bitbang.h`](lift_tcp/arduino_code/ws2812b-bitbang.h): Bit-banging driver for WS2812B RGB LEDs.
- [`templates/ui.html`](lift_tcp/templates/ui.html): Responsive glassmorphism web console.
- [`lift_config.json`](lift_tcp/lift_config.json): Floor name, lift ID, robot IP, and register mapping settings.
- [`update_mcu.sh`](lift_tcp/update_mcu.sh): One-click deployment script with SWD direct upload, automatic venv dependency resolution, and non-blocking fallback.
- [`MANUAL.md`](lift_tcp/MANUAL.md): Comprehensive technical specification and register reference for `lift_tcp`.

### Deployment & Flashing:
```bash
# Full deployment: sync Python/UI + compile & flash MCU firmware via SWD
/home/cookies/lift/lift_tcp/update_mcu.sh 192.168.20.49

# Fast update: sync Python and Web templates only (skip MCU flashing)
/home/cookies/lift/lift_tcp/update_mcu.sh 192.168.20.49 --skip-mcu
```

---

## 3. Node-RED Automation: `wave_share/` (For Node-RED Deployments)

Created for environments using **Node-RED** as the orchestration middleware along with industrial **Waveshare Modbus TCP Relay modules** instead of Arduino UNO Q hardware.

```text
+-----------------------+     Modbus TCP (Port 502)     +-----------------------+
|   Robot (AGV / AMR)   | <---------------------------> |       Node-RED        |
+-----------------------+                               |  (Runs flows_new.json)|
                                                        +-----------+-----------+
                                                                    | Modbus TCP (4196)
                                                                    v
                                                        +-----------------------+
                                                        | Waveshare Modbus      |
                                                        | Relay Controller      |
                                                        | - DI: Door Sensors    |
                                                        | - DO: Solenoid Relays |
                                                        +-----------------------+
```

### Responsibilities of `wave_share/`:
- **Hardware Interface**: Communicates with a Waveshare Modbus Relay box over Ethernet (default: `192.168.1.200:4196`).
  - **Digital Inputs (DI)**: Physical floor buttons and elevator door limit sensors.
  - **Digital Outputs (DO)**: UP/DOWN solenoid actuators and directional indicator lamps.
- **Node-RED Integration**:
  - Ready-to-import flow handles elevator scheduling, robot call synchronization, and door sequence timers.
  - Logs door events, physical button presses, and communication diagnostics.
- **Key Files**:
  - [`flows_new.json`](wave_share/flows_new.json): Complete Node-RED flow configuration for immediate import.
  - [`config.json`](wave_share/config.json): Modbus register and coil mapping definitions for the Waveshare device.
  - [`daily_elevator_log.csv`](wave_share/daily_elevator_log.csv): Operational audit log format recording door transitions, manual calls, and faults.

---

## 🧭 Decision Guide: Which Architecture to Use?

| Use Case / Scenario | Recommended Solution | Rationale |
| :--- | :--- | :--- |
| **Standalone setup on a single Arduino board with minimal infrastructure** | 👉 **`lift_tcp/`** | Runs directly on Arduino UNO Q without requiring an external server. Connects straight to the robot, features an autonomous state machine, and provides a built-in web dashboard. |
| **Multi-lift or multi-floor facility requiring centralized monitoring and control** | 👉 **`arduino_uno_q/`** + **`lift_server/`** | `arduino_uno_q` operates as dedicated floor station nodes while `lift_server` provides centralized aggregation, heartbeat health checks, auto-discovery, and self-healing. |
| **Facility utilizing existing Node-RED infrastructure or industrial relay boxes** | 👉 **`wave_share/`** | Import `flows_new.json` directly into Node-RED to command Waveshare Modbus TCP relay hardware without writing custom microcontroller code. |

---

## 🧰 Arduino UNO Q Hardware Specifications & Pin Mapping

When using either `lift_tcp/` or `arduino_uno_q/`, the physical I/O connections on the Arduino UNO Q board are defined as follows:

| Pin | Type | Function | Notes |
| :---: | :---: | :--- | :--- |
| **A1** | Digital Input | Door Sensor (Standard Floors) | Active LOW (Pull-up). Circuit closed = Door Open. |
| **A4** | Digital Input | Door Sensor (Basement B1) | Dedicated door sensor input for basement floor B1. |
| **A2** | Digital Input | UP Elevator Button | Manual floor call button input. |
| **A3** | Digital Input | DOWN Elevator Button | Manual floor call button input. |
| **D11** | Digital Output | UP Solenoid Relay | Actuates physical elevator UP call button. |
| **D7** | Digital Output | DOWN Solenoid Relay | Actuates physical elevator DOWN call button. |
| **D8** | Digital Output | UP Button Indicator LED | Illuminates when UP call is active. |
| **D12** | Digital Output | DOWN Button Indicator LED | Illuminates when DOWN call is active. |
| **D6** | Digital Output | WS2812B RGB Data Line | 16-LED addressable NeoPixel strip indicating system state. |

### Built-in Hardware Safety Parameters
- **Press Lock Protection**: Rapid button presses are locked and debounced for **5,000 ms**.
- **Solenoid Auto-Release**: Actuator outputs automatically disengage after **800 ms** to protect physical coils against overheating.
