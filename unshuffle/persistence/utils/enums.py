from enum import StrEnum


class StoreName(StrEnum):
    CACHE = 'cache'
    COHERENCE = 'coherence'
    LEARNING = 'learning'
    LIFECYCLE = 'lifecycle'
    MAINTENANCE = 'maintenance'
    SESSIONS = 'sessions'
    TAXONOMY = 'taxonomy'

class DatabaseDriver(StrEnum):
    PEEWEE = 'peewee'
    SQLITE = 'sqlite'