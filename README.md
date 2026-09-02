# ระบบควบคุมลิฟต์สำหรับหุ่นยนต์และระบบอัตโนมัติ (Elevator Control System)

โปรเจกต์นี้เป็นระบบควบคุมและเชื่อมต่อลิฟต์สำหรับงานอุตสาหกรรมและหุ่นยนต์เคลื่อนที่อัตโนมัติ (AGV / AMR) รองรับการทำงานร่วมกับ PLC, Modbus TCP, Node-RED และบอร์ดประมวลผล **Arduino UNO Q** (Linux MPU + Zephyr RTOS MCU)

ภายในคลังโค้ดนี้แบ่งออกเป็น 3 รูปแบบสถาปัตยกรรมหลักตามลักษณะหน้างานและการนำไปใช้งาน:

---

## 📁 โครงสร้างโฟลเดอร์และหน้าที่การทำงาน (Folder Overview)

```text
/home/cookies/lift/
├── arduino_uno_q/    # [รูปแบบ 1] โค้ดสำหรับบอร์ด Arduino UNO Q ประจำสถานี (ทำงานร่วมกับ lift_server)
├── lift_server/      # [รูปแบบ 1] เซิร์ฟเวอร์กลาง Master Server (ทำงานร่วมกับ arduino_uno_q)
├── lift_tcp/         # [รูปแบบ 2] โค้ด Standalone บน Arduino UNO Q บอร์ดเดียวรันได้ทันที
├── wave_share/       # [รูปแบบ 3] การตั้งค่าและ Flow สำหรับ Node-RED + Waveshare Relay Board
├── requirements.txt  # Python Dependencies รวม
└── README.md         # เอกสารอธิบายระบบ (ไฟล์นี้)
```

### ตารางเปรียบเทียบสถาปัตยกรรมแต่ละโฟลเดอร์

| โฟลเดอร์ | สถาปัตยกรรม | อุปกรณ์ฮาร์ดแวร์ | หน้าที่หลัก |
| :--- | :--- | :--- | :--- |
| **`arduino_uno_q/`** + **`lift_server/`** | **Master-Station Architecture** (เซิร์ฟเวอร์กลาง + สถานีย่อย) | PC/Server + บอร์ด Arduino UNO Q ประจำแต่ละชั้น | ทำงานร่วมกัน: `arduino_uno_q` คุมฮาร์ดแวร์แต่ละชั้น ส่งข้อมูลเข้า `lift_server` เพื่อบริหารจัดการหลายลิฟต์/หลายชั้นพร้อมกันผ่านหน้า Web UI กลาง |
| **`lift_tcp/`** | **Standalone Controller** (บอร์ดเดียวจบ ไม่ต้องมีเซิร์ฟเวอร์) | บอร์ด Arduino UNO Q ตัวเดียวต่อสถานี | **ใช้แค่บอร์ดอาร์ดูอิโนตัวเดียวแล้วรันได้เลย**: รัน Dual-Modbus Bridge (ต่อตรงกับหุ่นยนต์ Port 502 + เซิร์ฟเวอร์ในตัว Port 1502) พร้อม Web Dashboard ในตัว |
| **`wave_share/`** | **Node-RED Automation** (โหนดเรด) | Waveshare Modbus TCP Relay + Node-RED Host | **ใช้สำหรับโหนดเรด**: ควบคุมลิฟต์ผ่านกล่อง Waveshare Relay โดยใช้ Node-RED จัดการ Logic การเรียกชั้นและบันทึกประวัติการทำงาน |

---

## 1. รูปแบบ Master-Station: `arduino_uno_q/` และ `lift_server/` (ทำงานร่วมกัน)

สถาปัตยกรรมนี้เหมาะสำหรับ **อาคารที่มีหลายชั้น หรือ มีลิฟต์หลายตัว (Multi-Lift System)** โดยแบ่งการทำงานออกเป็น 2 ส่วน:

```text
                         +-----------------------------+
                         |      Robot (AGV / AMR)      |
                         +--------------+--------------+
                                        | Modbus TCP (Port 502)
                                        v
                         +-----------------------------+
                         |         lift_server         |
                         |  (Central Master Server)    |
                         |  - จัดการสถานะลิฟต์รวม      |
                         |  - สแกนหาบอร์ดในเครือข่าย   |
                         |  - หน้า Admin UI ควบคุมรวม   |
                         +--------------+--------------+
                                        |
                 +----------------------+----------------------+
                 | Modbus TCP (1502) / REST API                | Modbus TCP (1502)
                 v                                             v
    +--------------------------+                  +--------------------------+
    |      arduino_uno_q       |                  |      arduino_uno_q       |
    | (Station ชั้น 1 - Lift A)|                  | (Station ชั้น 2 - Lift A)|
    | - Arduino UNO Q          |                  | - Arduino UNO Q          |
    | - คุม Relay, ปุ่ม, ไฟ    |                  | - คุม Relay, ปุ่ม, ไฟ    |
    +--------------------------+                  +--------------------------+
```

### 1.1 `arduino_uno_q/` (Station Controller ประจำแต่ละชั้น)
โค้ดสำหรับติดตั้งและรันบนบอร์ด **Arduino UNO Q** ประจำหน้าประตูลิฟต์แต่ละชั้น
- **หน้าที่การทำงาน**:
  - เชื่อมต่อกับฮาร์ดแวร์จริงหน้าลิฟต์: โซลินอยด์กดปุ่มลิฟต์ (UP/DOWN), ไฟสถานะปุ่มกด, เซ็นเซอร์ตรวจจับสถานะประตูเปิด/ปิด (Reed Switch/Proximity), และไฟแถบ WS2812B RGB
  - เปิด **Modbus TCP Server ที่พอร์ต 1502** เพื่อให้ `lift_server` อ่านสถานะ (Heartbeat, Floor, Door, Status) และส่งคำสั่งควบคุมเข้ามา
  - รัน **MessagePack RPC Client** สื่อสารข้ามโพรเซสระหว่างระบบปฏิบัติการ Linux บนบอร์ดกับเฟิร์มแวร์ MCU ผ่าน `/var/run/arduino-router.sock`
  - มี Web UI ขนาดเล็กสำหรับตั้งค่า Wi-Fi และทดสอบสั่งงาน Relay เฉพาะตัวบอร์ด
- **ไฟล์สำคัญในโฟลเดอร์**:
  - [`main.py`](arduino_uno_q/main.py): Service หลักบน Linux MPU (Modbus Server 1502 + Flask API)
  - [`arduino_code/lift.ino`](arduino_uno_q/arduino_code/lift.ino): เฟิร์มแวร์ C++/Zephyr RTOS สำหรับ MCU STM32 คุมพิน I/O แบบ Real-time
  - [`config.json`](arduino_uno_q/config.json): การตั้งค่าเครือข่าย, หมายเลขชั้น (`floor_name`), และชื่อลิฟต์ (`lift_id`)
  - [`update_mcu.sh`](arduino_uno_q/update_mcu.sh): สคริปต์คอมไพล์และอัปเดตเฟิร์มแวร์เข้าบอร์ดจากระยะไกลผ่าน SSH

### 1.2 `lift_server/` (Central Master Orchestrator)
เซิร์ฟเวอร์ศูนย์กลาง รันบนคอมพิวเตอร์แม่ข่าย, Mini PC หรือ Industrial PC ประจำอาคาร
- **หน้าที่การทำงาน**:
  - **เชื่อมต่อกับหุ่นยนต์ (Robot / AMR)**: เชื่อมต่อ Modbus TCP Port 502 กับหุ่นยนต์ เพื่อรับคำสั่งเรียกชั้น (`call_target`) และรายงานสถานะว่าลิฟต์พร้อมแล้วหรือไม่
  - **จัดการและมอนิเตอร์บอร์ดลูกข่ายทั้งหมด (`arduino_uno_q`)**:
    - มีระบบ Auto-Discovery สแกนค้นหาบอร์ดในวง Subnet อัตโนมัติ
    - ตรวจจับสัญญาณ Heartbeat หากบอร์ดใดขาดการติดต่อไปเกิน 10 วินาที จะแจ้งเตือน Offline
    - มีระบบ **SSH Self-Healing** พยายามสั่งรีสตาร์ท Service บนบอร์ดปลายทางอัตโนมัติเมื่อเกิดปัญหา
  - **Web Management Console (Port 5000)**:
    - หน้าแดชบอร์ดแสดงสถานะของลิฟต์ทุกตัวและทุกสถานีพร้อมกันแบบ Real-time
    - หน้าต่างตั้งค่า Mapping IP ของแต่ละชั้น (`lift_config.json`)
    - ระบบดู Log ย้อนหลังและดู Log สด (Server-Sent Events)
    - ระบบ **OTA Update**: อัปโหลดไฟล์ `patch.zip` ผ่านหน้าเว็บเพื่ออัปเดตโค้ดเซิร์ฟเวอร์อัตโนมัติ
- **ไฟล์สำคัญในโฟลเดอร์**:
  - [`lift_single_ui.py`](lift_server/lift_single_ui.py): ซอฟต์แวร์ Master Server หลัก พร้อมหน้า Web Admin Dashboard
  - [`lift_config.json`](lift_server/lift_config.json): ไฟล์คอนฟิกกำหนด IP และ Register Offset ของบอร์ดแต่ละสถานี
  - [`update.py`](lift_server/update.py): สคริปต์จัดการแตกไฟล์และอัปเดตเวอร์ชันผ่าน Web Patch

---

## 2. รูปแบบ Standalone: `lift_tcp/` (ใช้แค่บอร์ดอาร์ดูอิโนแล้วรันได้เลย)

โฟลเดอร์นี้ถูกออกแบบขึ้นเพื่อความง่าย คล่องตัว และเชื่อถือได้สูงสุด **"ใช้แค่บอร์ด Arduino UNO Q ตัวเดียวแล้วรันได้ทันที โดยไม่ต้องพึ่งพา lift_server"**

```text
               +---------------------------------------------------+
               |            Robot / AMR PLC / คอนโทรลเลอร์         |
               +-------------------------+-------------------------+
                                         |
                                         | Modbus TCP (Port 502)
                                         v
+---------------------------------------------------------------------------------+
| บอร์ด Arduino UNO Q (Linux MPU + Zephyr MCU) - โฟลเดอร์ lift_tcp/                 |
|                                                                                 |
|  [Modbus Client Port 502] <---> อ่านคำสั่งเรียกชั้นจากหุ่นยนต์ตรงๆ              |
|  [Modbus Server Port 1502] <--> เปิดให้ SCADA / PLC อื่นๆ คุมได้ด้วย            |
|  [Mission State Machine]  <---> วิ่ง Sequence: MOVING -> ARRIVED -> PULSE -> IDLE|
|  [Flask Web UI Port 5000] <---> หน้าจอตั้งค่าและทดสอบควบคุมผ่านเว็บสวยงาม       |
|  [mDNS & Recovery Hotspot]<---> เข้าเว็บผ่าน http://lift<mac>.local:5000         |
|                                                                                 |
|  [MCU Layer (STM32)]      <---> สั่ง Solenoid, อ่านเซ็นเซอร์ประตู, ขับไฟ WS2812B|
+---------------------------------------------------------------------------------+
```

### จุดเด่นของ `lift_tcp/`:
1. **บอร์ดเดียวจบ (All-in-One)**: รวมทั้ง Client, Server, Web UI, State Machine, และ Firmware อยู่ในตัวบอร์ดเดียว
2. **Dual-Modbus Bridge ในตัว**:
   - เป็น **Modbus Client (Port 502)** เชื่อมต่อไปยังหุ่นยนต์ (`robot_ip:502`) อ่าน Register การเรียกชั้นอัตโนมัติ
   - เปิด **Modbus Server (Port 1502)** ในตัวบอร์ด ให้ระบบภายนอกหรือโปรแกรมเสริมอื่นๆ สื่อสารเข้ามาได้
3. **Mission State Machine อัตโนมัติ**:
   - เมื่อหุ่นยนต์สั่งเรียกชั้น บอร์ดจะขับ Solenoid กดปุ่มลิฟต์ $\rightarrow$ รอเซ็นเซอร์ประตูเปิดจริง $\rightarrow$ ส่งสัญญาณบอกหุ่นยนต์ $\rightarrow$ Pulse โซลินอยด์หน่วงเวลาเปิดประตู $\rightarrow$ เคลียร์สถานะเสร็จสิ้น
   - มี **Watchdog 60 วินาที** หากลิฟต์ค้างหรือเกิดข้อผิดพลาด จะตัดกลับสถานะ IDLE อัตโนมัติ ป้องกันระบบค้าง
4. **ไฟสถานะ WS2812B RGB**: แสดงสถานะการทำงานด้วยแสงไฟ (น้ำเงิน = พร้อม, เขียว = กำลังเรียก, ชมพู = ประตูเปิด/ถึงชั้นแล้ว, แดง = ผิดพลาด)
5. **Zero-Configuration mDNS & Recovery Hotspot**:
   - เข้าหน้าเว็บได้ทันทีผ่านชื่อ Hostname: `http://lift<4_ตัวท้าย_MAC>.local:5000`
   - หาก Wi-Fi หลุดหรือยังไม่ได้ตั้งค่า บอร์ดจะปล่อย Hotspot กู้ภัยชื่อ `NextElevator_<MAC>` (รหัสผ่าน `12345678`) อัตโนมัติ

### ไฟล์สำคัญใน `lift_tcp/`:
- [`main.py`](lift_tcp/main.py): โปรแกรมหลักที่รวม Bridge, Web Server, State Machine และ Wi-Fi Manager
- [`arduino_code/lift.ino`](lift_tcp/arduino_code/lift.ino): เฟิร์มแวร์ MCU Zephyr RTOS สำหรับขับฮาร์ดแวร์
- [`arduino_code/ws2812b-bitbang.h`](lift_tcp/arduino_code/ws2812b-bitbang.h): ไดรเวอร์ขับไฟ RGB WS2812B แบบ Bit-banging
- [`templates/ui.html`](lift_tcp/templates/ui.html): หน้าจอ Web Dashboard ทันสมัย Glassmorphism
- [`lift_config.json`](lift_tcp/lift_config.json): การตั้งค่าหมายเลขชั้น, IP หุ่นยนต์, และที่อยู่ Modbus Registers
- [`update_mcu.sh`](lift_tcp/update_mcu.sh): เครื่องมืออัปเดตโค้ดและแฟลช MCU ผ่าน SSH ในคำสั่งเดียว (รองรับ `--skip-mcu`, `--install-deps`)
- [`MANUAL.md`](lift_tcp/MANUAL.md): คู่มือทางเทคนิคและสเปกการทำงานอย่างละเอียดของ `lift_tcp`

### คำสั่งสั่งรัน / อัปเดต `lift_tcp`:
```bash
# อัปเดตโค้ด Python และแฟลชเฟิร์มแวร์ MCU เข้าบอร์ดปลายทาง
/home/cookies/lift/lift_tcp/update_mcu.sh 192.168.20.49

# อัปเดตเฉพาะโค้ด Python และหน้าเว็บอย่างรวดเร็ว (ข้ามการแฟลช MCU)
/home/cookies/lift/lift_tcp/update_mcu.sh 192.168.20.49 --skip-mcu
```

---

## 3. รูปแบบ Node-RED: `wave_share/` (ใช้สำหรับโหนดเรด)

โฟลเดอร์นี้จัดเตรียมไว้ **"สำหรับผู้ที่ใช้งานระบบผ่าน Node-RED (โหนดเรด)"** ร่วมกับฮาร์ดแวร์ **Waveshare Modbus TCP Relay Module** แทนการใช้บอร์ด Arduino UNO Q

```text
+-----------------------+     Modbus TCP (Port 502)     +-----------------------+
|   Robot (AGV / AMR)   | <---------------------------> |       Node-RED        |
+-----------------------+                               |  (รัน flows_new.json) |
                                                        +-----------+-----------+
                                                                    | Modbus TCP (4196)
                                                                    v
                                                        +-----------------------+
                                                        | Waveshare Modbus      |
                                                        | Relay Controller      |
                                                        | - DI: เซ็นเซอร์ประตู   |
                                                        | - DO: คุม Solenoid    |
                                                        +-----------------------+
```

### หน้าที่และการทำงานของ `wave_share/`:
- **Hardware Integration**: เชื่อมต่อกล่องรีเลย์ Waveshare Modbus Relay ผ่านสาย LAN (เช่น IP `192.168.1.200:4196`)
  - **Digital Input (DI)**: ต่อเซ็นเซอร์ประตูลิฟต์, สวิตช์ปุ่มกดหน้าชั้น
  - **Digital Output (DO)**: ต่อโซลินอยด์กดปุ่มเรียกขึ้น/ลง และหลอดไฟแสดงสถานะ
- **Node-RED Automation**:
  - มีไฟล์ Flow สำหรับ Import เข้า Node-RED ทำหน้าที่รัน Logic ควบคุมลิฟต์, เชื่อมต่อ Modbus ระหว่าง Robot กับ Waveshare
  - จัดการ Event การเปิด-ปิดประตูลิฟต์, เคลียร์สถานะเมื่อลิฟต์มาถึง
  - บันทึกประวัติการทำงานและแจ้งเตือนสถานะการเชื่อมต่อ
- **ไฟล์สำคัญในโฟลเดอร์**:
  - [`flows_new.json`](wave_share/flows_new.json): ไฟล์ Export ของ Node-RED Flow พร้อมใช้งาน
  - [`config.json`](wave_share/config.json): การกำหนดค่า Modbus Registers และ Coils ของอุปกรณ์ Waveshare
  - [`daily_elevator_log.csv`](wave_share/daily_elevator_log.csv): ตัวอย่างโครงสร้างไฟล์บันทึกประวัติการทำงาน (Timestamp, Door Event, Physical Button, Manual Trigger)

---

## 🧭 คำแนะนำการเลือกใช้งาน (Decision Guide)

| กรณีการใช้งาน | โฟลเดอร์ที่ควรเลือก | เหตุผล |
| :--- | :--- | :--- |
| **ต้องการติดตั้งแบบง่าย รวดเร็ว ใช้แค่บอร์ด Arduino UNO Q ตัวเดียวต่อสถานี** | 👉 **`lift_tcp/`** | รันบอร์ดเดี่ยวได้ทันที ไม่ต้องมีเซิร์ฟเวอร์กลาง คุยกับหุ่นยนต์ตรงๆ มีหน้าจอ Web UI และ Hotspot ในตัว |
| **มีลิฟต์หลายตัว หลายชั้น ต้องการรวมศูนย์และดู Dashboard ลิฟต์ทุกตัวในหน้าจอเดียว** | 👉 **`arduino_uno_q/`** + **`lift_server/`** | บอร์ด `arduino_uno_q` คุมฮาร์ดแวร์แต่ละชั้น แล้วให้ `lift_server` เป็น Master รวมศูนย์ คอยตรวจสอบ Heartbeat และ Self-healing |
| **มีระบบเดิมที่ใช้ Node-RED อยู่แล้ว หรือต้องการใช้กล่องรีเลย์สำเร็จรูปอุตสาหกรรม** | 👉 **`wave_share/`** | นำไฟล์ `flows_new.json` ไป Import ใน Node-RED เพื่อเชื่อมต่อกับบอร์ดรีเลย์ Waveshare ได้ทันที |

---

## 🧰 ข้อมูลพินฮาร์ดแวร์สำหรับบอร์ด Arduino UNO Q (Hardware Pinout)

สำหรับการใช้งานโฟลเดอร์ `lift_tcp/` หรือ `arduino_uno_q/` พินบนบอร์ด Arduino UNO Q มีการต่อวงจรดังนี้:

| พิน Arduino UNO Q | ประเภท | หน้าที่การทำงาน | หมายเหตุ |
| :---: | :---: | :--- | :--- |
| **A1** | Digital Input | เซ็นเซอร์ตรวจจับประตูลิฟต์ (ชั้นทั่วไป) | Active LOW (Pull-up) ประตูเปิด = LOW / ประตูเปิดวงจร = HIGH |
| **A4** | Digital Input | เซ็นเซอร์ตรวจจับประตูลิฟต์ (ชั้น B1) | ใช้เฉพาะกรณีติดตั้งที่ชั้นใต้ดิน B1 |
| **A2** | Digital Input | สวิตช์ปุ่มกดเรียกลิฟต์ขึ้น (UP Button) | ปุ่มกด Manual หน้าชั้น |
| **A3** | Digital Input | สวิตช์ปุ่มกดเรียกลิฟต์ลง (DOWN Button) | ปุ่มกด Manual หน้าชั้น |
| **D11** | Digital Output | รีเลย์ขับโซลินอยด์ปุ่มขึ้น (UP Solenoid) | ควบคุมรีเลย์กดปุ่มลิฟต์ขึ้น |
| **D7** | Digital Output | รีเลย์ขับโซลินอยด์ปุ่มลง (DOWN Solenoid) | ควบคุมรีเลย์กดปุ่มลิฟต์ลง |
| **D8** | Digital Output | ไฟแสดงสถานะปุ่มกดขึ้น (UP Button LED) | สว่างเมื่อลิฟต์กำลังถูกเรียกขึ้น |
| **D12** | Digital Output | ไฟแสดงสถานะปุ่มกดลง (DOWN Button LED) | สว่างเมื่อลิฟต์กำลังถูกเรียกลง |
| **D6** | Digital Output | สัญญาณข้อมูลไฟ RGB WS2812B (Data) | แถบไฟ RGB 16 ดวง แสดงสีบอกสถานะลิฟต์ |

### กลไกความปลอดภัยในตัวบอร์ด (Built-in Safety):
- **Press Lock Protection**: ล็อกการกดปุ่มซ้ำซ้อนภายใน **5,000 ms** เพื่อป้องกันการกดปุ่มรัว
- **Solenoid Auto-Release**: ตัดการจ่ายไฟโซลินอยด์อัตโนมัติภายใน **800 ms** เพื่อป้องกันขดลวดโซลินอยด์ไหม้
