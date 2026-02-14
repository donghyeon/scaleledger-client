# worker.py
import time
from enum import Enum, auto

import serial
import structlog
from structlog.stdlib import get_logger

from suwol1000.protocol import RequestPacket, ResponsePacket, ETX


def setup_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # 개발 환경에서는 ConsoleRenderer, 배포 환경에서는 JSONRenderer 권장
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class WorkerState(Enum):
    INITIALIZE = auto()
    CONNECT = auto()
    STANDBY = auto()
    RECOVER = auto()


class WeighingStationWorker:
    def __init__(self, port: str):
        self.port = port
        self.state = WorkerState.INITIALIZE
        self.ser: serial.Serial | None = None
        self.logger = get_logger().bind(port=port)

        self.last_weight = 0
        self.last_plate = ""

        self.polling_interval = 0.1
        self.retry_interval = 10
    
    def initialize(self) -> WorkerState:
        self.logger.info("sys.worker.startup", next_state="CONNECT")
        return WorkerState.CONNECT
    
    def connect(self) -> WorkerState:
        self.logger.debug("hw.serial.connecting")
        self.ser = serial.Serial(self.port, timeout=1.0)
        self.ser.reset_input_buffer()
        self.logger.info("hw.serial.connected", next_state="STANDBY")
        return WorkerState.STANDBY

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.logger.info("hw.serial.closed")
        else:
            self.logger.debug("hw.serial.already_closed")
    
    def standby(self) -> WorkerState:
        request_packet = RequestPacket(
            display_weight=self.last_weight,
            display_plate=self.last_plate,
        )
        self.ser.write(request_packet.to_bytes())

        response = self.ser.read_until(expected=ETX)
        try:
            response_packet = ResponsePacket.from_bytes(response)
            if response_packet.current_weight != self.last_weight:
                self.logger.info("hw.scale.weight_changed", packet=response_packet)
                self.last_weight = response_packet.current_weight
        except ValueError:
            self.logger.exception("hw.protocol.parse_error", response=response)
        
        time.sleep(self.polling_interval)
        return WorkerState.STANDBY
    
    def recover(self) -> WorkerState:
        self.close()
        self.logger.info("sys.worker.recovery_scheduled", retry_in=self.retry_interval, next_state="CONNECT")
        time.sleep(self.retry_interval)
        return WorkerState.CONNECT

    def run(self):
        while True:
            structlog.contextvars.bind_contextvars(state=self.state.name)
            try:
                match self.state:
                    case WorkerState.INITIALIZE:
                        self.state = self.initialize()
                    case WorkerState.CONNECT:
                        self.state = self.connect()
                    case WorkerState.STANDBY:
                        self.state = self.standby()
                    case WorkerState.RECOVER:
                        self.state = self.recover()
            except serial.SerialTimeoutException:
                self.logger.exception("hw.serial.timeout")
            except serial.SerialException:
                self.logger.exception("hw.serial.connection_lost")
                self.state = WorkerState.RECOVER


def main():
    setup_logging()
    logger = get_logger()

    worker = WeighingStationWorker(port="COM3")
    try:
        worker.run()
    except KeyboardInterrupt:
        logger.error("sys.worker.shutdown_requested", reason="keyboard_interrupt")
    except Exception:
        logger.exception("sys.worker.fatal_error")
    finally:
        worker.close()
        logger.info("sys.worker.stopped")


if __name__ == "__main__":
    main()
