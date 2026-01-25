from dataclasses import dataclass

CANVAS_WIDTH = 1400
CANVAS_HEIGHT = 900
CANVAS_SIZE = (CANVAS_WIDTH, CANVAS_HEIGHT)
ROI_VERSION = "ru_driver_license_v2"


@dataclass(frozen=True)
class Roi:
    name: str
    x: int
    y: int
    w: int
    h: int


FRONT_ROIS = {
    "surname": Roi("surname", 520, 240, 780, 60),
    "name": Roi("name", 520, 300, 780, 60),
    "patronymic": Roi("patronymic", 520, 350, 780, 55),
    "full_name_line": Roi("full_name_line", 520, 240, 780, 170),
    "birth_date": Roi("birth_date", 520, 400, 300, 55),
    "license_number": Roi("license_number", 520, 650, 420, 60),
    "license_issued_by": Roi("license_issued_by", 520, 560, 520, 70),
    "driving_since": Roi("driving_since", 520, 520, 300, 50),
}

BACK_ROIS = {
    "categories": Roi("categories", 330, 140, 980, 100),
    "special_marks": Roi("special_marks", 330, 280, 980, 220),
    "raw_text": Roi("raw_text", 80, 80, 1240, 740),
}
