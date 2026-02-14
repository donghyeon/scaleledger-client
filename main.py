# main.py
import asyncio
from enum import Enum, auto

import httpx
import structlog
from structlog.stdlib import get_logger
from tortoise import Tortoise

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


class ClientState(Enum):
    INITIALIZE = auto()
    SYNC = auto()
    REGISTER = auto()
    HEARTBEAT = auto()


class HeadlessClient:
    def __init__(self, server_url: str):
        self.state = ClientState.INITIALIZE
        self.api_client = APIClient(base_url=server_url)

        self.mac_address = get_mac_address()
        self.ip_address = get_ip_address()
        self.hostname = get_hostname()

        self.logger = get_logger()

        self.heartbeat_interval = 30
        self.polling_interval = 30
        self.retry_interval = 30

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

    async def initialize(self) -> ClientState:
        self.logger.debug("biz.auth.check_local")
        gateway = await Gateway.filter(mac_address=self.mac_address).first()

        if gateway and gateway.access_token:
            self.access_token = gateway.access_token
            self.logger.info("biz.auth.local_token_found", status=gateway.status)
            return ClientState.HEARTBEAT
        
        self.logger.info("biz.auth.no_token", next_state="SYNC")
        return ClientState.SYNC
    
    async def sync(self) -> ClientState:
        try:
            self.logger.debug("biz.sync.requesting")

            response = await self.api_client.retrieve_gateway(self.mac_address)
            response.pop("mac_address")
            gateway, created = await Gateway.update_or_create(mac_address=self.mac_address, defaults=response)
            
            if gateway.access_token:
                self.access_token = gateway.access_token
                self.logger.info("biz.sync.success", gateway_status=gateway.status)

                self.logger.info("hw.scan.start")
                peripherals = await asyncio.to_thread(scan_peripherals)
                self.logger.debug("hw.scan.found", count=len(peripherals))

                for peripheral in peripherals:
                    peripheral["gateway"] = gateway.id

                response = await self.api_client.sync_peripherals(self.access_token, peripherals)
                self.logger.info("biz.sync.peripherals_completed", count=len(response))
                return ClientState.HEARTBEAT
            
            self.logger.info("biz.sync.pending_approval", 
                             msg="Waiting for admin approval", 
                             polling_in=self.polling_interval)
            await asyncio.sleep(self.polling_interval)
            return ClientState.SYNC
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self.logger.warning("biz.sync.device_not_registered", next_state="REGISTER")
                return ClientState.REGISTER
            elif e.response.status_code == 400:
                self.logger.error("biz.sync.failed", status_code=e.response.status_code, errors=e.response.text)
            raise e

    async def register(self) -> ClientState:
        try:
            self.logger.info("biz.registration.attempting")
            await self.api_client.register_gateway(
                mac_address=self.mac_address,
                hostname=self.hostname,
                ip_address=self.ip_address,
                name=self.hostname,
            )
            self.logger.info("biz.registration.success", next_state="SYNC")
            return ClientState.SYNC
        
        except httpx.HTTPStatusError as e:
            self.logger.error("biz.registration.failed", status_code=e.response.status_code)
            raise e
    
    async def heartbeat(self) -> ClientState:
        try:
            self.logger.debug("net.heartbeat.sending")
            await self.api_client.send_heartbeat(self.access_token)
            self.logger.debug("net.heartbeat.ack")
            await asyncio.sleep(self.heartbeat_interval)
            return ClientState.HEARTBEAT
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                self.logger.error("net.heartbeat.rejected", 
                                  reason="Auth failed or device deleted", 
                                  status_code=e.response.status_code)
                self.access_token = None
                await Gateway.filter(mac_address=self.mac_address).delete()
                return ClientState.REGISTER
            raise e
    
    async def run(self):
        setup_logging()
        await self.setup()

        while True:
            structlog.contextvars.bind_contextvars(state=self.state.name)
            try:
                match self.state:
                    case ClientState.INITIALIZE:
                        self.state = await self.initialize()
                    case ClientState.SYNC:
                        self.state = await self.sync()
                    case ClientState.REGISTER:
                        self.state = await self.register()
                    case ClientState.HEARTBEAT:
                        self.state = await self.heartbeat()
            except asyncio.CancelledError:
                self.logger.info("sys.loop.cancelled")
                break
            except Exception:
                self.logger.exception("sys.loop.crashed", retry_in=self.retry_interval)
                await asyncio.sleep(self.retry_interval)


async def main():
    client = HeadlessClient(server_url="http://localhost:8000")
    try:
        await client.run()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
