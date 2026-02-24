# drivers.py
import threading
from enum import Enum, auto
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


class DriverState(Enum):
    INITIALIZE = auto()
    CONNECT = auto()
    IDLE = auto()
    MEASURE = auto()
    RECOVER = auto()


class WeighingStationDriver:
    def __init__(
        self,
        port: str,
        stop_event: threading.Event | None = None,
        on_event: Callable[[BaseEvent], None] | None = None,
    ):
        self.port = port
        self.on_event = on_event

        self.state = DriverState.INITIALIZE
        self.ser: serial.Serial | None = None
        self.logger = get_logger().bind(port=port)

        self.stop_event = stop_event or threading.Event()

        self.last_weight = 0
        self.last_plate = ""

        self.polling_interval = 0.1
        self.retry_interval = 10.0
    
    def stop(self):
        self.logger.info("sys.worker.stop_requested")
        self.stop_event.set()

        if self.ser and self.ser.is_open:
            try:
                self.ser.cancel_read()
                self.ser.cancel_write()
            except Exception:
                self.logger.exception("hw.serial.cancel_failed")
    
    def cleanup(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
                self.logger.info("hw.serial.closed")
            except Exception:
                self.logger.exception("hw.serial.close_failed")
    
    def emit_event(self, event: BaseEvent):
        if self.on_event:
            self.on_event(event)
    
    def initialize(self) -> DriverState:
        self.logger.info("sys.worker.startup", next_state="CONNECT")
        return DriverState.CONNECT
    
    def connect(self) -> DriverState:
        self.logger.debug("hw.serial.connecting")
        self.ser = serial.Serial(self.port, timeout=1.0)
        self.ser.reset_input_buffer()
        self.logger.info("hw.serial.connected", next_state="IDLE")
        return DriverState.IDLE
    
    def idle(self) -> DriverState:
        request_packet = RequestPacket(display_weight=self.last_weight)
        self.ser.write(request_packet.to_bytes())

        response = self.ser.read_until(expected=ETX)

        if self.stop_event.is_set():
            self.logger.debug("hw.serial.read_cancelled_by_interrupt")
            return DriverState.IDLE
        
        if not response or not response.endswith(ETX):
            self.logger.warning("hw.serial.read_timeout_or_incomplete", response=response)
            return DriverState.RECOVER

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
            return DriverState.MEASURE
        
        self.stop_event.wait(self.polling_interval)
        return DriverState.IDLE
    
    def measure(self) -> DriverState:
        voice_codes = [
            VoiceCode.PLEASE_WAIT,
            VoiceCode.WEIGHT_COMPLETE,
            VoiceCode.THANK_YOU,
        ]
        
        is_speaker_on = False
        for voice_code in voice_codes:
            if self.stop_event.is_set():
                return DriverState.IDLE
            
            self.logger.info("hw.speaker.on", voice=voice_code.name)
            while not self.stop_event.is_set():
                request_packet = RequestPacket(
                    display_weight=self.last_weight,
                    display_plate=self.last_plate,
                    green_blink=True,
                    voice_code=VoiceCode.NONE if is_speaker_on else voice_code,
                )

                self.ser.write(request_packet.to_bytes())
                response = self.ser.read_until(expected=ETX)

                if self.stop_event.is_set():
                    return DriverState.IDLE
                
                if not response or not response.endswith(ETX):
                    self.logger.warning("hw.serial.read_timeout_or_incomplete", response=response)
                    return DriverState.RECOVER
                
                response_packet = ResponsePacket.from_bytes(response)
                if response_packet.current_weight != self.last_weight:
                    self.last_weight = response_packet.current_weight
                is_speaker_on = response_packet.voice_code != VoiceCode.NONE

                self.stop_event.wait(self.polling_interval)
                if not is_speaker_on:
                    break
        
        self.logger.info("hw.weighing.completed", next_state="IDLE")
        self.emit_event(WeighingCompletedEvent(rfid_card_uid=self.last_plate, weight=self.last_weight))
        return DriverState.IDLE

    def recover(self) -> DriverState:
        self.logger.info("sys.worker.recovery_scheduled", retry_in=self.retry_interval, next_state="CONNECT")
        if self.ser and self.ser.is_open:
            self.ser.close()
        
        self.stop_event.wait(self.retry_interval)
        return DriverState.CONNECT

    def run(self):
        self.logger.info("sys.worker.started")

        while not self.stop_event.is_set():
            structlog.contextvars.bind_contextvars(state=self.state.name)
            try:
                match self.state:
                    case DriverState.INITIALIZE:
                        self.state = self.initialize()
                    case DriverState.CONNECT:
                        self.state = self.connect()
                    case DriverState.IDLE:
                        self.state = self.idle()
                    case DriverState.MEASURE:
                        self.state = self.measure()
                    case DriverState.RECOVER:
                        self.state = self.recover()
            except serial.SerialTimeoutException:
                self.logger.exception("hw.serial.timeout")
                self.state = DriverState.RECOVER

            except serial.SerialException:
                self.logger.exception("hw.serial.connection_lost")
                self.state = DriverState.RECOVER
            
            except ValueError:
                self.logger.exception("hw.protocol.parse_error")
                self.state = DriverState.IDLE
            
            except Exception:
                self.logger.exception("sys.worker.unexpected_error")
                self.state = DriverState.RECOVER
                self.stop_event.wait(1.0)

        self.cleanup()
        self.logger.info("sys.worker.terminated")        


def main():
    setup_logging()
    logger = get_logger()

    worker = WeighingStationDriver(port="COM3")
    worker_thread = threading.Thread(target=worker.run)
    worker_thread.start()

    try:
        worker_thread.join()
    except KeyboardInterrupt:
        logger.warning("sys.main.keyboard_interrupt_detected")
        worker.stop()
        worker_thread.join()
        logger.info("sys.main.shutdown_complete")


if __name__ == "__main__":
    main()
