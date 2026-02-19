# main.py
import asyncio
import json

import httpx
import structlog
from structlog.stdlib import get_logger
from tortoise import Tortoise
import websockets

from api import APIClient
from models import Gateway
from utils import get_mac_address, get_ip_address, get_hostname


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


class HeadlessClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

        self.api_client = APIClient(base_url=self.base_url)
        self.ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.provisioning_url = f"{self.ws_url}/ws/devices/gateways/provisioning/"

        self.mac_address = get_mac_address()
        self.ip_address = get_ip_address()
        self.hostname = get_hostname()

        self.logger = get_logger()

        self.retry_interval = 5

        self.access_token: str | None = None

    async def close(self):
        await self.api_client.close()
        await Tortoise.close_connections()
        self.logger.info("sys.lifecycle.shutdown")

    async def setup(self):
        self.logger.info("sys.lifecycle.startup", server_url=self.api_client.client.base_url)
        await Tortoise.init(
            db_url="sqlite://db.sqlite3",
            modules={"models": ["models"]},
        )
        await Tortoise.generate_schemas()
        self.logger.debug("db.schema.generated")
    
    async def bootstrap(self):
        self.logger.info("boot.check.local_db")
        gateway = await Gateway.filter(mac_address=self.mac_address).first()

        if gateway:
            self.access_token = gateway.access_token
            return
        
        self.logger.debug("boot.check.remote_api")
        try:
            response = await self.api_client.retrieve_gateway(self.mac_address)
            self.logger.info("boot.check.remote_found", name=response["name"])
            gateway = await Gateway.create(**response)
            self.access_token = gateway.access_token
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self.logger.warning("boot.check.remote_not_found", status=404)
            else:
                self.logger.exception("boot.check.remote_error")

    async def run(self):
        setup_logging()
        await self.setup()
        await self.bootstrap()

        while True:
            try:
                self.logger.info("net.ws.connecting", url=self.provisioning_url)
                async with websockets.connect(self.provisioning_url) as websocket:
                    self.logger.info("net.ws.connected")
                    async for message in websocket:
                        await self.dispatch(websocket, message)

            except (ConnectionRefusedError, OSError) as e:
                self.logger.exception("net.ws.connect_failed", retry_in=self.retry_interval)
                await asyncio.sleep(self.retry_interval)
            except websockets.exceptions.ConnectionClosed:
                self.logger.exception("net.ws.connection_closed", retry_in=self.retry_interval)
                await asyncio.sleep(self.retry_interval)
            except asyncio.CancelledError:
                self.logger.info("sys.loop.cancelled")
                break
            except Exception:
                self.logger.exception("sys.loop.crashed", retry_in=self.retry_interval)
                await asyncio.sleep(self.retry_interval)
    
    async def dispatch(self, websocket, message: str):
        try:
            data = json.loads(message)
            message_type = data.get("type")

            self.logger.debug("net.ws.received", type=message_type)

            match message_type:
                case "identify":
                    await self.identify(websocket)
                case _:
                    self.logger.warning("net.ws.unknown_message", type=message_type)
        except json.JSONDecodeError:
            self.logger.error("net.ws.json_decode_error", message=message)
    
    async def identify(self, websocket):
        self.logger.info("biz.identify.executing")

        payload = {
            "type": "identity",
            "payload": {
                "mac_address": self.mac_address,
                "hostname": self.hostname,
                "ip_address": self.ip_address,
            },
        }

        await websocket.send(json.dumps(payload))
        self.logger.info("biz.identify.completed")

async def main():
    client = HeadlessClient(base_url="http://localhost:8000")
    try:
        await client.run()
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
