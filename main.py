# main.py
import asyncio

from tortoise import Tortoise
from tortoise.connection import connections

from api import APIClient
from models import Gateway
from utils import get_mac_address, get_ip_address, get_hostname


class HeadlessClient:
    def __init__(self, server_url: str):
        self.api_client = APIClient(base_url=server_url)
        self.access_token = None

        self.mac_address = get_mac_address()
        self.ip_address = get_ip_address()
        self.hostname = get_hostname()

        self.heartbeat_interval = 30
        self.polling_interval = 10

    async def initialize_db(self):
        await Tortoise.init(
            db_url="sqlite://db.sqlite3",
            modules={"models": ["models"]},
        )
        await Tortoise.generate_schemas()

    async def close(self):
        await self.api_client.close()
        await connections.close_all()

    async def load_access_token(self):
        gateway = await Gateway.filter(mac_address=self.mac_address).first()
        if gateway and gateway.access_token:
            self.access_token = gateway.access_token
    
    async def sync(self):
        response = await self.api_client.get_gateway_by_mac_address(self.mac_address)
        if response:
            response.pop("id")
            await Gateway.update_or_create(mac_address=self.mac_address, defaults=response)
            self.access_token = response.get("access_token")

    async def register(self):
        response = await self.api_client.register_gateway(
            mac_address=self.mac_address,
            hostname=self.hostname,
            ip_address=self.ip_address,
            name=self.hostname,
        )
        await Gateway.create(**response)
    
    async def trigger_retrieve_access_token_loop(self):
        while self.access_token is None:
            response = await self.api_client.get_gateway_by_mac_address(self.mac_address)
            if response["access_token"]:
                self.access_token = response["access_token"]
                response.pop("id")
                await Gateway.filter(mac_address=self.mac_address).update(**response)
            await asyncio.sleep(self.polling_interval)
    
    async def trigger_heartbeat_loop(self):
        while True:
            await self.api_client.send_heartbeat(self.access_token)
            await asyncio.sleep(self.heartbeat_interval)
    
    async def run(self):
        await self.initialize_db()

        await self.load_access_token()
        if self.access_token is None:
            try:
                await self.sync()
            except Exception:
                await self.register()
            if self.access_token is None:
                await self.trigger_retrieve_access_token_loop()
        await self.trigger_heartbeat_loop()


async def main():
    client = HeadlessClient(server_url="http://localhost:8000")
    try:
        await client.run()
    except Exception:
        pass
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
