# suwol1000/protocol.py
from dataclasses import dataclass
from enum import IntEnum, StrEnum, IntFlag


STX = b"\x02"
ETX = b"\x03"


class CommandCode(StrEnum):
    DISPLAY = "D"  # 기본 표시 및 제어
    PRINT = "P"    # 프린터 출력
    TEMP = "T"     # 온도 설정


class VoiceCode(IntEnum):
    NONE = 0               # (출력 없음)
    WEIGHT_COMPLETE = 1    # 계량이 끝났습니다.
    STAND_BY = 2           # 잠시 대기하십시오.
    PLEASE_WAIT = 3        # 잠시만 기다려주십시오.
    TAG_CARD = 4           # 카드를 대어주십시오.
    INDICATOR_ERROR = 5    # 인디케이터 이상입니다.
    OVERLOAD = 6           # 과적입니다.
    CHECK_ADMIN = 7        # 관리자에게 확인하십시오.
    UNREGISTERED_CARD = 8  # 등록되지 않은 카드입니다.
    WARNING_BEEP = 9       # 삐삐삐삐삐 (단순 경고음)
    SYSTEM_ERROR = 10      # 시스템 이상입니다. 잠시만 기다려주십시오.
    THANK_YOU = 11         # 이용해주셔서 감사합니다.
    ALERT_SOUND = 12       # 찌잉 (경고음)


class InputCode(StrEnum):
    NONE = "0"               # (입력 없음)
    VEHICLE_NO = "N"         # 차량번호
    CUSTOMER_CODE = "C"      # 거래처
    PRODUCT_CODE = "M"       # 제품
    REPRINT = "P"            # 전표 재발행


class PrinterStatus(IntEnum):
    NORMAL = 0        # 정상
    NO_PAPER = 1      # 용지없음
    TRANSMITTING = 2  # 전송중


class RelayCode(IntFlag):
    OFF = 0x00
    GREEN = 0x01   # Relay 1: 녹색등 점멸
    RED = 0x02     # Relay 2: 적색등 점멸
    FAN = 0x08     # Relay 4: 팬 작동
    HEATER = 0x10  # Relay 5: 히터 작동


# TODO(donghyeon): Define scale status codes


@dataclass(frozen=True)
class RequestPacket:
    device_id: int = 0
    command_code: CommandCode = CommandCode.DISPLAY
    display_weight: str = "+0000000"
    display_plate: str = "      "
    green_blink: bool = False
    red_blink: bool = False
    voice_code: VoiceCode = VoiceCode.NONE

    def to_bytes(self) -> bytes:
        device_id_str = str(self.device_id)[-1].encode()

        command_code_str = self.command_code.encode()

        display_weight_str = f"{self.display_weight:>8}"[:8].encode()
        display_plate_str = f"{self.display_plate:>6}"[:6].encode()

        reserved1_str = b" " * 6

        relay_value = RelayCode.GREEN * self.green_blink
        relay_value |= RelayCode.RED * self.red_blink
        relay_str = f"{relay_value:02X}".encode()

        voice_index_str = f"{self.voice_code:02d}".encode()

        reserved2_str = b" " * 4
        
        return (
            STX +                 # 1 byte
            device_id_str +       # 1 byte
            command_code_str +    # 1 byte
            display_weight_str +  # 8 bytes
            display_plate_str +   # 6 bytes
            reserved1_str +       # 6 bytes
            relay_str +           # 2 bytes
            voice_index_str +     # 2 bytes
            reserved2_str +       # 4 bytes
            ETX                   # 1 byte
        )


@dataclass(frozen=True)
class ResponsePacket:
    device_id: int = 0
    command_code: CommandCode = CommandCode.DISPLAY
    rfid_card_uid: str = "00000000"
    user_command_code: InputCode = InputCode.NONE
    user_input: str = "000000"
    green_blink: bool = False
    red_blink: bool = False
    fan_on: bool = False
    heater_on: bool = False
    unknown_input: str = "00"
    voice_code: VoiceCode = VoiceCode.NONE
    inner_temperature: int = 0
    fan_trigger_temp: int = 30
    heater_trigger_temp: int = 5
    printer_status: PrinterStatus = PrinterStatus.NORMAL
    is_weight_stable: bool = False
    current_weight: int = 0

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ResponsePacket":
        data = raw.decode()
        if len(data) != 53:
            raise ValueError(f"Invalid response packet length: {len(data)} bytes")
        if data[0] != STX.decode() or data[-1] != ETX.decode():
            raise ValueError(f"Invalid STX/ETX")
        
        device_id = int(data[1])
        command_code = CommandCode(data[2])
        rfid_card_uid = data[3:11]
        user_command_code = InputCode(data[11])
        user_input = data[12:18]
        relay_code = RelayCode(int(data[18:20], 16))
        green_blink = RelayCode.GREEN in relay_code
        red_blink = RelayCode.RED in relay_code
        fan_on = RelayCode.FAN in relay_code
        heater_on = RelayCode.HEATER in relay_code
        unknown_input = data[20:22]
        voice_code = VoiceCode(int(data[22:24]))
        inner_temperature = int(data[24:27])
        fan_trigger_temp = int(data[27:29])
        heater_trigger_temp = int(data[29:31])
        printer_status = PrinterStatus(int(data[31]))
        is_weight_stable = data[36:38] == "ST"
        current_weight = int(data[42] + data[43:50].lstrip())

        return cls(
            device_id=device_id,
            command_code=command_code,
            rfid_card_uid=rfid_card_uid,
            user_command_code=user_command_code,
            user_input=user_input,
            green_blink=green_blink,
            red_blink=red_blink,
            fan_on=fan_on,
            heater_on=heater_on,
            unknown_input=unknown_input,
            voice_code=voice_code,
            inner_temperature=inner_temperature,
            fan_trigger_temp=fan_trigger_temp,
            heater_trigger_temp=heater_trigger_temp,
            printer_status=printer_status,
            is_weight_stable=is_weight_stable,
            current_weight=current_weight,
        )


# TODO(donghyeon): Define communication packets for printer output

# TODO(donghyeon): Define communication packets for fan/heater settings
