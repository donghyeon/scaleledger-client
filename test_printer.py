# test_printer.py
import time
import dataclasses
from decimal import Decimal
from datetime import datetime
from textwrap import dedent
import uuid

import structlog
from structlog.stdlib import get_logger

from suwol1000 import (
    SerialClient,
    DisplayRequestPacket,
    PrinterRequestPacket,
    PrinterStatus,
    ResponsePacket,
)
from printer import Receipt, ReceiptTemplate

def setup_test_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )

def log_response_packet_details(logger, res: ResponsePacket):
    packet_dump = dedent(f"""
        ===================================================
        📡 [Response Packet Snapshot]
        ===================================================
        🖥️  [Device & Command]
        - Device ID      : {res.device_id}
        - Command Code   : {res.command_code.name} ('{res.command_code.value}')

        💳  [Input & RFID]
        - RFID UID       : {res.rfid_card_uid}
        - User Command   : {res.user_command_code.name} ('{res.user_command_code.value}')
        - User Input     : {res.user_input}

        🚦  [Relay & Voice]
        - Green Light    : {'🟢 ON' if res.green_blink else '⚪ OFF'}
        - Red Light      : {'🔴 ON' if res.red_blink else '⚪ OFF'}
        - Fan            : {'🌀 ON' if res.fan_on else '⚪ OFF'}
        - Heater         : {'🔥 ON' if res.heater_on else '⚪ OFF'}
        - Voice Code     : {res.voice_code.name} ({res.voice_code.value})

        🌡️  [Environment & Printer]
        - Inner Temp     : {res.inner_temperature}°C
        - Fan Trigger    : {res.fan_trigger_temp}°C
        - Heater Trigger : {res.heater_trigger_temp}°C
        - Printer Status : {res.printer_status.name} ({res.printer_status.value})

        ⚖️  [Weight Data]
        - Weight Status  : {res.weight_status.name}
        - Weight Type    : {res.weight_type.name}
        - Weight Value   : {res.weight_value} {res.weight_unit}
        ===================================================
    """)
    print(packet_dump)
    logger.debug("hw.serial.packet_dumped", device_id=res.device_id, printer=res.printer_status.name, weight=str(res.weight_value))

def print_packet_diff(logger, prev_res: ResponsePacket, curr_res: ResponsePacket):
    if prev_res is None:
        return

    diffs = {}
    for field in dataclasses.fields(curr_res):
        field_name = field.name
        prev_val = getattr(prev_res, field_name)
        curr_val = getattr(curr_res, field_name)
        
        if prev_val != curr_val:
            prev_str = f"{prev_val.name}({prev_val.value})" if hasattr(prev_val, 'name') else str(prev_val)
            curr_str = f"{curr_val.name}({curr_val.value})" if hasattr(curr_val, 'name') else str(curr_val)
            diffs[field_name] = f"{prev_str} -> {curr_str}"
    
    if diffs:
        print("\n🔄 [Status Changed]")
        for key, value in diffs.items():
            print(f"  - {key:<20}: {value}")
        
        logger.debug("hw.serial.packet_changed", changed_keys=list(diffs.keys()))

def run_printer_test(port: str):
    logger = get_logger().bind(port=port)
    logger.info("sys.test.printer_test.started")

    client = SerialClient(port=port, timeout=1.0)
    
    try:
        logger.debug("hw.serial.connection.opening")
        client.connect()
        logger.info("hw.serial.connection.established")

        # 1. 상태 사전 점검
        logger.info("hw.printer.status.checking")
        pre_check_req = DisplayRequestPacket(display_weight=Decimal("0.0"))
        pre_check_res = client.send_and_receive(pre_check_req)

        log_response_packet_details(logger, pre_check_res)

        if pre_check_res.printer_status != PrinterStatus.NORMAL:
            logger.error("hw.printer.status.abnormal", action="abort_test")
            return

        # 2. 템플릿 렌더링 및 프린터 명령 전송
        test_receipt = Receipt(
            record_uuid=uuid.uuid4(),
            rfid_card_uid="A1b2c3D4",
            producer_name="(주)CJ 프레시웨이",
            species_name="미트볼 Pasta",
            weight=Decimal("1850.0"),
            measured_at=datetime.now()
        )
        
        document_data = ReceiptTemplate.render(test_receipt)
        print_req = PrinterRequestPacket(copies=1, document_bytes=document_data)
        
        logger.info("hw.printer.command.sending", payload_length=len(document_data))
        client.serial.write(print_req.to_bytes())

        # 3. 상태 모니터링 폴링
        logger.info("hw.printer.status.polling_started", interval="100ms")
        
        start_time = time.time()
        timeout = 10.0
        
        prev_res = pre_check_res
        has_transmitted = False
        
        while time.time() - start_time < timeout:
            poll_req = DisplayRequestPacket(display_weight=Decimal("-8888.8")) 
            curr_res = client.send_and_receive(poll_req)
            
            print_packet_diff(logger, prev_res, curr_res)
            
            if curr_res.printer_status == PrinterStatus.TRANSMITTING:
                has_transmitted = True
                
            if has_transmitted and curr_res.printer_status == PrinterStatus.NORMAL:
                logger.info(
                    "hw.printer.print_completed", 
                    elapsed_time=f"{time.time() - start_time:.2f}s"
                )
                break
                
            if curr_res.printer_status == PrinterStatus.NO_PAPER:
                logger.error("hw.printer.error.no_paper_detected")
                break
                
            prev_res = curr_res
            time.sleep(0.1)
            
        else:
            logger.warning("hw.printer.status.polling_timeout", timeout=timeout)

    except Exception:
        logger.exception("sys.test.printer_test.failed")
    finally:
        client.disconnect()
        logger.info("hw.serial.connection.closed")
        logger.info("sys.test.printer_test.completed")

if __name__ == "__main__":
    setup_test_logging()
    TEST_PORT = "COM3" 
    run_printer_test(TEST_PORT)
