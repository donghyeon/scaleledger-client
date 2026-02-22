# 🐟 ScaleLedger Client Product Requirements Document (PRD)

**문서 버전**: 3.1
**작성 일자**: 2026.02.22
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

1.  **Hardware Worker (Thread) - [Producer Multi]**
    * 역할: 개별 `WeighingStation` 설정값(포트, 설정)을 바탕으로 동적으로 생성되는 스레드. `SUWOL-1000` 장비와 물리적 통신 전담 (Blocking I/O 격리).
    * 동작: 고속 폴링(Request/Response)을 수행하며, 유의미한 센서 데이터 감지 시 즉시 Main Loop로 이벤트를 발행.
2.  **Main Controller (Asyncio Loop) - [Processor]**
    * 역할: 장비 설정 동기화, Worker 스레드 라이프사이클 관리, 비즈니스 로직(FSM) 처리.
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

### 4.1 Gateway Provisioning & Sync (Gateway 등록 및 동기화)
기존의 복잡한 무한 폴링(FSM) 로직을 폐기하고, 단순화된 **웹소켓 이벤트 대기 및 API 동기화** 로직을 사용합니다.

1.  **BOOTSTRAP (부팅 및 점검)**
    * 로컬 DB(`SQLite`)를 조회하여 `access_token` 유무 확인.
    * 토큰이 존재하면 서버 API(`/devices/api/gateways/self/`)를 호출하여 서버의 최신 Gateway 설정값으로 로컬 DB를 **동기화(Sync)**.
    * 토큰이 없다면 프로비저닝 웹소켓(`/ws/devices/gateways/provisioning/`)에 연결하여 대기.
2.  **PROVISIONING (이벤트 기반 등록)**
    * 서버로부터 `identify` 이벤트 수신 시 장비 정보(MAC, IP 등) 응답.
    * 등록 완료 이벤트(`gateway.registered`) 수신 시 새 토큰을 저장하고 즉시 **동기화(Sync)** 진행.
3.  **ACTIVE (정상 운영)**
    * 주기적인 Heartbeat 전송 및 전용 명령 채널 대기.

### 4.2 WeighingStation Provisioning & Sync (WeighingStation 등록 및 동기화)
서버에서 관리자가 계량기를 추가/수정/삭제하면 현장 PC의 로컬 DB에 즉각 반영되고 하드웨어 제어 스레드에 적용됩니다.

* **Trigger**: 서버로부터 전용 웹소켓 채널을 통해 `weighing_station.sync` 이벤트 수신 (또는 클라이언트 부팅 완료 시 1회 수행).
* **Action**:
    1. REST API(`/devices/api/gateways/self/stations/`)를 호출하여 서버의 최신 Station 목록 전체를 조회.
    2. 서버 응답을 기준으로 로컬 SQLite DB의 `WeighingStation` 테이블을 **동기화(Sync)** (Upsert 및 Hard Delete).
    3. 구동 중인 `WeighingStationWorker` 중 설정이 변경되거나 삭제된 포트의 Worker를 중지(Close).
    4. 새로 추가되거나 설정이 변경된 포트에 대해 새로운 `WeighingStationWorker` 스레드 기동.

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
클라이언트의 로컬 데이터베이스는 서버 데이터의 **캐시(Cache)** 역할과 현장 물리 데이터의 **원본(Source of Truth)** 역할을 동시에 수행합니다.

### 5.1 Gateway (서버 데이터 로컬 캐시)
서버(Django)에서 관리되는 현장 PC 설정의 로컬 사본입니다. 네트워크 단절 시에도 클라이언트가 스스로의 식별 정보를 유지할 수 있도록 돕습니다.

| Field | Type | Description |
|---|---|---|
| `id` | INT (PK) | 서버에서 발급한 Gateway ID |
| `mac_address` | TEXT | 기기 고유 하드웨어 주소 (Unique) |
| `hostname` | TEXT | PC 호스트명 |
| `ip_address` | TEXT | IP 주소 |
| `name` | TEXT | 관리자가 지정한 기기명 |
| `description` | TEXT | 기기 상세 비고 |
| `access_token` | TEXT | API 인증 토큰 |
| `last_heartbeat` | DATETIME | 마지막 생존 신호 시각 |
| `created_at` | DATETIME | 기기 등록 시각 |
| `updated_at` | DATETIME | 정보 수정 시각 |

### 5.2 WeighingStation (서버 데이터 로컬 캐시)
서버에서 할당된 계량기 설정의 로컬 사본입니다. Main Loop는 이 테이블을 읽어 Worker 스레드를 동적으로 띄웁니다.

| Field | Type | Description |
|---|---|---|
| `id` | INT (PK) | 서버에서 발급한 Station ID |
| `gateway_id` | INT (FK) | 종속된 Gateway ID |
| `name` | TEXT | 계량기 명칭 |
| `serial_port` | TEXT | 통신 포트명 (예: COM3) |
| `serial_number` | TEXT | 하드웨어 시리얼 (옵션) |
| `updated_at` | DATETIME | 서버 갱신 시각 (동기화 기준값) |

### 5.3 WeighingRecord (계량 기록 원본 - [TODO: 구현 예정])
현장에서 발생한 물리적 계량 사실의 **원천 데이터(Source of Truth)**입니다. 클라이언트는 이 데이터를 무조건 로컬 DB에 우선 저장한 후, 백그라운드에서 서버로 동기화(업로드)하는 책임을 가집니다. (현재 미구현 상태)

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
