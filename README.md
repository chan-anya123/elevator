# Elevator Control System

A Python-based cross-platform elevator control and monitoring master system designed for industrial automation and robotic integration (AGVs/AMRs).

## Overview

This project provides communication and control interfaces for elevator systems, allowing external clients such as robots, AGVs, AMRs, or automation systems to:

- Call elevators via Modbus TCP.
- Select target floors dynamically.
- Monitor elevator status (Door status, current floor, heartbeats).
- Receive real-time feedback via a web interface or REST API.
- Automatically self-heal and rediscover disconnected lift stations.

---

## Architecture

```text
+-----------------------+
|  Central Cloud / Web  | <--- OTA Update (patch.zip / lift_version.txt)
+-----------+-----------+
            │
            ▼
+-----------------------+
|      Robot / AMR      |
+-----------+-----------+ 
            │ (Modbus TCP Client / REST API)
            ▼
+-----------------------+
|  Lift Master Server   | <--- (Runs lift_single_ui.py inside venv)
+-----------+-----------+
            │ (Modbus TCP Client / HTTP Status)
            ▼
+-----------------------+
|       Elevator        | <--- (Arduino)
+-----------------------+
