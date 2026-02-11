# 🐟 ScaleLedger Client

**High-Frequency IoT Edge Controller for Fishery Weighing Automation**

ScaleLedger Client is a **Headless IoT Edge Controller** running on gateway PCs at wholesale fishery auction sites. It acts as a critical bridge that orchestrates physical weighing hardware (`SUWOL-1000`) while maintaining real-time synchronization with the central ScaleLedger Django server.

## 📖 Project Overview

This project is not just a passive data forwarder. It operates as a **Master Controller** for field equipment, utilizing a **PC-Driven Polling Architecture** to dominate hardware I/O. Simultaneously, it employs an **Event-Driven Sync Strategy** to ensure zero-latency data transmission to the cloud, strictly adhering to **Local-First** principles for data integrity.

## 🎯 Core Responsibilities

The client handles two distinct operational domains with different concurrency models:

### 1. Hardware Orchestration (The Polling Domain)
The client acts as the **Master** driver for the `SUWOL-1000` integrated weighing system.

* **Active Polling & Data Capture**: Since the hardware does not buffer data, the client performs high-frequency polling (sub-100ms) to capture volatile RFID tags and weight data before they vanish.
* **Feedback Control**: It actively renders visual information on the LED display and triggers voice guidance (TTS) and printer outputs based on the weighing workflow state.

### 2. Reactive Cloud Synchronization (The Event Domain)
Unlike the hardware layer, the network layer avoids polling.

* **Zero-Latency Upload**: Weighing records are pushed to a transmission queue immediately upon creation, triggering an instant upload to the server without waiting for a scheduled interval.
* **Offline Resilience**: If the network is down, the queue pauses, but data is safely secured in the local SQLite database. Upon reconnection, the system automatically replays the pending events.
* **Identity Management**: Securely manages device identity via MAC address and auto-refreshing access tokens.

## 🏗 System Architecture

To satisfy both the strict timing requirements of hardware and the asynchronous nature of network I/O, the system uses a **Producer-Consumer Pattern**:

### 1. Dedicated Hardware Thread (Producer)
* Isolates blocking serial I/O from the main event loop.
* Continuously polls the hardware in a tight loop (Request-Response).
* Parses raw packets and produces "Physical Events" (e.g., RFID detected, Weight Stabilized) into a thread-safe queue.

### 2. Asyncio Main Loop (Processor)
* Operates on a **Finite State Machine (FSM)**: `INITIALIZE` → `SYNC` → `REGISTER` → `HEARTBEAT`.
* Consumes physical events, executes business logic (e.g., deciding when to finalize a transaction), and persists data to the local database (**Tortoise ORM**).

### 3. Sync Worker (Consumer)
* A dedicated background task that monitors the **Sync Queue**.
* As soon as the Main Loop commits a record to the DB, it pushes a task to this queue.
* The worker consumes the task and executes the HTTP REST call to the server, ensuring immediate data synchronization.

## 🔌 Hardware Interface (`SUWOL-1000` Protocol)

The client implements the full **Master-Slave protocol** for `SUWOL-1000`:

* **Communication**: RS-232 Serial (9600bps).
* **Protocol Logic**:
    * **Request (PC → MCU)**: Sends display updates, relay control bits, and voice commands in a single packet.
    * **Response (MCU → PC)**: Receives weight sensor data, RFID tags, and keypad inputs.
* **Constraint**: The MCU has no memory. If the client stops polling even for a second, field data is permanently lost. Reliability is paramount.

## 🛠 Tech Stack

* **Language**: Python 3.14+
* **Runtime Manager**: uv
* **Core**: asyncio (Event Loop), asyncio.Queue (Data Pipeline), threading
* **Networking**: httpx (Async HTTP), websockets
* **Database**: Tortoise ORM (Async SQLite)
* **Hardware**: pyserial
* **Logging**: structlog (Structured JSON Logging)
