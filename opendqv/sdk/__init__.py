"""
OpenDQV Python SDK — client library for source systems.

Synchronous usage:
    from opendqv.sdk import OpenDQVClient

    client = OpenDQVClient("http://localhost:8000", token="your-pat-token")
    result = client.validate({"email": "alice@example.com", "age": 25}, contract="customer")
    if result["valid"]:
        # proceed with write
    else:
        # handle errors

Async usage (FastAPI, Kafka consumers):
    from opendqv.sdk import AsyncOpenDQVClient

    async with AsyncOpenDQVClient("http://localhost:8000", token="your-pat-token") as client:
        result = await client.validate(record, contract="customer")
"""

from .client import AsyncOpenDQVClient, OpenDQVClient, ValidationError

__all__ = ["OpenDQVClient", "AsyncOpenDQVClient", "ValidationError"]
