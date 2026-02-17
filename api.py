# api.py
from typing import Dict, Any

import httpx


class APIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def close(self):
        await self.client.aclose()

    async def retrieve_gateway(self, mac_address: str) -> Dict[str, Any]:
        response = await self.client.get(f"devices/api/gateways/{mac_address}/")
        response.raise_for_status()
        return response.json()

    async def send_heartbeat(self, access_token: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await self.client.post("devices/api/gateways/heartbeat/", headers=headers)
        response.raise_for_status()
        return response.json()
