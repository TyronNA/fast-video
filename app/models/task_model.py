from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class VideoTask:
    """
    Lightweight task record.
    Not persisted — included for future extension (e.g. queue / DB integration).
    """

    task_id: str
    prompt: str
    duration: int
    status: TaskStatus = TaskStatus.PENDING
    file_path: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
