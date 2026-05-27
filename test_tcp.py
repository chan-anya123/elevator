# from pyModbusTCP.client import ModbusClient
# import time

# c = ModbusClient(host="192.168.20.52", port=502, auto_open=True)

# print("--- Testing MPU at 192.168.10.5 ---")
# for _ in range(5):
#     regs = c.read_holding_registers(1, 1) # อ่าน Heartbeat
#     if regs:
#         print(f"Heartbeat (Addr 1): {regs[0]}")
    
#     # ลองส่ง Feedback
#     success = c.write_single_register(8, 1)
#     if success:
#         print("Sent Feedback to Addr 8: Success")
    
#     time.sleep(1)


# from pyModbusTCP.client import ModbusClient
# import time

# # ลองเชื่อมต่อกับชั้น 2 โดยตรง
# test_client = ModbusClient(host='192.168.20.66', port=502, auto_open=True)

# while True:
#     regs = test_client.read_holding_registers(0, 15)
#     if regs:
#         print(f"Read Success! -> Door A: {regs[4]}, Door B: {regs[14]}")
#     else:
#         print("Read Failed: Cannot connect to Modbus on Floor 2")
#     time.sleep(1)


from pyModbusTCP.client import ModbusClient
import time

# ใส่ IP Robot ของคุณ
c = ModbusClient(host="192.168.10.5", port=502, auto_open=True)

while True:
    regs = c.read_holding_registers(0, 20) # อ่าน 20 ตัวแรกมาดูเลย
    if regs:
        print(f"Current Registers: {regs}")
        print(f"Target A (Reg 6): {regs[6]}")
        print(f"Target B (Reg 16): {regs[16]}")
    else:
        print("Read Error")
    time.sleep(1)