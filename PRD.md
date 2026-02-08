# Product Requirements Document: ScaleLedger Headless Client

**문서 버전**: 1.0
**작성 일자**: 2026.02.07

---

## 1. 개요 (Overview)
**ScaleLedger Client**는 수산물 경매 현장의 게이트웨이 PC에서 실행되는 Headless 프로그램이다. 계량기(Scale)와 RFID 리더기 같은 주변기기의 데이터를 실시간으로 수집하여 로컬에 저장하고, 중앙 서버(Django)로 안정적으로 전송하는 역할을 한다.

### 1.1 주요 목표
- **실시간성:** 계량 데이터를 지연 없이 수집하고, 필요 시 웹소켓을 통해 서버로 스트리밍한다.
- **안정성 (Resilience):** 네트워크가 끊겨도 계량 업무는 중단되지 않아야 하며(Local First), 연결 복구 시 자동으로 데이터가 동기화되어야 한다.
- **확장성:** `asyncio` 기반의 비동기 아키텍처를 통해 적은 리소스로 다중 통신(HTTP, WebSocket)을 처리한다.

---

## 2. 시스템 아키텍처 (Architecture)

### 2.1 기술 스택
- **Language:** Python 3.14+
- **Package Manager:** `uv` (빠른 의존성 관리 및 배포)
- **Concurrency:** `asyncio` (Main Loop), `threading` (Serial Blocking I/O 회피)
- **Network:** `httpx` (Async HTTP), `websockets` (Async WebSocket)
- **Serial:** `pyserial`
- **Local DB:** `Tortoise ORM` (Async ORM) + `SQLite` (`aiosqlite` 드라이버)
- **Logging:** `structlog` (Structured Logging)
- **Daemon:** Systemd (Linux) / Servy (Windows)

### 2.2 구조도 (Conceptual Diagram)
```text
[Serial Hardware] --(UART/USB)--> [Thread: Serial Reader] 
                                          |
                                    (Blocking Read)
                                          |
                                          v
                                   [Asyncio Queue] <--(Bridge)--+
                                          |                     |
                                          v                     |
[Local DB (SQLite)] <--(Save)-- [Main Event Loop (FSM)] --------+
                                          |
                        +-----------------+-----------------+
                        |                 |                 |
                  (HTTP REST)         (WebSocket)       (Heartbeat)
                        |                 |                 |
                        v                 v                 v
                 [Django Server: API & Channels]
```

### 2.3 스레딩 모델 (Hybrid Model)

1. **Serial Thread (Producer):** `pyserial`의 `readline()` 같은 Blocking I/O를 전담. 데이터를 읽는 즉시 `loop.call_soon_threadsafe()`를 통해 Main Loop의 Queue로 주입.
2. **Main Event Loop (Consumer):** 단일 스레드(Single Thread)에서 동작. Queue 처리, FSM 상태 전이, HTTP 요청, WebSocket 통신을 모두 비동기(`await`)로 처리.

---

## 3. 핵심 기능 요구사항 (Functional Requirements)

### FR-01: 기기 등록 및 인증 (Registration & Sync)

프로그램 시작 시 다음의 상태 흐름을 따른다 (`ClientState`).

1. **INITIALIZE:** 로컬 DB(`Gateway` 테이블)를 확인한다. 유효한 `Access Token`이 있으면 즉시 `HEARTBEAT` 상태로 전이한다. 없으면 `SYNC` 상태로 진입한다.
2. **SYNC:** 서버에 기기 정보(`MAC Address` 기반)를 조회한다.
* **Not Found (404):** 서버에 등록되지 않은 기기이므로 `REGISTER` 상태로 전이한다.
* **Pending:** 등록은 되었으나 승인 대기 중인 경우, 일정 시간 대기 후 다시 `SYNC`를 시도한다.
* **Approved:** 서버로부터 토큰을 받아 로컬 DB에 저장하고 `HEARTBEAT` 상태로 전이한다.


3. **REGISTER:** 서버에 신규 기기 등록 요청(`POST`)을 보낸다. 등록 성공 시 `SYNC` 상태로 돌아가 토큰 발급을 확인한다.

### FR-02: 데이터 수집 및 파싱 (Data Acquisition)

* 시리얼 포트로부터 들어오는 Raw Data를 파싱하여 유형(`RFID` 또는 `WEIGHT`)을 구분한다.
* **RFID:** 태그 ID(UID) 추출.
* **WEIGHT:** 무게 값(float) 및 단위 추출. 노이즈 필터링 필요 시 적용.

### FR-03: 계량 프로세스 (FSM Logic)

프로그램은 다음 상태(State)를 가진다.

1. **BOOT:** 초기화 및 설정 로드.
2. **UNREGISTERED:** 서버 등록 대기.
3. **IDLE:** 대기 상태. RFID 태그를 기다림.
4. **MEASURING:** 계량 중. RFID가 태그된 상태에서 무게가 안정화되기를 기다림.

**[상세 로직]**

* `IDLE` 상태에서 RFID 태그 감지 -> `MEASURING`으로 전이.
* `MEASURING` 상태에서 무게 데이터 수집 (Buffer 저장).
* 무게가 일정 시간(예: 1초) 동안 변동폭 내에서 유지되면 **"안정(Stable)"**으로 판단.
* 안정된 무게를 **로컬 DB에 즉시 저장**(`is_sent=False`)하고 `IDLE`로 복귀.

### FR-04: 데이터 동기화 (Store-and-Forward)

* **백그라운드 Task**가 주기적으로(예: 5초) 로컬 DB에서 `is_sent=False`인 기록을 조회.
* 서버 API로 전송(`POST`)하고, `201 Created` 응답을 받으면 로컬 DB의 해당 기록을 `is_sent=True`로 마킹.
* **Idempotency:** 네트워크 오류로 재전송되더라도 중복 저장되지 않도록 `UUID`를 클라이언트에서 생성하여 보낸다.

### FR-05: 실시간 스트리밍 (WebSocket Throttling)

* 서버의 요청(`START_STREAM` 커맨드)이 있을 때만 무게 데이터를 전송한다.
* **Throttling:** 시리얼 데이터(50Hz)를 그대로 보내지 않고, 최대 전송 빈도(예: 10Hz, 100ms 간격)를 설정하여 샘플링 전송한다.
* `MEASURING` 상태 여부와 관계없이 현재 저울에 올라온 무게를 전송해야 한다.

### FR-06: 헬스 체크 (Heartbeat)

* 주기적(기본 30초)으로 서버로 생존 신호를 보낸다.
* 인증 실패(401, 403) 또는 기기 삭제(404) 응답 시, 로컬 데이터를 초기화하고 `REGISTER` 상태로 돌아가 재등록 절차를 밟는다.

---

## 4. 데이터베이스 스키마 (Local SQLite)

### 4.1 `gateway`

기기 설정 및 인증 정보를 저장. (`Gateway` 모델)
| Field | Type | Description |
|---|---|---|
| `mac_address` | VARCHAR(17) | MAC 주소 (PK) |
| `hostname` | VARCHAR(255) | 호스트명 |
| `ip_address` | VARCHAR(15) | IP 주소 |
| `access_token` | VARCHAR(64) | API 인증 토큰 |
| `status` | VARCHAR(10) | 기기 상태 (Active, Inactive 등) |
| `last_heartbeat` | DATETIME | 마지막 통신 시각 |
| `created_at` | DATETIME | 생성 시각 |
| `updated_at` | DATETIME | 수정 시각 |

### 4.2 `weighing_records`

계량된 실적을 저장. (`WeighingRecord` 모델 예정)
| Field | Type | Description |
|---|---|---|
| `uuid` | CHAR(36) | 고유 ID (PK) |
| `rfid_uid` | VARCHAR(50) | 태그된 RFID |
| `weight` | FLOAT | 측정 무게 |
| `measured_at` | DATETIME | 측정 시각 |
| `is_sent` | BOOLEAN | 서버 전송 여부 (Index) |

---

## 5. 인터페이스 명세 (Server API Contract)

Django 서버(`devices`, `weighing` 앱)는 다음 API를 제공해야 한다.

### 5.1 기기 관리 (`devices`)

* `GET devices/api/gateways/{mac_address}/`
* 기기 상태 및 토큰 발급 여부 확인 (Sync).
* `POST devices/api/gateways/`
* 신규 기기 등록 요청.
* **Body:** `{ "mac_address": "...", "hostname": "...", "ip_address": "...", "name": "..." }`
* `POST devices/api/gateways/heartbeat/`
* 생존 신고.
* **Header:** `Authorization: Bearer {access_token}`

### 5.2 계량 처리 (`weighing`)

* `POST api/weighing/records/` (예정)
* 계량 기록 업로드.
* **Body:** `{uuid, rfid_uid, weight, measured_at}`

### 5.3 웹소켓 채널

* URL: `ws://{server}/ws/devices/{gateway_id}/`
* **Client -> Server:** `{"type": "weight_update", "value": 10.5}`
* **Server -> Client:** `{"type": "START_STREAM"}`, `{"type": "STOP_STREAM"}`

---

## 6. 배포 및 운영 전략 (Deployment)

### 6.1 설치 자동화

* **Windows:** `install_win.ps1`
* `winget`으로 Git, Python 설치 확인.
* `Servy`를 통해 Windows Service로 등록.
* **Linux:** `install_linux.sh`
* `apt` 패키지 설치.
* `systemd` unit 파일 생성 및 등록.

### 6.2 업데이트

* 서비스 재시작 시 자동으로 `git pull`을 수행하도록 스크립트 구성 (선택 사항) 또는 별도 업데이트 명령어 제공.
* `uv sync`를 통해 의존성 최신화.
