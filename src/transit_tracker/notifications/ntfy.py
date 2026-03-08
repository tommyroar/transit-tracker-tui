import httpx
from ..config import TransitConfig

async def send_ntfy(config: TransitConfig, title: str, message: str):
    """
    Sends a push notification via ntfy.sh.
    Currently separated out from the main logic until the notification system is fully re-implemented.
    """
    topic = config.ntfy_topic
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://ntfy.sh/{topic}",
            content=message,
            headers={
                "Title": title,
                "Priority": "4",
                "Tags": "bus,warning"
            }
        )
