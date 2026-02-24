# managers.py
from dataclasses import dataclass
import threading
from typing import Callable, Dict

from structlog.stdlib import get_logger

from events import BaseEvent
from models import WeighingStation
from suwol1000 import SerialClient, WeighingStationWorker


@dataclass
class StationRuntime:
    worker: WeighingStationWorker
    thread: threading.Thread
    port: str


class WeighingStationManager:
    def __init__(self, on_event: Callable[[BaseEvent], None]):
        self.on_event = on_event
        self.workers: Dict[int, StationRuntime] = {}
        self.logger = get_logger()

    def sync(self, stations: list[WeighingStation]):
        self.logger.info("sys.manager.station.sync_evalutaing", current_count=len(self.workers), target_count=len(stations))

        target_ids = {station.id for station in stations}
        current_ids = set(self.workers.keys())

        to_stop_ids = current_ids - target_ids
        for station_id in to_stop_ids:
            self.stop_worker(station_id)

        for station in stations:
            if station.id not in self.workers:
                self.start_worker(station)
            else:
                runtime = self.workers[station.id]
                if runtime.port != station.serial_port:
                    self.logger.info(
                        "sys.manager.station.config_changed", 
                        station_id=station.id, 
                        current_port=runtime.port,
                        target_port=station.serial_port,
                        action="restart_worker"
                    )
                    self.stop_worker(station.id)
                    self.start_worker(station)
        self.logger.info("sys.manager.station.sync_completed", running_workers=len(self.workers))

    def start_worker(self, station: WeighingStation):
        self.logger.info("sys.manager.station.start", station_id=station.id, port=station.serial_port)
        worker = WeighingStationWorker(
            serial_client=SerialClient(port=station.serial_port),
            on_event=self.on_event,
        )
        thread = threading.Thread(target=worker.run, name=f"WeighingStation-{station.id}-{station.serial_port}", daemon=True)
        thread.start()
        self.workers[station.id] = StationRuntime(worker=worker, thread=thread, port=station.serial_port)

    def stop_worker(self, station_id: int):
        runtime = self.workers.pop(station_id)
        self.logger.info("sys.manager.station.stop_worker", station_id=station_id, port=runtime.port)
        runtime.worker.stop()
        runtime.thread.join(timeout=3.0)

    def stop_all(self):
        self.logger.info("sys.manager.station.stop_all.requested")
        station_ids = list(self.workers.keys())
        for station_id in station_ids:
            self.stop_worker(station_id)
