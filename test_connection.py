# test_connection.py
import serial
import time
from suwol1000.protocol import (
    RequestPacket, 
    ResponsePacket, 
    CommandCode, 
    VoiceCode, 
    RelayCode,
    STX, 
    ETX
)

SERIAL_PORT = "COM3"
DEVICE_ID = 0  # 프로토콜 정의에 따라 int형 (0~9)

def main():
    print(f"Checking connection to {SERIAL_PORT}...")
    
    try:
        with serial.Serial(SERIAL_PORT, timeout=5.0) as ser:
            # 시리얼 버퍼 초기화
            ser.reset_input_buffer()
            print(f"✅ Serial Port Opened: {ser.name}")

            # ---------------------------------------------------------
            # 1. 요청 패킷 생성 (RequestPacket 클래스 사용)
            # ---------------------------------------------------------
            # 기존의 build_request_packet 함수 대신 객체를 생성합니다.
            req = RequestPacket(
                device_id=DEVICE_ID,
                command_code=CommandCode.DISPLAY,
                display_weight="412",      # 표시할 중량
                display_plate="6575",      # 표시할 차량번호
                green_blink=False,         # 녹색등 점멸 테스트
                red_blink=False,           # 적색등 점멸 테스트
                voice_code=VoiceCode.NONE  # 음성 테스트
            )
            
            req_bytes = req.to_bytes()
            print(f"📤 Sending Request ({len(req_bytes)} bytes)")
            print(f"   Structure: {req}")
            print(f"   Raw bytes: {req_bytes}")
            print(f"   Relay bytes: {req_bytes[23:25]}")

            # ---------------------------------------------------------
            # 2. 전송
            # ---------------------------------------------------------
            ser.write(req_bytes)

            # ---------------------------------------------------------
            # 3. 수신
            # ---------------------------------------------------------
            start_time = time.time()
            # STX ~ ETX까지 읽거나 타임아웃
            res_bytes = ser.read_until(expected=ETX)
            end_time = time.time()

            if not res_bytes:
                print("❌ No response received (Timeout). Check cable or power.")
                return

            print(f"📥 Received Response ({len(res_bytes)} bytes)")
            print(f"   Raw Bytes: {res_bytes}")
            print(f"   Relay Bytes: {res_bytes[18:20]}")
            print(f"   ⏱ Latency: {end_time - start_time:.4f} sec")

            # ---------------------------------------------------------
            # 4. 응답 파싱 및 검증 (ResponsePacket 클래스 사용)
            # ---------------------------------------------------------
            try:
                # ResponsePacket.from_bytes()가 STX/ETX 검증 및 파싱을 수행함
                response = ResponsePacket.from_bytes(res_bytes)
                
                print("\n✅ Packet Parsed Successfully:")
                print(f"  - Device ID      : {response.device_id}")
                print(f"  - Current Weight : {response.current_weight} kg")
                print(f"  - Weight Stable  : {'Yes' if response.is_weight_stable else 'No'}")
                print(f"  - RFID Card UID  : {response.rfid_card_uid}")
                print(f"  - User Input     : {response.user_input} (Command: {response.user_command_code.name})")
                print(f"  - Status         : Fan={response.fan_on}, Heater={response.heater_on}, Printer={response.printer_status.name}")
                print(f"  - Environment    : Inner Temp={response.inner_temperature}°C")
                
            except ValueError as e:
                # 패킷 길이, STX/ETX 불일치 등 프로토콜 위반 시 발생
                print(f"⚠️ Invalid Packet Structure: {e}")

            print("\nTest completed.")

    except serial.SerialException as e:
        print(f"❌ Serial Port Error: {e}")
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")

if __name__ == "__main__":
    main()
