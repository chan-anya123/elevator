#!/bin/bash

# --- ตั้งค่าบอร์ดปลายทาง ---
TARGET_IP="192.168.20.60"        # เปลี่ยนเป็น IP ของบอร์ด Linux ตัวใหม่
TARGET_USER="arduino"            # เปลี่ยนเป็น Username ของบอร์ดใหม่
TARGET_DIR="/home/arduino/lift"  # โฟลเดอร์ปลายทางที่ต้องการเอาไฟล์ไปวาง
FQBN="arduino:zephyr:unoq"

echo "Connecting and syncing files to $TARGET_IP..."

# สร้างโฟลเดอร์ปลายทางเผื่อไว้ (ถ้ายังไม่มี)
ssh $TARGET_USER@$TARGET_IP "mkdir -p $TARGET_DIR"

# เริ่มโยนไฟล์ (ข้ามโฟลเดอร์ที่ไม่จำเป็นต้องรันบนฝั่ง Python เพื่อความรวดเร็ว)
if ssh $TARGET_USER@$TARGET_IP "hash rsync 2>/dev/null"; then
    rsync -avz --exclude '__pycache__' arduino_code main.py templates $TARGET_USER@$TARGET_IP:$TARGET_DIR
else
    echo "rsync not found on the target board. The system will compress and send files using tar instead..."
    tar --exclude='__pycache__' -czf - arduino_code main.py templates | ssh $TARGET_USER@$TARGET_IP "mkdir -p $TARGET_DIR && tar -xzf - -C $TARGET_DIR"
fi

echo "Files uploaded successfully! Your project is now on $TARGET_IP"

# ทำการคอมไพล์และแฟลชบอร์ดบนบอร์ดปลายทาง
echo "Starting compilation and flashing of Arduino MCU on the target board ($TARGET_IP)..."

# คัดลอกไปโฟลเดอร์ชั่วคราวบนบอร์ดปลายทางเพื่อให้ชื่อสเก็ตช์ตรงกับโฟลเดอร์ตามมาตรฐาน arduino-cli
ssh $TARGET_USER@$TARGET_IP "mkdir -p /tmp/lift && cp -r $TARGET_DIR/arduino_code/* /tmp/lift/"

# รันคอมไพล์และอัปโหลดบนบอร์ดปลายทางผ่าน script block เพื่อความรวดเร็วและทนทาน
ssh $TARGET_USER@$TARGET_IP << 'EOF'
  FQBN="arduino:zephyr:unoq"
  PORTS=$(arduino-cli board list | grep 'Arduino UNO Q' | awk '{print $1}')
  
  if [ -z "$PORTS" ]; then
      PORTS="192.168.20.60"
  fi

  # ตรวจสอบและติดตั้งไลบรารีที่จำเป็นก่อนคอมไพล์
  echo "[*] Checking and installing necessary libraries (Arduino_RouterBridge)..."
  arduino-cli lib install Arduino_RouterBridge

  echo "[*] Compiling code on the target board..."
  arduino-cli compile --fqbn $FQBN /tmp/lift
  if [ $? -ne 0 ]; then
      echo "❌ Compilation of firmware failed!"
      exit 1
  fi

  UPLOAD_SUCCESS=false
  for PORT in $PORTS; do
      echo "[*] Trying to upload to port: $PORT..."
      arduino-cli upload -p "$PORT" --fqbn "$FQBN" /tmp/lift
      if [ $? -eq 0 ]; then
          echo "Upload successful through port: $PORT"
          UPLOAD_SUCCESS=true
          break
      fi
  done

  if [ "$UPLOAD_SUCCESS" = false ]; then
      echo "Upload of firmware failed on all ports!"
      exit 1
  fi
EOF

if [ $? -ne 0 ]; then
    echo "Failed to perform operations on the target board!"
    exit 1
fi

echo "Firmware update for MCU on the target board completed successfully!"

# รีสตาร์ทเซอร์วิสบนบอร์ดปลายทางเพื่อให้โค้ดใหม่ทำงาน
echo "Restarting services on the target board (system may prompt for sudo password)..."
ssh -t $TARGET_USER@$TARGET_IP "sudo systemctl restart arduino-router.service lift-service.service"

if [ $? -eq 0 ]; then
    echo "Services restarted successfully on the target board!"
else
    echo "Unable to restart services automatically. Please restart manually."
fi