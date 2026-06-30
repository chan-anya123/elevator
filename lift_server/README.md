# Lift Master Server API Reference

This document provides a comprehensive API reference for the Lift Master Server, implemented in [lift_single_ui.py](lift_single_ui.py). The server runs a Flask web application on port `5000` to serve the configuration dashboard, report status, manage update packages, and expose logs.

---

## 🛠️ Endpoints Overview

| Method | Endpoint | Description | Mime-Type |
| :--- | :--- | :--- | :--- |
| **GET** | `/` | Redirects directly to the Configuration Dashboard (`/admin`). | `text/html` |
| **GET** | `/status` | Retrieves current system status, server version, and lift states. | `application/json` |
| **GET** | `/get_lift_config` | Returns the current configuration (or defaults if missing). | `application/json` |
| **GET** | `/download_log` | Safely downloads the system log file (`lift_server.log`) as an attachment. | `application/octet-stream` |
| **GET** | `/stream_logs_live` | Initiates a real-time Server-Sent Events (SSE) live log stream. | `text/event-stream` |
| **POST** | `/upload_patch` | Receives and extracts a system update `patch.zip` file. | `application/json` |
| **GET/POST** | `/admin` | Renders the config editor, log downloader, and system update controls. | `text/html` |

---

## 📡 Detailed Endpoint Specifications

### 1. Root Redirect
* **Method**: `GET`
* **Path**: `/`
* **Description**: Automatically redirects the user's browser to the management dashboard.
* **Response**: `302 Found` or HTML redirect snippet:
  ```html
  <script>window.location.href="/admin";</script>
  ```

---

### 2. Board Status (`/status`)
* **Method**: `GET`
* **Path**: `/status`
* **Description**: Queries the master server status and the detailed telemetry of each configured lift.
* **Success Response (200 OK)**:
  ```json
  {
      "status": "online",
      "version": "1.0.1",
      "lifts": {
          "1A": {
              "busy": false,
              "door": "CLOSED",
              "floor": 1
          },
          "1B": {
              "busy": false,
              "door": "CLOSED",
              "floor": 2
          }
      }
  }
  ```
* **Error Response (503 Service Unavailable)**: Returned if the system is still starting up:
  ```json
  {
      "status": "master_starting"
  }
  ```

---

### 3. Get Configuration (`/get_lift_config`)
* **Method**: `GET`
* **Path**: `/get_lift_config`
* **Description**: Reads the configurations from `lift_config.json`. If this file is missing, it falls back to the hardcoded default configurations.
* **Success Response (200 OK)**:
  ```json
  {
      "robot_ip": "192.168.10.5",
      "settings": {
          "brightness": 100,
          "max_timeout": 20,
          "robot_dir_registers": [8, 18, 28, 38]
      },
      "lifts": [
          {"ip": "192.168.20.70", "reg_offset": 0, "comment": "Lift A1"},
          {"ip": "192.168.20.66", "reg_offset": 10, "comment": "Lift B1"}
      ]
  }
  ```

---

### 4. Download Log File (`/download_log`)
* **Method**: `GET`
* **Path**: `/download_log`
* **Description**: Downloads the master system log file `lift_server.log`. To prevent Windows file sharing locks (especially when frozen as a `.exe`), it creates a temporary copy `lift_server_download.log` which is cleaned up immediately after transmission.
* **Success Response (200 OK)**: File stream with attachment header.
* **Error Responses**:
  * `404 Not Found`: Log file does not exist.
  * `500 Internal Server Error`: Access control error on Windows.
    ```json
    {
        "status": "error",
        "message": "Windows File Access Error: [Error Details]"
    }
    ```

---

### 5. Live Log Stream (`/stream_logs_live`)
* **Method**: `GET`
* **Path**: `/stream_logs_live`
* **Description**: Establishes a persistent Server-Sent Events (SSE) pipe to tail the logs in real time.
* **Headers Required**:
  ```http
  Connection: keep-alive
  Cache-Control: no-cache, must-revalidate
  X-Accel-Buffering: no
  Content-Type: text/event-stream
  ```
* **Event Format**:
  ```text
  data: --- Connected to Live Log Stream ---

  data: [2026-06-30 09:28:00] MISSION START: A -> Floor 1

  data: [2026-06-30 09:28:05] ----- A Arrived at Floor 1 -----
  ```

---

### 6. Upload System Patch (`/upload_patch`)
* **Method**: `POST`
* **Path**: `/upload_patch`
* **Description**: Uploads a zip file named `patch.zip` to upgrade files (scripts, configurations, or executables). Once uploaded, the server spawns a separate thread to run the `update.py` script (or `update.exe` on Windows) which terminates the current process, overwrites source files, and boots up the new version within 2 seconds.
* **Payload**: `multipart/form-data`
  * `file`: (Required binary `.zip` file)
* **Success Response (200 OK)**:
  ```json
  {
      "status": "success",
      "message": "Upload successful! The system will restart and update in 2 seconds. Please refresh the page later."
  }
  ```
* **Error Responses (400 Bad Request)**:
  * Missing form part: `{"status": "error", "message": "No file part"}`
  * Unselected file: `{"status": "error", "message": "No selected file"}`
  * Invalid extension: `{"status": "error", "message": "Invalid file type. Please upload a .zip file."}`

---

### 7. Config Dashboard / Admin Panel (`/admin`)
* **Method**: `GET` / `POST`
* **Path**: `/admin`
* **Description**: Renders a browser-based user interface to check versioning, edit configurations, download logs, and upload updates.
* **GET Behavior**: Displays the config editor form populated with active configs.
* **POST Behavior**:
  * Form Parameter: `json_data` containing the raw JSON configuration string.
  * Validation: Verifies presence of keys `robot_ip`, `lifts`, and `settings`.
  * Hot-Reload: Writes the configuration to `lift_config.json` and immediately applies it to the active master system node without process restart.
* **Response**: `200 OK` (HTML markup with success/error status messages).

---

## ⚙️ Configuration File Structure (`lift_config.json`)

The config file loaded by `/admin` and `/get_lift_config` supports hot-reloads and must strictly conform to the following schema:

```json
{
    "robot_ip": "192.168.10.5",
    "settings": {
        "brightness": 100,
        "max_timeout": 20, 
        "robot_dir_registers": [8, 18, 28, 38]
    },
    "lifts": [
        {
            "ip": "192.168.20.70",
            "reg_offset": 0,
            "comment": "Lift A1"
        },
        {
            "ip": "192.168.20.66",
            "reg_offset": 10,
            "comment": "Lift B1"
        }
    ]
}
```

### Parameter Explanations
* `robot_ip`: IP of the Modbus Robot Controller / PLC.
* `settings.brightness`: Brightness percentage value (0-100) applied to the Lift Station LEDs.
* `settings.max_timeout`: The timeout in seconds before a door-pulsing mission aborts with an error state.
* `settings.robot_dir_registers`: An array of Modbus registers watched by the master server to detect robot entry/exit direction activity.
* `lifts`: An array containing target lift station IP configurations, modbus register offsets, and comment metadata.
