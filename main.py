# main.py
import asyncio
from enum import Enum, auto

import httpx
from tortoise import Tortoise, connections

from api import APIClient
from models import Gateway
from utils import get_mac_address, get_ip_address, get_hostname


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

        self.heartbeat_interval = 30
        self.polling_interval = 30
        self.retry_interval = 30

        self.access_token: str | None = None

    async def close(self):
        await self.api_client.close()
        await connections.close_all()

    async def setup(self):
        await Tortoise.init(
            db_url="sqlite://db.sqlite3",
            modules={"models": ["models"]},
        )
        await Tortoise.generate_schemas()

    async def initialize(self) -> ClientState:
        gateway = await Gateway.filter(mac_address=self.mac_address).first()
        if gateway and gateway.access_token:
            self.access_token = gateway.access_token
            return ClientState.HEARTBEAT
        
        return ClientState.SYNC
    
    async def sync(self) -> ClientState:
        try:
            response = await self.api_client.retrieve_gateway(self.mac_address)
            response.pop("mac_address")
            gateway, created = await Gateway.update_or_create(mac_address=self.mac_address, defaults=response)
            
            if gateway.access_token:
                self.access_token = gateway.access_token
                return ClientState.HEARTBEAT
            
            await asyncio.sleep(self.polling_interval)
            return ClientState.SYNC
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ClientState.REGISTER
            raise e

    async def register(self) -> ClientState:
        try:
            await self.api_client.register_gateway(
                mac_address=self.mac_address,
                hostname=self.hostname,
                ip_address=self.ip_address,
                name=self.hostname,
            )
            return ClientState.SYNC
        
        except httpx.HTTPStatusError as e:
            raise e
    
    async def heartbeat(self) -> ClientState:
        try:
            await self.api_client.send_heartbeat(self.access_token)
            await asyncio.sleep(self.heartbeat_interval)
            return ClientState.HEARTBEAT
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                self.access_token = None
                await Gateway.filter(mac_address=self.mac_address).delete()
                return ClientState.REGISTER
            raise e
    
    async def run(self):
        await self.setup()

        while True:
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
                break
            except Exception:
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
