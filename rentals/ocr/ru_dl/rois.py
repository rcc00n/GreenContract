from dataclasses import dataclass

CANVAS_WIDTH = 1400
CANVAS_HEIGHT = 900
CANVAS_SIZE = (CANVAS_WIDTH, CANVAS_HEIGHT)
ROI_VERSION = "ru_driver_license_v1"


@dataclass(frozen=True)
class Roi:
    name: str
    x: int
    y: int
    w: int
    h: int


FRONT_ROIS = {
    "surname": Roi("surname", 480, 150, 860, 60),
    "name": Roi("name", 480, 220, 860, 60),
    "patronymic": Roi("patronymic", 480, 290, 860, 60),
    "full_name_line": Roi("full_name_line", 430, 145, 920, 220),
    "birth_date": Roi("birth_date", 480, 360, 280, 50),
    "license_number": Roi("license_number", 480, 520, 420, 60),
    "license_issued_by": Roi("license_issued_by", 480, 585, 860, 90),
    "driving_since": Roi("driving_since", 480, 690, 280, 50),
}

BACK_ROIS = {
    "categories": Roi("categories", 330, 140, 980, 100),
    "special_marks": Roi("special_marks", 330, 280, 980, 220),
    "raw_text": Roi("raw_text", 80, 80, 1240, 740),
}
