#!/bin/bash

# ==============================================================================
# Arduino UNO Q - MCU Firmware Update Script
# This script automates compiling and flashing the Arduino code from the MPU.
# ==============================================================================

# Ensure the script is run with sudo
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script with sudo:"
  echo "sudo ./update_mcu.sh"
  exit 1
fi

echo "=== Starting MCU Firmware Update ==="

# Stop the lift service to free up the serial port
echo "[*] Stopping lift-service..."
systemctl stop lift-service.service
sleep 2

# Directory containing the Arduino code
ARDUINO_DIR="/home/arduino/lift/arduino_uno_q/arduino_code"
if [ ! -d "$ARDUINO_DIR" ]; then
    # Fallback to current directory if not in the expected path
    ARDUINO_DIR="$(pwd)/arduino_code"
fi

if [ ! -f "$ARDUINO_DIR/lift.ino" ]; then
    echo "[!] Error: Could not find lift.ino in $ARDUINO_DIR"
    echo "[*] Restarting lift-service..."
    systemctl start lift-service.service
    exit 1
fi

echo "[*] Found firmware source at: $ARDUINO_DIR"

# Flag to check if we actually need to flash the Arduino
NEEDS_FLASH=false

# Check if there are new INO/CPP files (simple modification time check could be added, but for now we compile if they exist)
if ls "$ARDUINO_DIR"/*.ino 1> /dev/null 2>&1; then
    NEEDS_FLASH=true
fi

if [ "$NEEDS_FLASH" = true ]; then
    # Verify arduino-cli is installed
    if ! command -v arduino-cli &> /dev/null; then
        echo "[!] Error: arduino-cli is not installed."
        echo "    Please install it first: curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh"
        echo "[*] Restarting lift-service..."
        systemctl start lift-service.service
        exit 1
    fi

    # Define the FQBN (Fully Qualified Board Name) for Arduino UNO R4/Q
    FQBN="arduino:renesas_uno:unor4wifi"
    PORT="/dev/ttyACM0"

    echo "[*] Compiling firmware ($FQBN)..."
    arduino-cli compile --fqbn "$FQBN" "$ARDUINO_DIR"
    if [ $? -ne 0 ]; then
        echo "[!] Compilation failed!"
        echo "[*] Restarting lift-service..."
        systemctl start lift-service.service
        exit 1
    fi

    echo "[*] Uploading firmware to $PORT..."
    arduino-cli upload -p "$PORT" --fqbn "$FQBN" "$ARDUINO_DIR"
    if [ $? -ne 0 ]; then
        echo "[!] Upload failed! Please check if the Arduino is connected to $PORT."
        echo "[*] Restarting lift-service..."
        systemctl start lift-service.service
        exit 1
    fi

    echo "[🎉] MCU Firmware update successful!"
else
    echo "[*] No Arduino firmware changes detected. Skipping MCU flash."
fi

# Apply any MPU updates (main.py, templates) by restarting the service
echo "[*] Applying MPU changes (Python/HTML) and starting lift-service..."
systemctl start lift-service.service

echo "=== Update Process Complete ==="
