# api.py
from typing import Dict, Any

import httpx


class APIClient:
    def __init__(self, base_url: str):
        self.client = httpx.AsyncClient(base_url=base_url)

    async def close(self):
        await self.client.aclose()

    async def register_gateway(
        self, 
        mac_address: str, 
        hostname: str, 
        ip_address: str, 
        name: str
    ) -> Dict[str, Any]:
        payload = {
            "mac_address": mac_address,
            "hostname": hostname,
            "ip_address": ip_address,
            "name": name,
        }
        response = await self.client.post("devices/api/gateways/", json=payload)
        response.raise_for_status()
        return response.json()

    async def get_gateway_by_mac_address(self, mac_address: str) -> Dict[str, Any]:
        response = await self.client.get(f"devices/api/gateways/{mac_address}/")
        response.raise_for_status()
        return response.json()

    async def send_heartbeat(self, access_token: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await self.client.post("devices/api/gateways/heartbeat/", headers=headers)
        response.raise_for_status()
        return response.json()
