#!/bin/bash
# ==============================================================================
# Arduino UNO Q Elevator Controller - Deployment & Firmware Flashing Tool (v2)
# ==============================================================================

set -e

# --- ANSI Color Codes ---
CLR_RESET="\033[0m"
CLR_INFO="\033[1;34m"
CLR_SUCCESS="\033[1;32m"
CLR_WARN="\033[1;33m"
CLR_ERR="\033[1;31m"
CLR_CYAN="\033[1;36m"

# --- Default Configurations ---
DEFAULT_IP="192.168.20.49"
TARGET_USER="arduino"
TARGET_DIR="/home/arduino/lift"
FQBN="arduino:zephyr:unoq"

TARGET_IP=""
SKIP_MCU=false
SKIP_PY=false
OVERWRITE_CONFIG=false
FIX_OFFLINE_WIFI=false

# --- Usage Helper ---
show_help() {
    echo -e "${CLR_CYAN}Arduino UNO Q Update Tool v2${CLR_RESET}"
    echo "Usage: $0 [TARGET_IP] [OPTIONS]"
    echo ""
    echo "Arguments:"
    echo "  TARGET_IP               Target Linux board IP address (Default: $DEFAULT_IP)"
    echo ""
    echo "Options:"
    echo "  --skip-mcu              Skip Arduino MCU compilation & flashing (Fast Python update)"
    echo "  --skip-py               Skip Python files and Web UI sync (Flash MCU only)"
    echo "  --config                Force overwrite 'lift_config.json' on target"
    echo "  --fix-wifi              Apply NetworkManager offline Wi-Fi / connectivity fix"
    echo "  -h, --help              Show this help menu"
    echo ""
    echo "Examples:"
    echo "  $0 192.168.20.53"
    echo "  $0 192.168.20.53 --skip-mcu"
    echo "  $0 192.168.20.53 --fix-wifi"
    exit 0
}

# --- Parse Arguments ---
for arg in "$@"; do
    case $arg in
        --skip-mcu)
            SKIP_MCU=true
            ;;
        --skip-py)
            SKIP_PY=true
            ;;
        --config)
            OVERWRITE_CONFIG=true
            ;;
        --fix-wifi)
            FIX_OFFLINE_WIFI=true
            ;;
        -h|--help)
            show_help
            ;;
        *)
            if [ -z "$TARGET_IP" ]; then
                TARGET_IP="$arg"
            fi
            ;;
    esac
done

if [ -z "$TARGET_IP" ]; then
    TARGET_IP="$DEFAULT_IP"
fi

echo -e "${CLR_INFO}============================================================${CLR_RESET}"
echo -e "${CLR_INFO}🚀 Target Board       :${CLR_RESET} $TARGET_USER@$TARGET_IP"
echo -e "${CLR_INFO}📂 Target Directory   :${CLR_RESET} $TARGET_DIR"
echo -e "${CLR_INFO}⚙️  Update Python/Web  :${CLR_RESET} $([ "$SKIP_PY" = true ] && echo "SKIP" || echo "YES")"
echo -e "${CLR_INFO}⚡ Update Arduino MCU :${CLR_RESET} $([ "$SKIP_MCU" = true ] && echo "SKIP" || echo "YES")"
echo -e "${CLR_INFO}💾 Overwrite Config   :${CLR_RESET} $([ "$OVERWRITE_CONFIG" = true ] && echo "YES" || echo "PRESERVE REMOTE")"
echo -e "${CLR_INFO}🌐 Offline Wi-Fi Fix  :${CLR_RESET} $([ "$FIX_OFFLINE_WIFI" = true ] && echo "YES" || echo "NO")"
echo -e "${CLR_INFO}============================================================${CLR_RESET}"

# --- Step 1: Pre-flight SSH Connectivity Check ---
echo -e "\n${CLR_INFO}[1/5] Testing SSH connection to $TARGET_IP...${CLR_RESET}"
if ! ssh -o ConnectTimeout=5 -o BatchMode=no "$TARGET_USER@$TARGET_IP" "echo 'SSH Connection OK'" > /dev/null 2>&1; then
    echo -e "${CLR_ERR}❌ Cannot connect to $TARGET_USER@$TARGET_IP via SSH.${CLR_RESET}"
    echo -e "${CLR_WARN}Please verify IP address, network connection, or Wi-Fi status.${CLR_RESET}"
    exit 1
fi
echo -e "${CLR_SUCCESS}✅ SSH Connection established successfully.${CLR_RESET}"

# Create remote directory
ssh "$TARGET_USER@$TARGET_IP" "mkdir -p $TARGET_DIR"

# --- Step 2: Sync Python / Web Files ---
if [ "$SKIP_PY" = false ]; then
    echo -e "\n${CLR_INFO}[2/5] Syncing application files (main.py, templates, firmware sketch)...${CLR_RESET}"
    
    # Backup remote config before sync
    ssh "$TARGET_USER@$TARGET_IP" "if [ -f $TARGET_DIR/lift_config.json ]; then cp $TARGET_DIR/lift_config.json $TARGET_DIR/lift_config.json.bak; fi"
    
    SYNC_FILES="arduino_code main.py templates"
    if [ "$OVERWRITE_CONFIG" = true ]; then
        SYNC_FILES="$SYNC_FILES lift_config.json"
    else
        # Copy lift_config.json only if it doesn't already exist on target
        if ! ssh "$TARGET_USER@$TARGET_IP" "test -f $TARGET_DIR/lift_config.json"; then
            SYNC_FILES="$SYNC_FILES lift_config.json"
        else
            echo -e "${CLR_WARN}ℹ️  Preserving existing 'lift_config.json' on target board (backup saved to .bak).${CLR_RESET}"
        fi
    fi

    if ssh "$TARGET_USER@$TARGET_IP" "hash rsync 2>/dev/null"; then
        rsync -avz --exclude '__pycache__' --exclude '*.pyc' $SYNC_FILES "$TARGET_USER@$TARGET_IP:$TARGET_DIR"
    else
        echo -e "${CLR_WARN}rsync not available on target; using tar stream...${CLR_RESET}"
        tar --exclude='__pycache__' --exclude='*.pyc' -czf - $SYNC_FILES | ssh "$TARGET_USER@$TARGET_IP" "tar -xzf - -C $TARGET_DIR"
    fi

    # Sync main.py to main.py so systemd service runs the updated code
    ssh "$TARGET_USER@$TARGET_IP" "cp $TARGET_DIR/main.py $TARGET_DIR/main.py"
    echo -e "${CLR_SUCCESS}✅ Application files synced (main.py -> main.py).${CLR_RESET}"
else
    echo -e "\n${CLR_WARN}[2/5] Skipping Python and UI files sync (--skip-py).${CLR_RESET}"
fi

# --- Step 3: NetworkManager Configuration & Dynamic Hotspot Setup ---
echo -e "\n${CLR_INFO}[3/5] Verifying Hotspot SSID ('NextElevator_<MAC>') and Network Configuration...${CLR_RESET}"
ssh "$TARGET_USER@$TARGET_IP" "bash -s" << 'EOF'
    # 1. Configure Hotspot with NextElevator_<MAC>
    MAC_HEX=$(cat /sys/class/net/wl*/address /sys/class/net/wlan*/address /sys/class/net/en*/address /sys/class/net/eth*/address 2>/dev/null | tr -d ':\n ' | tr '[:lower:]' '[:upper:]' | head -c 12)
    if [ -n "$MAC_HEX" ] && [ "$MAC_HEX" != "000000000000" ]; then
        HOTSPOT_SSID="NextElevator_${MAC_HEX}"
        if nmcli -t -f NAME con show | grep -Fx "MyLiftHotspot" > /dev/null 2>&1; then
            nmcli con mod "MyLiftHotspot" 802-11-wireless.ssid "$HOTSPOT_SSID" connection.autoconnect-priority 0 > /dev/null 2>&1 || true
        else
            nmcli con add type wifi ifname wlan0 con-name "MyLiftHotspot" autoconnect no ssid "$HOTSPOT_SSID" 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared 802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk 12345678 connection.autoconnect-priority 0 > /dev/null 2>&1 || true
        fi
        echo "   -> Hotspot SSID configured to: $HOTSPOT_SSID (Profile: MyLiftHotspot, Key: 12345678)"
    fi
EOF
echo -e "${CLR_SUCCESS}✅ Dynamic Hotspot configured on target board.${CLR_RESET}"

if [ "$FIX_OFFLINE_WIFI" = true ]; then
    echo -e "${CLR_INFO}Applying NetworkManager offline connectivity check fix...${CLR_RESET}"
    ssh -t "$TARGET_USER@$TARGET_IP" "sudo bash -c 'echo -e \"[connectivity]\nenabled=false\" > /etc/NetworkManager/conf.d/20-disable-connectivity.conf && systemctl restart NetworkManager'" || true
    echo -e "${CLR_SUCCESS}✅ NetworkManager connectivity checking disabled on target board.${CLR_RESET}"
fi

# --- Step 4: Arduino MCU Firmware Compile & Upload ---
if [ "$SKIP_MCU" = false ]; then
    echo -e "\n${CLR_INFO}[4/5] Compiling and flashing Arduino MCU firmware on target board...${CLR_RESET}"
    
    # Copy sketch files to temporary build directory
    ssh "$TARGET_USER@$TARGET_IP" "mkdir -p /tmp/lift && cp -r $TARGET_DIR/arduino_code/* /tmp/lift/"
    
    # Run compilation and flashing on target
    ssh "$TARGET_USER@$TARGET_IP" "TARGET_IP_ARG='$TARGET_IP' bash -s" << 'EOF'
        set -e
        FQBN="arduino:zephyr:unoq"

        # Check / Install Arduino Router Bridge Library
        echo "[*] Ensuring Arduino_RouterBridge library is installed..."
        arduino-cli lib install Arduino_RouterBridge > /dev/null 2>&1 || true

        # Compile Sketch
        echo "[*] Compiling firmware for $FQBN..."
        arduino-cli compile --fqbn $FQBN /tmp/lift
        echo "✅ Firmware compilation successful!"

        # Identify Upload Port: Prioritize target IPv4, then local loopback, then discovered non-IPv6 ports
        DISCOVERED_PORTS=$(arduino-cli board list 2>/dev/null | grep -E 'Arduino UNO Q|unoq' | awk '{print $1}' | grep -v ':' | grep -v '%' || true)
        PORTS="$TARGET_IP_ARG 127.0.0.1 $DISCOVERED_PORTS"

        UPLOAD_SUCCESS=false
        for PORT in $PORTS; do
            # Skip empty or IPv6 link-local ports
            if [ -z "$PORT" ] || [[ "$PORT" == *":"* ]] || [[ "$PORT" == *"%"* ]]; then
                continue
            fi

            echo "[*] Attempting firmware upload to port: $PORT..."
            if arduino-cli upload -p "$PORT" --fqbn "$FQBN" /tmp/lift; then
                echo "✅ MCU Flash upload successful via port: $PORT"
                UPLOAD_SUCCESS=true
                break
            fi
        done

        if [ "$UPLOAD_SUCCESS" = false ]; then
            echo "❌ Failed to flash firmware to any available port!"
            exit 1
        fi
EOF
    echo -e "${CLR_SUCCESS}✅ MCU Firmware flashed successfully.${CLR_RESET}"
else
    echo -e "\n${CLR_WARN}[4/5] Skipping MCU compilation and flashing (--skip-mcu).${CLR_RESET}"
fi

# --- Step 5: Restart Services & Verify Health ---
echo -e "\n${CLR_INFO}[5/5] Restarting services on target board...${CLR_RESET}"
ssh -t "$TARGET_USER@$TARGET_IP" "sudo systemctl restart arduino-router.service lift-service.service"

echo -e "\n${CLR_INFO}Verifying service status...${CLR_RESET}"
sleep 2

STATUS_REPORT=$(ssh "$TARGET_USER@$TARGET_IP" "
    ROUTER_STAT=\$(systemctl is-active arduino-router.service 2>/dev/null || echo 'inactive')
    LIFT_STAT=\$(systemctl is-active lift-service.service 2>/dev/null || echo 'inactive')
    echo \"arduino-router: \$ROUTER_STAT | lift-service: \$LIFT_STAT\"
")

echo -e "${CLR_SUCCESS}============================================================${CLR_RESET}"
echo -e "${CLR_SUCCESS}🎉 Deployment Completed Successfully!${CLR_RESET}"
echo -e "${CLR_INFO}📊 Service Status :${CLR_RESET} $STATUS_REPORT"
echo -e "${CLR_INFO}🌐 Web Dashboard  :${CLR_RESET} http://$TARGET_IP:5000"
echo -e "${CLR_SUCCESS}============================================================${CLR_RESET}"