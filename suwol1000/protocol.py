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
    OFF    = 0
    GREEN  = 1 << 0  # Relay 1: 녹색등 점멸
    RED    = 1 << 1  # Relay 2: 적색등 점멸
    RELAY3 = 1 << 2  # Relay 3: 모름
    FAN    = 1 << 3  # Relay 4: 팬 작동
    HEATER = 1 << 4  # Relay 5: 히터 작동
    RELAY6 = 1 << 5  # Relay 6: 모름
    RELAY7 = 1 << 6  # Relay 7: 모름
    RELAY8 = 1 << 7  # Relay 8: 모름


# TODO(donghyeon): Define scale status codes


@dataclass(frozen=True)
class RequestPacket:
    device_id: int = 0
    command_code: CommandCode = CommandCode.DISPLAY
    display_weight: int = 0
    display_plate: str = ""
    green_blink: bool = False
    red_blink: bool = False
    voice_code: VoiceCode = VoiceCode.NONE

    def to_bytes(self) -> bytes:
        device_id_str = str(self.device_id)[-1].encode()

        command_code_str = self.command_code.encode()

        sign = "+" if self.display_weight >= 0 else "-"
        abs_weight = str(abs(self.display_weight))
        display_weight_str=f"{sign}{abs_weight:>7.7}".encode()
        display_plate_str = f"{self.display_plate[-6:]:>6}".encode()

        reserved1_str = b" " * 6

        relay_value = (
            RelayCode.GREEN * self.green_blink |
            RelayCode.RED * self.red_blink |
            RelayCode.RELAY3 * False |
            RelayCode.FAN * False |
            RelayCode.HEATER * False |
            RelayCode.RELAY6 * False |
            RelayCode.RELAY7 * False |
            RelayCode.RELAY8 * False
        )

        # *주의* hex 표현 아님: 0, 1, ..., 9, A, B, C, D, E, F
        # ascii 코드 순서 형태: 0, 1, ..., 9, :, ;, <, =, >, ?
        high_nibble = (relay_value & 0b11110000) >> 4
        low_nibble  = (relay_value & 0b00001111)
        relay_str = bytes([high_nibble + ord("0"), low_nibble + ord("0")])

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

        # *주의* hex 표현 아님: 0, 1, ..., 9, A, B, C, D, E, F
        # ascii 코드 순서 형태: 0, 1, ..., 9, :, ;, <, =, >, ?
        relay_str = data[18:20]
        high_nibble = ord(relay_str[0]) - ord("0")
        low_nibble  = ord(relay_str[1]) - ord("0")
        relay_value = (high_nibble << 4) | low_nibble
        relay_code = RelayCode(relay_value)

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
