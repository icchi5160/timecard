from enum import StrEnum


class Status(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNCONFIRMED = "UNCONFIRMED"
