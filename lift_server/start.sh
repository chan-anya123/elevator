#!/bin/bash
# 1. วิ่งไปเช็คและดาวน์โหลดอัปเดตโค้ดก่อน
python3 update.py

# 2. รันเซิร์ฟเวอร์ลิฟต์หลักพร้อมหน้าเว็บ UI
echo ">>> Starting Lift Master System on Ubuntu... <<<"
python3 lift_single_ui.py