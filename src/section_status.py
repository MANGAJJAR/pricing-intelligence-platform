from enum import Enum

class SectionProcessingStatus(str, Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    PROCESSING_INDICATION = "processing_indication"
    PROCESSING_DOSAGE_AND_ADVERSE_REACTIONS = "processing_dosage_and_adverse_reactions"
    COMPLETED_SECTION = "completed_section"
    FAILED_SECTION = "failed_section"

    @classmethod
    def _missing_(cls, value):
        for member in cls:
            if member.value.lower() == str(value).lower() or member.name.lower() == str(value).lower():
                return member
        return cls.NOT_STARTED

