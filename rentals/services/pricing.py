from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict

from ..models import Car

# Таблица для ночного выхода — можно скорректировать при необходимости.
NIGHT_EXIT_FEE_DEFAULT = Decimal("0")
NIGHT_EXIT_FEE_SLOTS = [
    ("20:00", "20:59", Decimal("1000")),
    ("21:00", "23:59", Decimal("1700")),
    ("00:00", "05:59", Decimal("2200")),
    ("06:00", "07:59", Decimal("1700")),
    ("08:00", "08:59", Decimal("1000")),
]

BASE_DELIVERY_FEES: Dict[str, Decimal] = {
    "Алупка": Decimal("4500"),
    "Алушта": Decimal("2500"),
    "за Алушту": Decimal("3000"),
    "Змейка, Новотерский": Decimal("1300"),
    "Армянск": Decimal("4500"),
    "Ай-Даниль": Decimal("3500"),
    "Балаклава": Decimal("3800"),
    "Бахчисарай": Decimal("2500"),
    "Береговое (Николаевка)": Decimal("2500"),
    "Береговое Фео": Decimal("3500"),
    "Витино": Decimal("3500"),
    "Гаспра": Decimal("4000"),
    "Гурзуф": Decimal("4000"),
    "Джанкой": Decimal("3000"),
    "Евпатория": Decimal("3000"),
    "Заозерное": Decimal("3500"),
    "Канака ( с Приветное)": Decimal("4500"),
    "Кацивели": Decimal("4000"),
    "Кача": Decimal("4000"),
    "Керчь": Decimal("5500"),
    "Краснодар": Decimal("7000"),
    "Крымская роза": Decimal("2000"),
    "Коктебель": Decimal("4200"),
    "Кореиз": Decimal("4200"),
    "Курпаты": Decimal("4200"),
    "Курортное": Decimal("4000"),
    "Ласпи": Decimal("4200"),
    "Лучистое": Decimal("3900"),
    "Малореченское": Decimal("3500"),
    "Массандра": Decimal("3000"),
    "Межводное": Decimal("4900"),
    "Минеральные Воды-0": Decimal("0"),
    "Минеральные Воды-1000": Decimal("1000"),
    "Мирное": Decimal("3700"),
    "Морское": Decimal("4000"),
    "МРИЯ отель": Decimal("4200"),
    "Николаевка": Decimal("2500"),
    "Новофедоровка": Decimal("2800"),
    "Новый Свет": Decimal("4200"),
    "Оленевка": Decimal("4900"),
    "Оползневое": Decimal("4200"),
    "Орджоникидзе": Decimal("4200"),
    "Орловка": Decimal("4000"),
    "Отрадное": Decimal("2900"),
    "Партенит": Decimal("3000"),
    "Песчаное": Decimal("3000"),
    "Поповка": Decimal("3800"),
    "Приветное": Decimal("4000"),
    "Приморский": Decimal("4200"),
    "Саки": Decimal("2400"),
    "Севастополь": Decimal("3500"),
    "Семидворье": Decimal("3500"),
    "Симеиз": Decimal("4200"),
    "Симферополь-0": Decimal("0"),
    "Симферополь-1000": Decimal("1000"),
    "Симферопольский р-ны Строгоновка, Денисовка, Мазанка": Decimal("1500"),
    "Солнечногорское (Рыбачье)": Decimal("3500"),
    "Сочи": Decimal("10000"),
    "Судак": Decimal("3900"),
    "Тарханкут (оленевка)": Decimal("4900"),
    "Утес": Decimal("3300"),
    "Уютное": Decimal("3000"),
    "Угловое": Decimal("4000"),
    "Феодосия": Decimal("3900"),
    "фиолент": Decimal("3500"),
    "Форос": Decimal("4200"),
    "Фрунзе": Decimal("3000"),
    "Черноморское": Decimal("4000"),
    "Штормовое": Decimal("3800"),
    "Щелкино": Decimal("5500"),
    "Ялта": Decimal("3500"),
    "за Ялту": Decimal("4000"),
    "Кисловодск": Decimal("3500"),
    "Ессентуки": Decimal("2500"),
    "Пятигорск": Decimal("2500"),
    "Железноводск": Decimal("2000"),
}

CHILD_SEAT_DAILY = Decimal("200")
CHILD_SEAT_CAP = Decimal("2800")
BOOSTER_DAILY = Decimal("100")
BOOSTER_CAP = Decimal("1000")
SKI_RACK_DAILY = Decimal("400")
AUTOBOX_DAILY = Decimal("900")
CROSSBARS_DAILY = Decimal("300")
MONEY_QUANT = Decimal("1")


@dataclass
class PricingBreakdown:
    days: int
    daily_rate: Decimal
    base_total: Decimal
    car_wash_total: Decimal
    night_total: Decimal
    delivery_total: Decimal
    seats_total: Decimal
    boosters_total: Decimal
    gear_total: Decimal
    equipment_manual_total: Decimal
    extras_total: Decimal
    discount_amount: Decimal
    discount_percent_amount: Decimal
    subtotal: Decimal
    total_price: Decimal
    prepayment: Decimal
    balance_due: Decimal


def rental_days(start_date: date | None, end_date: date | None) -> int:
    """Return rental length in days (non-negative)."""
    if not start_date or not end_date:
        return 0
    return max((end_date - start_date).days, 0)


def _to_decimal(value, default="0.00") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _round_money(value) -> Decimal:
    try:
        return _to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal("0")


def _parse_time_string(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _parse_decimal_str(value: str) -> Decimal:
    text = str(value).strip().replace(",", ".")
    if not text:
        raise ValueError("Пустое значение суммы.")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Некорректное значение суммы.")


def parse_delivery_overrides(text: str) -> dict[str, Decimal]:
    overrides: dict[str, Decimal] = {}
    for idx, raw_line in enumerate((text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Строка {idx}: ожидалось 'Город=сумма'.")
        city_raw, amount_raw = line.split("=", 1)
        city = city_raw.strip()
        if not city:
            raise ValueError(f"Строка {idx}: пустое название города.")
        amount = _parse_decimal_str(amount_raw)
        overrides[city] = amount
    return overrides


def parse_night_slots(text: str) -> list[tuple[str, str, Decimal]]:
    slots: list[tuple[str, str, Decimal]] = []
    for idx, raw_line in enumerate((text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line or "-" not in line:
            raise ValueError(f"Строка {idx}: ожидалось 'HH:MM-HH:MM=сумма'.")
        range_part, amount_part = line.split("=", 1)
        start_raw, end_raw = [part.strip() for part in range_part.split("-", 1)]
        try:
            _parse_time_string(start_raw)
            _parse_time_string(end_raw)
        except ValueError:
            raise ValueError(f"Строка {idx}: неверный формат времени.")
        amount = _parse_decimal_str(amount_part)
        slots.append((start_raw, end_raw, amount))
    return slots


def _get_business_settings():
    from ..models import BusinessSettings

    return BusinessSettings.get_solo()


def get_delivery_fees(settings=None) -> Dict[str, Decimal]:
    if settings is None:
        settings = _get_business_settings()
    fees = dict(BASE_DELIVERY_FEES)
    if settings and settings.delivery_fees_text:
        fees.update(parse_delivery_overrides(settings.delivery_fees_text))
    return fees


def get_night_slots(settings=None) -> list[tuple[str, str, Decimal]]:
    if settings is None:
        settings = _get_business_settings()
    if settings and settings.night_fee_slots_text:
        return parse_night_slots(settings.night_fee_slots_text)
    return NIGHT_EXIT_FEE_SLOTS


def season_for_date(date_value: date | None, settings=None) -> str:
    if not date_value or not settings:
        return "high"
    start = settings.high_season_start
    end = settings.high_season_end
    if not start or not end:
        return "high"
    start_md = (start.month, start.day)
    end_md = (end.month, end.day)
    target_md = (date_value.month, date_value.day)
    if start_md <= end_md:
        return "high" if start_md <= target_md <= end_md else "low"
    # High season overlaps year end (e.g. Nov->Mar)
    return "high" if (target_md >= start_md or target_md <= end_md) else "low"

def _time_in_range(start: time, end: time, current: time) -> bool:
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _fee_for_time(value: time | None, slots: list[tuple[str, str, Decimal]], default: Decimal) -> Decimal:
    if value is None:
        return default
    for start_raw, end_raw, amount in slots:
        start_time = _parse_time_string(start_raw)
        end_time = _parse_time_string(end_raw)
        if _time_in_range(start_time, end_time, value):
            return amount
    return default


def delivery_fee_for_city(city: str | None, fees: Dict[str, Decimal] | None = None) -> Decimal:
    if not city:
        return Decimal("0.00")
    fee_map = fees or get_delivery_fees(_get_business_settings())
    return fee_map.get(city, Decimal("0.00"))


def pricing_config() -> dict:
    """Expose pricing constants for the UI."""

    def _slot_list(slots):
        return [{"start": start, "end": end, "amount": float(amount)} for start, end, amount in slots]

    settings = _get_business_settings()
    night_slots = get_night_slots(settings)
    delivery_fees = get_delivery_fees(settings)
    season_start = settings.high_season_start
    season_end = settings.high_season_end

    night_default = (
        settings.night_fee_default if settings.night_fee_default is not None else NIGHT_EXIT_FEE_DEFAULT
    )
    child_seat_daily = settings.child_seat_daily if settings.child_seat_daily is not None else CHILD_SEAT_DAILY
    child_seat_cap = settings.child_seat_cap if settings.child_seat_cap is not None else CHILD_SEAT_CAP
    booster_daily = settings.booster_daily if settings.booster_daily is not None else BOOSTER_DAILY
    booster_cap = settings.booster_cap if settings.booster_cap is not None else BOOSTER_CAP
    ski_rack_daily = settings.ski_rack_daily if settings.ski_rack_daily is not None else SKI_RACK_DAILY
    autobox_daily = settings.autobox_daily if settings.autobox_daily is not None else AUTOBOX_DAILY
    crossbars_daily = settings.crossbars_daily if settings.crossbars_daily is not None else CROSSBARS_DAILY
    car_wash_default = (
        settings.car_wash_default if settings.car_wash_default is not None else Decimal("1000.00")
    )

    return {
        "night_default": float(night_default),
        "night_slots": _slot_list(night_slots),
        "delivery_fees": {name: float(amount) for name, amount in delivery_fees.items()},
        "child_seat_daily": float(child_seat_daily),
        "child_seat_cap": float(child_seat_cap),
        "booster_daily": float(booster_daily),
        "booster_cap": float(booster_cap),
        "ski_rack_daily": float(ski_rack_daily),
        "autobox_daily": float(autobox_daily),
        "crossbars_daily": float(crossbars_daily),
        "car_wash_default": float(car_wash_default),
        "season_start": {
            "month": season_start.month,
            "day": season_start.day,
        }
        if season_start
        else None,
        "season_end": {
            "month": season_end.month,
            "day": season_end.day,
        }
        if season_end
        else None,
    }


def calculate_rental_pricing(
    car: Car | None,
    start_date: date | None,
    end_date: date | None,
    *,
    start_time: time | None = None,
    end_time: time | None = None,
    unique_daily_rate: Decimal | None = None,
    car_wash_fee: Decimal | None = None,
    night_fee_start: Decimal | None = None,
    night_fee_end: Decimal | None = None,
    delivery_issue_city: str = "",
    delivery_return_city: str = "",
    delivery_issue_fee: Decimal | None = None,
    delivery_return_fee: Decimal | None = None,
    child_seat_count: int = 0,
    booster_count: int = 0,
    ski_rack_count: int = 0,
    roof_box_count: int = 0,
    crossbars_count: int = 0,
    child_seat_included: bool = False,
    booster_included: bool = False,
    ski_rack_included: bool = False,
    roof_box_included: bool = False,
    crossbars_included: bool = False,
    equipment_manual_total: Decimal | None = None,
    discount_amount: Decimal | None = None,
    discount_percent: Decimal | None = None,
    prepayment: Decimal | None = None,
) -> PricingBreakdown:
    """
    Calculate full rental pricing with surcharges, extras, discounts and prepayment.
    """
    days = rental_days(start_date, end_date)
    if not car or days <= 0:
        zero = Decimal("0")
        return PricingBreakdown(
            days=days,
            daily_rate=zero,
            base_total=zero,
            car_wash_total=zero,
            night_total=zero,
            delivery_total=zero,
            seats_total=zero,
            boosters_total=zero,
            gear_total=zero,
            equipment_manual_total=zero,
            extras_total=zero,
            discount_amount=zero,
            discount_percent_amount=zero,
            subtotal=zero,
            total_price=zero,
            prepayment=_round_money(prepayment),
            balance_due=zero,
        )

    settings = _get_business_settings()
    season = season_for_date(start_date, settings)
    night_slots = get_night_slots(settings)
    night_default = settings.night_fee_default if settings.night_fee_default is not None else NIGHT_EXIT_FEE_DEFAULT
    delivery_fees = get_delivery_fees(settings)

    base_daily_rate = (
        _round_money(unique_daily_rate)
        if unique_daily_rate not in (None, "")
        else _round_money(car.get_rate_for_days(days, season=season))
    )
    base_total = _round_money(base_daily_rate * Decimal(days))

    car_wash_value = car_wash_fee if car_wash_fee not in (None, "") else settings.car_wash_default
    car_wash_total = _round_money(car_wash_value)
    if car_wash_total < 0:
        car_wash_total = Decimal("0")

    night_start = (
        _round_money(night_fee_start)
        if night_fee_start not in (None, "")
        else _round_money(_fee_for_time(start_time, night_slots, night_default))
    )
    night_end = (
        _round_money(night_fee_end)
        if night_fee_end not in (None, "")
        else _round_money(_fee_for_time(end_time, night_slots, night_default))
    )
    night_total = _round_money(night_start + night_end)

    issue_delivery_fee = (
        _round_money(delivery_issue_fee)
        if delivery_issue_fee not in (None, "")
        else _round_money(delivery_fee_for_city(delivery_issue_city, delivery_fees))
    )
    return_delivery_fee = (
        _round_money(delivery_return_fee)
        if delivery_return_fee not in (None, "")
        else _round_money(delivery_fee_for_city(delivery_return_city, delivery_fees))
    )
    delivery_total = _round_money(issue_delivery_fee + return_delivery_fee)

    child_daily = settings.child_seat_daily if settings.child_seat_daily is not None else CHILD_SEAT_DAILY
    child_cap = settings.child_seat_cap if settings.child_seat_cap is not None else CHILD_SEAT_CAP
    booster_daily = settings.booster_daily if settings.booster_daily is not None else BOOSTER_DAILY
    booster_cap = settings.booster_cap if settings.booster_cap is not None else BOOSTER_CAP
    ski_daily = settings.ski_rack_daily if settings.ski_rack_daily is not None else SKI_RACK_DAILY
    autobox_daily = settings.autobox_daily if settings.autobox_daily is not None else AUTOBOX_DAILY
    crossbars_daily = settings.crossbars_daily if settings.crossbars_daily is not None else CROSSBARS_DAILY

    seat_units = max(int(child_seat_count or 0), 1 if child_seat_included else 0)
    booster_units = max(int(booster_count or 0), 1 if booster_included else 0)
    seats_total = _round_money(min(child_daily * days, child_cap) * seat_units)
    boosters_total = _round_money(min(booster_daily * days, booster_cap) * booster_units)

    ski_units = max(int(ski_rack_count or 0), 1 if ski_rack_included else 0)
    box_units = max(int(roof_box_count or 0), 1 if roof_box_included else 0)
    cross_units = max(int(crossbars_count or 0), 1 if crossbars_included else 0)
    gear_total = _round_money(
        ski_daily * ski_units * days + autobox_daily * box_units * days + crossbars_daily * cross_units * days
    )

    equipment_override = _round_money(equipment_manual_total)
    if equipment_override > 0:
        equipment_manual = equipment_override
        seats_total = boosters_total = gear_total = Decimal("0")
    else:
        equipment_manual = Decimal("0")

    extras_total = _round_money(
        car_wash_total
        + night_total
        + delivery_total
        + seats_total
        + boosters_total
        + gear_total
        + equipment_manual
    )
    subtotal = _round_money(base_total + extras_total)

    discount_value = _round_money(discount_amount)
    discount_value = max(min(discount_value, subtotal), Decimal("0"))

    percent_raw = _to_decimal(discount_percent)
    if percent_raw < 0:
        percent_raw = Decimal("0.00")
    if percent_raw > 100:
        percent_raw = Decimal("100.00")
    percent_base = max(base_total, Decimal("0"))
    discount_percent_amount = _round_money(percent_base * percent_raw / Decimal("100"))
    max_percent_discount = max(subtotal - discount_value, Decimal("0"))
    if discount_percent_amount > max_percent_discount:
        discount_percent_amount = max_percent_discount

    total_price = _round_money(subtotal - discount_value - discount_percent_amount)
    prepayment_value = _round_money(prepayment)
    if prepayment_value < 0:
        prepayment_value = Decimal("0")
    balance_due = _round_money(max(total_price - prepayment_value, Decimal("0")))

    return PricingBreakdown(
        days=days,
        daily_rate=base_daily_rate,
        base_total=base_total,
        car_wash_total=car_wash_total,
        night_total=night_total,
        delivery_total=delivery_total,
        seats_total=seats_total,
        boosters_total=boosters_total,
        gear_total=gear_total,
        equipment_manual_total=equipment_manual,
        extras_total=extras_total,
        discount_amount=discount_value,
        discount_percent_amount=discount_percent_amount,
        subtotal=subtotal,
        total_price=total_price,
        prepayment=prepayment_value,
        balance_due=balance_due,
    )
