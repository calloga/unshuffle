from enum import StrEnum


class RecordStatus(StrEnum):
    MOVED='moved'
    COPIED='copied'

class RecordStepStatus(StrEnum):
    COMMITTED='COMMITTED'