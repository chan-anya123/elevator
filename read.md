# Elevator Control System

A Python-based elevator control and monitoring system designed for industrial and robotic integration.

## Overview

This project provides communication and control interfaces for elevator systems, allowing external clients such as robots, AGVs, AMRs, or automation systems to:

- Call elevators
- Select target floors
- Monitor elevator status
- Receive real-time feedback
- Integrate with warehouse automation systems

## Features

- TCP/IP communication
- Elevator status monitoring
- Floor command control
- Multi-client support
- Real-time event handling
- Industrial automation ready

## Architecture

```text
+-------------+
| Robot / AMR |
+------+------+ 
       |
       v
+-------------+
| Lift Server |
+------+------+ 
       |
       v
+-------------+
|   Elevator  |
+-------------+