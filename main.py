# main.py
import asyncio
from enum import Enum, auto

import httpx
import structlog
from tortoise import Tortoise, connections

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

        self.logger = structlog.get_logger()

        self.heartbeat_interval = 30
        self.polling_interval = 30
        self.retry_interval = 30

        self.access_token: str | None = None

    async def close(self):
        await self.api_client.close()
        await connections.close_all()
        self.logger.info("client_shutdown")

    async def setup(self):
        self.logger.info("client_startup", server_url=self.api_client.client.base_url)
        await Tortoise.init(
            db_url="sqlite://db.sqlite3",
            modules={"models": ["models"]},
        )
        await Tortoise.generate_schemas()

    async def initialize(self) -> ClientState:
        self.logger.debug("checking_local_db")
        gateway = await Gateway.filter(mac_address=self.mac_address).first()

        if gateway and gateway.access_token:
            self.access_token = gateway.access_token
            self.logger.info("local_token_found", status=gateway.status)
            return ClientState.HEARTBEAT
        
        self.logger.info("no_local_token", next_state="SYNC")
        return ClientState.SYNC
    
    async def sync(self) -> ClientState:
        try:
            self.logger.debug("syncing_with_server")

            response = await self.api_client.retrieve_gateway(self.mac_address)
            response.pop("mac_address")
            gateway, created = await Gateway.update_or_create(mac_address=self.mac_address, defaults=response)
            
            if gateway.access_token:
                self.access_token = gateway.access_token
                self.logger.info("sync_access_token_received", gateway_status=gateway.status)

                self.logger.info("scanning_peripherals")
                peripherals = scan_peripherals()
                self.logger.debug("peripherals_found", count=len(peripherals))

                for peripheral in peripherals:
                    peripheral["gateway"] = gateway.id

                response = await self.api_client.sync_peripherals(self.access_token, peripherals)
                self.logger.info("peripheral_sync_complete", count=len(response))
                return ClientState.HEARTBEAT
            
            self.logger.info("sync_pending_approval", 
                             msg="Waiting for admin approval", 
                             polling_in=self.polling_interval)
            await asyncio.sleep(self.polling_interval)
            return ClientState.SYNC
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self.logger.warning("device_not_registered_on_server", next_state="REGISTER")
                return ClientState.REGISTER
            elif e.response.status_code == 400:
                self.logger.error("peripheral_sync_failed", status_code=e.response.status_code, errors=e.response.text)
            raise e

    async def register(self) -> ClientState:
        try:
            self.logger.info("attempting_registration")
            await self.api_client.register_gateway(
                mac_address=self.mac_address,
                hostname=self.hostname,
                ip_address=self.ip_address,
                name=self.hostname,
            )
            self.logger.info("registration_successful", next_state="SYNC")
            return ClientState.SYNC
        
        except httpx.HTTPStatusError as e:
            self.logger.error("registration_failed", status_code=e.response.status_code)
            raise e
    
    async def heartbeat(self) -> ClientState:
        try:
            self.logger.debug("sending_heartbeat")
            await self.api_client.send_heartbeat(self.access_token)
            self.logger.debug("heartbeat_ack")
            await asyncio.sleep(self.heartbeat_interval)
            return ClientState.HEARTBEAT
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                self.logger.error("heartbeat_rejected", 
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
                self.logger.info("loop_cancelled")
                break
            except Exception:
                self.logger.exception("unexpected_error_in_loop", retry_in=self.retry_interval)
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
