# 🐟 ScaleLedger Client

**The headless IoT edge client for the ScaleLedger fishery management system.**

This repository contains the source code for the client-side application running on field gateway PCs. It acts as a critical bridge between physical weighing hardware (digital indicators, RFID readers) and the central ScaleLedger Django server.

## 📖 About the Project

ScaleLedger is a comprehensive platform for managing fishery auctions, weighing records, and financial settlements. While the server handles data processing and administration, this **Client** is responsible for the "Edge" operations at the actual auction site.

The client is designed to run as a background system service on Windows/Linux machines connected to weighing scales via Serial (RS-232/USB) ports. It operates without a graphical user interface (Headless), focusing on **reliability**, **data integrity**, and **real-time concurrency**.

## 🎯 Core Responsibilities

1.  **Hardware Abstraction & FSM Logic**
    - Establishes persistent serial connections with industrial weighing indicators.
    - Manages the weighing lifecycle (Idle -> Measuring -> Stable) using an internal **Finite State Machine (FSM)**.
    - Decodes raw byte streams to extract weight data and RFID tag UIDs.

2.  **Device Identity & Security**
    - Uniquely identifies the gateway PC using its hardware MAC address.
    - Performs automated **Handshake & Registration** with the server to obtain access tokens.
    - Securely stores credentials and device configurations in a local database.

3.  **Robust Data Synchronization**
    - **Store-and-Forward:** Saves weighing records locally first (Local-First), then uploads them to the server asynchronously. This ensures zero data loss even during network outages.
    - **Real-time Streaming:** Pushes weight updates via WebSockets for the live auction UI, applying **Smart Throttling** (e.g., 10Hz) to prevent network congestion.
    - **Heartbeat:** Sends periodic health checks to report device status and connectivity.

## 🏗 System Architecture

The client employs a **Hybrid Asyncio-Thread Model** to ensure that blocking I/O operations never degrade the performance of the main event loop.

- **Serial Worker (Threaded):** A dedicated thread that continuously reads the hardware buffer (Blocking I/O) and pushes raw data into an async-safe queue.
- **Main Controller (Asyncio):** The single-threaded event loop that orchestrates the entire application:
    - Consumes data from the queue.
    - Updates the FSM state.
    - Manages HTTP REST API calls and WebSocket connections concurrently.
- **Local Persistence:** Uses **Tortoise ORM** with SQLite to persist unsent records and configuration.

## 🛠 Tech Stack

- **Language:** Python 3.14+
- **Package Manager:** `uv`
- **Concurrency:** `asyncio` (Core Logic) + `threading` (Serial Read)
- **Hardware:** `pyserial`
- **Network:** `httpx` (Async HTTP Client), `websockets`
- **Database:** `Tortoise ORM` (Async ORM)
- **Logging:** `structlog`
