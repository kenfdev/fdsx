import secrets
from datetime import datetime


def generate_thread_id() -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    suffix = secrets.token_hex(3)
    return f"{timestamp}-{suffix}"
