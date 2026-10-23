"""Core constants used across the backend."""


class Decision(str):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    ERROR = "ERROR"

    @classmethod
    def values(cls) -> list[str]:
        return [cls.PASS, cls.FAIL, cls.REVIEW, cls.ERROR]


class CameraSourceType(str):
    UPLOAD = "upload"
    LOCAL_FILE = "local_file"
    WEBCAM = "webcam"
    RTSP = "rtsp"
    SIMULATED = "simulated"


class CameraStatus(str):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    DEGRADED = "degraded"


class ModelDevice(str):
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"

    @classmethod
    def all(cls) -> list[str]:
        return [cls.CPU, cls.CUDA, cls.MPS]


class Role(str):
    ADMIN = "admin"
    OPERATOR = "operator"
    AI_ENGINEER = "ai_engineer"

    @classmethod
    def all(cls) -> list[str]:
        return [cls.ADMIN, cls.OPERATOR, cls.AI_ENGINEER]
