# 🐟 ScaleLedger Client Product Requirements Document (PRD)

**문서 버전**: 3.0
**작성 일자**: 2026.02.19
**대상 독자**: 엣지 클라이언트 개발자, 임베디드 시스템 엔지니어

---

## 1. 프로젝트 개요 (Overview)

### 1.1 프로젝트 정의
**ScaleLedger Client**는 수산물 경매 현장의 게이트웨이 PC에서 실행되는 **Headless IoT Edge Controller**입니다. 중앙 서버(Django)의 정책을 수신하여 로컬 장비를 제어하고, 물리적 계량 장비(`SUWOL-1000`)와 고속 통신을 수행하여 계량 데이터를 확보하는 핵심 미들웨어입니다.

### 1.2 핵심 철학 (Core Philosophy)
1.  **On-Demand Provisioning (이벤트 기반 등록)**: 게이트웨이 등록 및 하드웨어 스캔 과정에서 불필요한 Polling API 호출을 완전히 제거했습니다. 클라이언트는 WebSocket을 통해 서버의 명령(Command)을 대기하며, 관리자의 실시간 스캔 요청에만 응답합니다.
2.  **Local-First & Sync (선 저장 후 전송)**: 모든 계량 데이터는 로컬 DB(SQLite)에 우선 저장되어야 하며, 네트워크 상태와 무관하게 현장 업무는 지속되어야 합니다.
3.  **PC-Driven Control (PC 주도 제어)**: 연결된 계량 장비(`SUWOL-1000`)는 수동적인 Slave 장치입니다. 클라이언트는 Hardware Worker Thread를 통해 장비를 고속 폴링(Polling)하며 제어권을 독점합니다.

---

## 2. 시스템 아키텍처 (System Architecture)

### 2.1 하드웨어 토폴로지
* **Host**: Windows/Linux PC (Gateway)
* **Target Device**: `SUWOL-1000` 통합 계량기 (Scale + RFID Reader + LED Display + Printer + Voice Module)
* **Interface**: RS-232 Serial (Baudrate: 9600)

### 2.2 소프트웨어 구조 (Producer-Consumer Pattern)
데이터 흐름을 최적화하기 위해 `asyncio.Queue`를 활용한 비동기 파이프라인을 구축합니다.

1.  **Hardware Worker (Thread) - [Producer 1]**
    * 역할: `SUWOL-1000` 장비와 물리적 통신 전담 (Blocking I/O 격리).
    * 동작: 고속 폴링(Request/Response)을 수행하며, 유의미한 센서 데이터 감지 시 즉시 Main Loop로 이벤트를 발행.
2.  **Main Controller (Asyncio Loop) - [Processor]**
    * 역할: 비즈니스 로직(FSM) 처리.
    * 동작: Hardware Worker의 이벤트를 받아 로컬 DB에 저장하고, 동시에 `Sync Queue`에 작업을 발행(Put).
3.  **Sync Worker (Asyncio Task) - [Consumer]**
    * 역할: 서버 데이터 동기화 전담.
    * 동작: `Sync Queue`를 대기(`await get()`)하다가 데이터가 들어오는 즉시 서버로 업로드. 성공 시 DB 상태 업데이트.

---

## 3. 하드웨어 인터페이스 요구사항 (Hardware Driver)

클라이언트는 `SUWOL-1000` 프로토콜 사양(`SUWOL-1000.md`)을 완벽히 준수해야 합니다. **(이 영역은 물리적 제약으로 인해 Polling 방식을 유지합니다.)**

### 3.1 Polling & Display (Master Mode)
* **상시 명령 전송**: 클라이언트는 IDLE 상태에서도 상시 `D` 커맨드(Display Update)를 전달하여 계량 기기 상태를 상시 확인해야 합니다.
* **전광판 제어**: 현재 상태(대기중, 계량중, 완료 등)에 따라 전광판에 표시될 텍스트를 동적으로 렌더링합니다.

### 3.2 Input Capture (Volatile Data)
* **휘발성 데이터 방어**: `SUWOL-1000`의 입력값(RFID, Keypad)은 1회 전송 후 소멸되므로, Hardware Worker는 응답 패킷을 파싱하여 데이터 존재 시 즉시 캡처해야 합니다.

---

## 4. 상세 기능 명세 (Detailed Specifications)

### 4.1 접속 및 프로비저닝 상태 관리 (Network Event Loop)
기존의 복잡한 등록 확인 무한 폴링(FSM) 로직을 폐기하고, 단순화된 **WebSocket 기반 이벤트 대기** 로직을 사용합니다.

1.  **BOOTSTRAP (부팅 및 점검)**
    * 로컬 DB(`SQLite`)를 조회하여 할당된 `access_token`이 있는지 확인합니다.
    * 없으면 서버의 `/devices/api/gateways/{mac_address}/` API를 호출하여 등록 여부를 확인합니다.
2.  **PROVISIONING LISTENER (대기 상태)**
    * 서버에 미등록 상태(404)인 경우, 클라이언트는 즉시 `/ws/devices/gateways/provisioning/` 웹소켓에 연결합니다.
    * 서버(관리자 웹)로부터 `identify` 이벤트가 수신되면, 현재 기기의 `mac_address`, `hostname`, `ip_address`를 취합하여 즉시 응답 패킷을 발송합니다.
3.  **ACTIVE (정상 운영 및 명령 대기)**
    * 등록이 완료된 상태에서는 정해진 주기에 따라 HTTP 로 Heartbeat를 전송합니다.
    * 개별 기기 전용 웹소켓(`/ws/devices/{gateway_id}/`)에 연결하여 원격 명령을 수신합니다.

### 4.2 주변기기 동적 스캔 (Peripheral Scan via WebSocket)
관리자가 웹 대시보드에서 현장 PC에 연결된 시리얼 장비 목록을 원격으로 스캔할 수 있도록 지원해야 합니다.

* **Trigger**: 서버로부터 웹소켓을 통해 `command.scan.peripherals` 이벤트 수신.
* **Action**: `serial.tools.list_ports.comports()`를 실행하여 현재 꽂혀있는 모든 시리얼 장치 정보(포트명, 하드웨어 ID, 제조사 등)를 수집합니다.
* **Response**: 수집된 JSON 배열을 웹소켓 채널을 통해 서버로 반환하여, 관리자가 웹 UI상에서 올바른 `WeighingStation`을 직관적으로 선택할 수 있도록 합니다.


### 4.3 데이터 동기화 (Event-Driven Sync Strategy)
**Polling을 금지하며**, 큐(Queue)를 이용한 즉시 전송 방식을 사용합니다.

1.  **Boot & Reconnect Strategy (Recovery)**:
    * 프로그램 시작 시 또는 네트워크 재연결 시, 로컬 DB에서 `is_synced=False`인 모든 기록을 조회하여 `Sync Queue`에 일괄 주입합니다.
2.  **Real-time Upload (Consumer Loop)**:
    * `Sync Worker`는 큐에서 데이터를 하나씩 꺼내(`await queue.get()`) 서버 API로 전송합니다.
    * **Success**: 전송 성공 시(`201 Created`), 로컬 DB의 해당 레코드를 `is_synced=True`로 업데이트합니다.
    * **Failure**: 네트워크 오류 발생 시, 해당 아이템을 다시 큐에 넣거나(Re-queue with delay), 다음 재연결 이벤트까지 보류합니다.

---

## 5. 데이터 모델링 (Local SQLite Schema)

### 5.1 Gateway (기기 설정)
| Field | Type | Description |
|---|---|---|
| `mac_address` | TEXT (PK) | 기기 고유 ID |
| `access_token` | TEXT | API 인증 토큰 |
| `status` | TEXT | 등록 상태 |
| `heartbeat_interval` | INT | 서버 설정값 |

### 5.2 WeighingRecord (계량 기록)
| Field | Type | Description |
|---|---|---|
| `uuid` | TEXT (PK) | 고유 식별자 (UUID v4) |
| `rfid_uid` | TEXT | 태그된 RFID 값 |
| `weight` | INT | 측정 중량 (kg) |
| `measured_at` | DATETIME | 측정 완료 시각 |
| `is_synced` | BOOLEAN | 서버 전송 여부 (Index) |
| `created_at` | DATETIME | 로컬 생성 시각 |

---

## 6. 비기능 요구사항 (NFR)

1.  **Zero Latency Sync**:
    * 계량 완료 후 서버와의 데이터 동기화를 위해 별도의 Worker로 넘겨 빠르게 작업을 마무리 하며, 다른 작업들에 방해가 되지 않도록 합니다.
2.  **Concurrency Safety**:
    * Hardware Thread와 Asyncio Loop 간의 데이터 교환은 반드시 `Thread-Safe Queue`를 통해 이루어져야 합니다.
3.  **Graceful Shutdown**:
    * 프로그램 종료 시 `Sync Queue`에 남아있는 데이터는 처리하지 않고 종료해도 무방합니다(데이터는 이미 로컬 DB에 안전하게 저장되어 있으며, 다음 부팅 시 Recovery 로직에 의해 처리됨).
