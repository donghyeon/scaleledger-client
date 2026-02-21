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
from utils import get_mac_address, get_ip_address, get_hostname, scan_peripherals


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


class AuthDegradedError(Exception):
    pass


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
        self.gateway_id: int | None = None

    async def close(self):
        await self.api_client.close()
        await Tortoise.close_connections()
        self.logger.info("sys.lifecycle.process.shutdown")

    async def setup(self):
        self.logger.info("sys.lifecycle.process.startup", server_url=self.api_client.client.base_url)
        await Tortoise.init(
            db_url="sqlite://db.sqlite3",
            modules={"models": ["models"]},
        )
        await Tortoise.generate_schemas()
        self.logger.debug("sys.db.schema.ready")
    
    async def wipe_local_auth(self):
        self.logger.warning("sys.auth.local_db.wipe")
        await Gateway.all().delete()
        self.access_token = None
        self.gateway_id = None
    
    async def bootstrap(self):
        self.logger.debug("sys.boot.state.evaluating")

        if not self.access_token:
            gateway = await Gateway.get_or_none(mac_address=self.mac_address)
            if gateway:
                self.access_token = gateway.access_token
                self.gateway_id = gateway.id
                self.logger.info("sys.boot.local_cache.loaded", gateway_id=self.gateway_id)
            else:
                self.logger.info("sys.boot.auth.missing", action="require_provisioning")
                return
        
        self.logger.info("sys.boot.remote_api.syncing")
        try:
            gateway_data = await self.api_client.retrieve_gateway_self(self.access_token)

            gateway, _ = await Gateway.update_or_create(
                mac_address=self.mac_address,
                defaults={
                    "id": gateway_data["id"],
                    "hostname": gateway_data["hostname"],
                    "ip_address": gateway_data["ip_address"],
                    "name": gateway_data["name"],
                    "description": gateway_data["description"],
                    "access_token": gateway_data["access_token"],
                    "last_heartbeat": gateway_data["last_heartbeat"],
                    "created_at": gateway_data["created_at"],
                    "updated_at": gateway_data["updated_at"],
                }
            )
            self.gateway_id = gateway.id
            self.logger.info("sys.boot.remote_api.success", gateway_id=self.gateway_id)
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                self.logger.warning("sys.boot.auth.rejected", status=e.response.status_code)
                await self.wipe_local_auth()
            else:
                self.logger.exception("sys.boot.remote_api.error", status=e.response.status_code)
        except httpx.RequestError:
            self.logger.warning("sys.boot.network.offline", action="fallback_to_local_cache")

    async def run(self):
        setup_logging()
        await self.setup()

        while True:
            await self.bootstrap()

            try:
                if not self.access_token:
                    await self.run_provisioning_loop()
                else:
                    await self.run_active_loop()
            except AuthDegradedError:
                self.logger.exception("sys.loop.auth_degraded", action="wipe_and_retry")
                await self.wipe_local_auth()
            except (websockets.exceptions.ConnectionClosed, OSError):
                self.logger.exception("net.ws.connection_lost", retry_in=self.retry_interval)
                await asyncio.sleep(self.retry_interval)
            except asyncio.CancelledError:
                self.logger.info("sys.loop.cancelled")
                break
            except Exception:
                self.logger.exception("sys.loop.unexpected_crashed", retry_in=self.retry_interval)
                await asyncio.sleep(self.retry_interval)
    
    async def run_provisioning_loop(self):
        self.logger.info("net.ws.provisioning.connecting", url=self.provisioning_url)
        async with websockets.connect(self.provisioning_url) as ws:
            self.logger.info("net.ws.provisioning.connected")
            async for message in ws:
                await self.dispatch_provisioning(ws, message)

                if self.access_token:
                    self.logger.info("biz.provisioning.handover_ready")
                    break
    
    async def dispatch_provisioning(self, websocket, message: str):
        try:
            data = json.loads(message)
            message_type = data["type"]
            match message_type:
                case "identify":
                    self.logger.info("biz.provisioning.identify.received")
                    await websocket.send(json.dumps({
                        "type": "identity",
                        "payload": {
                            "mac_address": self.mac_address,
                            "hostname": self.hostname,
                            "ip_address": self.ip_address,
                        },
                    }))
                case "gateway.registered":
                    self.logger.info("biz.provisioning.registered.received")
                    new_token = data["payload"]["access_token"]
                    if new_token:
                        self.access_token = new_token
                case _:
                    self.logger.warning("net.ws.message.ignored", type=message_type)
        except json.JSONDecodeError:
            self.logger.error("net.ws.message.invalid_json", message=message)
    
    async def run_active_loop(self):
        target_ws_url = f"{self.ws_url}/ws/devices/gateways/{self.gateway_id}/"
        self.logger.info("net.ws.active.connecting", url=target_ws_url)

        async with websockets.connect(target_ws_url) as ws:
            self.logger.info("net.ws.active.connected")
            
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.listen_active_ws(ws))
                tg.create_task(self.heartbeat_worker())
    
    async def listen_active_ws(self, ws):
        async for message in ws:
            try:
                data = json.loads(message)
                message_type = data["type"]

                match message_type:
                    case "scan.peripherals":
                        self.logger.info("biz.active.scan_peripherals.executing")
                        peripherals = scan_peripherals()
                        await ws.send(json.dumps({
                            "type": "peripherals.scanned",
                            "payload": peripherals,
                        }))
                        self.logger.info("biz.active.scan_peripherals.completed", count=len(peripherals))
                    case _:
                        self.logger.debug("net.ws.message.ignored", type=message_type)
            except json.JSONDecodeError:
                self.logger.error("net.ws.message.invalid_json")

    async def heartbeat_worker(self):
        while True:
            try:
                self.logger.debug("net.api.heartbeat.sending")
                await self.api_client.send_heartbeat(self.access_token)
                self.logger.debug("net.api.heartbeat.success")
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403, 404):
                    self.logger.error("net.api.heartbeat.auth_rejected", status=e.response.status_code)
                    raise AuthDegradedError("Heartbeat auth failed")
                self.logger.error("net.api.heartbeat.server_error", status=e.response.status_code)
            except httpx.RequestError:
                self.logger.warning("net.api.heartbeat.network_error")
            
            await asyncio.sleep(30)


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
