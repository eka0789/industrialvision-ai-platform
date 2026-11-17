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
    GENICAM = "genicam"
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
    # RBAC v2 (A6): owner/admin manage the organization; viewer is read-only.
    OWNER = "owner"
    ADMIN = "admin"
    AI_ENGINEER = "ai_engineer"
    OPERATOR = "operator"
    VIEWER = "viewer"

    @classmethod
    def all(cls) -> list[str]:
        return [cls.OWNER, cls.ADMIN, cls.OPERATOR, cls.AI_ENGINEER, cls.VIEWER]

    @classmethod
    def write_roles(cls) -> list[str]:
        """Roles allowed to create/modify operational content."""
        return [cls.OWNER, cls.ADMIN, cls.OPERATOR, cls.AI_ENGINEER]

    @classmethod
    def admin_roles(cls) -> list[str]:
        """Roles with organization-level administration rights."""
        return [cls.OWNER, cls.ADMIN]
