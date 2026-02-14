# worker.py
from enum import Enum, auto
import time
from typing import Callable


import serial
import structlog
from structlog.stdlib import get_logger

from suwol1000.protocol import RequestPacket, ResponsePacket, VoiceCode, ETX

from events import BaseEvent, RFIDTaggedEvent, WeighingCompletedEvent


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
    IDLE = auto()
    MEASURE = auto()
    RECOVER = auto()


class WeighingStationWorker:
    def __init__(
        self,
        port: str,
        on_event: Callable[[BaseEvent], None] | None = None,
    ):
        self.port = port
        self.on_event = on_event

        self.state = WorkerState.INITIALIZE
        self.ser: serial.Serial | None = None
        self.logger = get_logger().bind(port=port)

        self.last_weight = 0
        self.last_plate = ""

        self.polling_interval = 0.1
        self.retry_interval = 10

        self.is_speaker_on = False
    
    def initialize(self) -> WorkerState:
        self.logger.info("sys.worker.startup", next_state="CONNECT")
        return WorkerState.CONNECT
    
    def connect(self) -> WorkerState:
        self.logger.debug("hw.serial.connecting")
        self.ser = serial.Serial(self.port, timeout=1.0)
        self.ser.reset_input_buffer()
        self.logger.info("hw.serial.connected", next_state="IDLE")
        return WorkerState.IDLE

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.logger.info("hw.serial.closed")
        else:
            self.logger.debug("hw.serial.already_closed")
    
    def emit_event(self, event: BaseEvent):
        if self.on_event:
            self.on_event(event)
    
    def idle(self) -> WorkerState:
        request_packet = RequestPacket(display_weight=self.last_weight)
        self.ser.write(request_packet.to_bytes())

        response = self.ser.read_until(expected=ETX)
        try:
            response_packet = ResponsePacket.from_bytes(response)
            if response_packet.current_weight != self.last_weight:
                self.last_weight = response_packet.current_weight
            
            if response_packet.rfid_card_uid != "00000000":
                self.last_plate = response_packet.rfid_card_uid
                self.logger.info(
                    "hw.rfid.detected",
                    rfid_card_uid=response_packet.rfid_card_uid,
                    next_state="MEASURE",
                )
                self.emit_event(RFIDTaggedEvent(rfid_card_uid=response_packet.rfid_card_uid))
                return WorkerState.MEASURE

        except ValueError:
            self.logger.exception("hw.protocol.parse_error", response=response)
        
        time.sleep(self.polling_interval)
        return WorkerState.IDLE
    
    def measure(self) -> WorkerState:
        voice_codes = [
            VoiceCode.PLEASE_WAIT,
            VoiceCode.WEIGHT_COMPLETE,
            VoiceCode.THANK_YOU,
        ]
        
        is_speaker_on = False
        for voice_code in voice_codes:
            self.logger.info("hw.speaker.on", voice=voice_code.name)
            while True:
                request_packet = RequestPacket(
                    display_weight=self.last_weight,
                    display_plate=self.last_plate,
                    green_blink=True,
                    voice_code=VoiceCode.NONE if is_speaker_on else voice_code,
                )
                self.ser.write(request_packet.to_bytes())

                response = self.ser.read_until(expected=ETX)
                try:
                    response_packet = ResponsePacket.from_bytes(response)
                    if response_packet.current_weight != self.last_weight:
                        self.last_weight = response_packet.current_weight
                    is_speaker_on = response_packet.voice_code != VoiceCode.NONE
                except ValueError:
                    self.logger.exception("hw.protocol.parse_error", response=response)
                time.sleep(self.polling_interval)
                if not is_speaker_on:
                    break
        
        self.logger.info("hw.weighing.completed", next_state="IDLE")
        self.emit_event(WeighingCompletedEvent(rfid_card_uid=self.last_plate, weight=self.last_weight))
        return WorkerState.IDLE

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
                    case WorkerState.IDLE:
                        self.state = self.idle()
                    case WorkerState.MEASURE:
                        self.state = self.measure()
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
