@echo off
:: 1. วิ่งไปเช็คและดาวน์โหลดอัปเดตโค้ดก่อน
python update.py

:: 2. รันเซิร์ฟเวอร์ลิฟต์หลักพร้อมหน้าเว็บ UI
echo >>> Starting Lift Master System on Windows... <<<
python lift_single_ui.py
pause