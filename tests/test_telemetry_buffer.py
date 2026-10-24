import asyncio
import time
import pytest
from src.services.telemetry_buffer import TelemetryIngestionBuffer


@pytest.mark.asyncio
async def test_buffer_push_and_batch_flush():
    test_buffer = TelemetryIngestionBuffer(batch_size=5, flush_interval_sec=0.2)
    test_buffer.start()

    # Push 12 telemetry frames
    for i in range(12):
        ok = await test_buffer.push({
            "time": time.time(),
            "driver_id": f"buf-drv-{i}",
            "latitude": 37.7749 + (i * 0.001),
            "longitude": -122.4194,
            "h3_cell": "8828308281fffff",
            "speed_kmh": 25.0,
            "heading": 90.0,
        })
        assert ok is True

    # Allow background flush loop to trigger
    await asyncio.sleep(0.6)
    await test_buffer.stop()

    metrics = test_buffer.get_metrics()
    assert metrics["total_ingested"] == 12
    assert metrics["total_flushed"] == 12
    assert metrics["total_batches"] >= 2
