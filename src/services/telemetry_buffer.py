import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any
from sqlalchemy import insert
from src.core.config import settings
from src.db.models import DriverTelemetry
from src.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class TelemetryIngestionBuffer:
    """High-throughput asynchronous telemetry buffer.

    Protects relational/TimescaleDB databases from write amplification by batching
    high-frequency GPS telemetry frames arriving via WebSockets.
    """

    def __init__(
        self,
        batch_size: int | None = None,
        flush_interval_sec: float | None = None,
        max_queue_size: int = 20000,
    ) -> None:
        self.batch_size = batch_size or settings.TELEMETRY_BUFFER_BATCH_SIZE
        self.flush_interval_sec = flush_interval_sec or settings.TELEMETRY_BUFFER_FLUSH_INTERVAL_SEC
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._is_running = False

        # Metrics for monitoring & resume display
        self.total_ingested = 0
        self.total_flushed = 0
        self.total_batches = 0
        self.last_flush_time = time.time()

    def start(self) -> None:
        """Start the background consumer loop."""
        if not self._is_running:
            self._is_running = True
            self._worker_task = asyncio.create_task(self._flush_loop())
            logger.info("Telemetry ingestion batch flusher started.")

    async def stop(self) -> None:
        """Gracefully stop worker and flush any pending queue items."""
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        # Final flush of residual items
        await self._flush_current_batch(force=True)
        logger.info(f"Telemetry buffer stopped. Ingested: {self.total_ingested}, Flushed: {self.total_flushed}")

    async def push(self, telemetry_item: dict[str, Any]) -> bool:
        """Non-blocking push of telemetry record to in-memory ingestion buffer."""
        try:
            self.queue.put_nowait(telemetry_item)
            self.total_ingested += 1
            return True
        except asyncio.QueueFull:
            logger.warning("Telemetry buffer queue is full. Dropping frame to apply backpressure.")
            return False

    async def _flush_loop(self) -> None:
        """Background loop reading from queue and flushing in micro-batches."""
        batch: list[dict[str, Any]] = []

        while self._is_running:
            try:
                # Wait for next item or timeout to trigger interval-based flush
                timeout = max(0.05, self.flush_interval_sec - (time.time() - self.last_flush_time))
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                    batch.append(item)
                    self.queue.task_done()
                except asyncio.TimeoutError:
                    pass

                # Check flush conditions: batch capacity reached or interval elapsed
                interval_elapsed = (time.time() - self.last_flush_time) >= self.flush_interval_sec
                if len(batch) >= self.batch_size or (interval_elapsed and batch):
                    await self._persist_batch(batch)
                    batch = []
                    self.last_flush_time = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in telemetry flush loop: {e}", exc_info=True)
                await asyncio.sleep(0.5)

        # Flush any remaining items before exiting
        if batch:
            await self._persist_batch(batch)

    async def _persist_batch(self, batch: list[dict[str, Any]]) -> None:
        """Execute single bulk INSERT for entire batch."""
        if not batch:
            return

        db_records: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for item in batch:
            dt = item.get("time") or now
            if isinstance(dt, (int, float)):
                dt = datetime.fromtimestamp(dt, tz=timezone.utc)

            db_records.append({
                "time": dt,
                "driver_id": str(item["driver_id"]),
                "latitude": float(item["latitude"]),
                "longitude": float(item["longitude"]),
                "h3_cell": str(item["h3_cell"]),
                "speed_kmh": float(item.get("speed_kmh", 0.0)),
                "heading": float(item.get("heading", 0.0)),
                "trip_id": item.get("trip_id"),
            })

        start_time = time.perf_counter()
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(insert(DriverTelemetry).values(db_records))
                await session.commit()

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            self.total_flushed += len(batch)
            self.total_batches += 1
            logger.debug(f"Flushed {len(batch)} telemetry points in {duration_ms:.2f}ms.")
        except Exception as e:
            logger.error(f"Failed to persist telemetry batch of {len(batch)} items: {e}")

    async def _flush_current_batch(self, force: bool = False) -> None:
        """Drains remaining queue items and persists them immediately."""
        residual: list[dict[str, Any]] = []
        while not self.queue.empty():
            try:
                item = self.queue.get_nowait()
                residual.append(item)
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break

        if residual:
            await self._persist_batch(residual)

    def get_metrics(self) -> dict[str, Any]:
        """Return runtime buffer metrics."""
        return {
            "queue_depth": self.queue.qsize(),
            "total_ingested": self.total_ingested,
            "total_flushed": self.total_flushed,
            "total_batches": self.total_batches,
            "buffer_capacity": self.queue.maxsize,
        }


# Singleton instance
telemetry_buffer = TelemetryIngestionBuffer()
