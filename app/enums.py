from enum import StrEnum


class AlertLevel(StrEnum):
    VERT = "vert"
    ORANGE = "orange"
    ROUGE = "rouge"

    @property
    def rank(self) -> int:
        return {"vert": 0, "orange": 1, "rouge": 2}[self.value]

    @property
    def color(self) -> str:
        return {"vert": "#2ECC71", "orange": "#F39C12", "rouge": "#E74C3C"}[self.value]


class ObstructionClass(StrEnum):
    LIBRE = "libre"
    PARTIELLE = "partielle"
    FORTE = "forte"
    CRITIQUE = "critique"

    @property
    def rank(self) -> int:
        return {"libre": 0, "partielle": 1, "forte": 2, "critique": 3}[self.value]


class AlertStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class Connectivity(StrEnum):
    ONLINE = "en_ligne"
    OFFLINE = "hors_ligne"
    UNKNOWN = "inconnu"


class Channel(StrEnum):
    SMS = "sms"
    PUSH = "push"
    EMAIL = "email"


class NotificationStatus(StrEnum):
    SENT = "sent"
    SIMULATED = "simulated"
    FAILED = "failed"


class ReportCategory(StrEnum):
    OVERFLOW = "debordement"
    BLOCKED_CANAL = "canal_obstrue"
    FLOODED_ROAD = "route_inondee"
    OTHER = "autre"


class ReportStatus(StrEnum):
    NEW = "new"
    VERIFIED = "verified"
    REJECTED = "rejected"
