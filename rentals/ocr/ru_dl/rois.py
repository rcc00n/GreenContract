from dataclasses import dataclass

CANVAS_WIDTH = 1400
CANVAS_HEIGHT = 900
CANVAS_SIZE = (CANVAS_WIDTH, CANVAS_HEIGHT)
ROI_VERSION = "ru_driver_license_v3"


@dataclass(frozen=True)
class Roi:
    name: str
    x: int
    y: int
    w: int
    h: int


FRONT_ROI_TEMPLATES = {
    "v2": {
        "surname": Roi("surname", 520, 240, 780, 60),
        "name": Roi("name", 520, 300, 780, 60),
        "patronymic": Roi("patronymic", 520, 350, 780, 55),
        "full_name_line": Roi("full_name_line", 520, 240, 780, 170),
        "birth_date": Roi("birth_date", 520, 400, 300, 55),
        "license_number": Roi("license_number", 520, 650, 420, 60),
        "license_issued_by": Roi("license_issued_by", 520, 560, 520, 70),
        "driving_since": Roi("driving_since", 520, 520, 300, 50),
    },
    "v1": {
        "surname": Roi("surname", 520, 230, 780, 55),
        "name": Roi("name", 520, 290, 780, 60),
        "patronymic": Roi("patronymic", 520, 350, 780, 45),
        "full_name_line": Roi("full_name_line", 520, 230, 780, 165),
        "birth_date": Roi("birth_date", 520, 400, 260, 50),
        "license_number": Roi("license_number", 520, 510, 420, 60),
        "license_issued_by": Roi("license_issued_by", 520, 560, 500, 70),
        "driving_since": Roi("driving_since", 520, 455, 260, 45),
    },
}

DEFAULT_FRONT_TEMPLATE = "v2"

FRONT_ANCHORS = {
    "v2": {
        "1": (490, 265),
        "2": (490, 325),
        "3": (490, 410),
        "4A": (490, 525),
        "4B": (900, 525),
        "5": (490, 670),
    },
    "v1": {
        "1": (490, 255),
        "2": (490, 315),
        "3": (490, 400),
        "4A": (490, 465),
        "4B": (900, 465),
        "5": (490, 525),
    },
}

BACK_ROIS = {
    "categories": Roi("categories", 330, 140, 980, 100),
    "special_marks": Roi("special_marks", 330, 280, 980, 220),
    "raw_text": Roi("raw_text", 80, 80, 1240, 740),
}
