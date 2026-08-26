from enum import StrEnum


class RecordStatus(StrEnum):
    MOVED='moved'
    COPIED='copied'

class RecordStepStatus(StrEnum):
    COMMITTED='COMMITTED'


class RefinementCandidateState(StrEnum):
    PENDING='pending'
    AUTO_STAGED='auto_staged'
    ACCEPTED='accepted'
    IGNORED='ignored'

class AnchorProfileState(StrEnum):
    CANDIDATE='candidate'
    VERIFIED='verified'
    SYSTEM='system'
    IGNORED='ignored'