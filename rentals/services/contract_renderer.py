from io import BytesIO
import logging
import re
from decimal import Decimal
from typing import Iterable

from django.template import engines
from django.utils import timezone
from docx import Document
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject
from xhtml2pdf import pisa

from ..models import ContractTemplate, Rental

logger = logging.getLogger(__name__)

# Readable placeholder descriptions for the contract template constructor UI.
PLACEHOLDER_GUIDE = [
    (
        "Клиент",
        {
            "customer.full_name": "ФИО клиента",
            "customer.phone": "Телефон",
            "customer.email": "Email",
            "customer.license_number": "Водительское удостоверение",
            "customer.passport_series": "Серия паспорта",
            "customer.passport_number": "Номер паспорта",
            "customer.passport_issue_date": "Дата выдачи паспорта",
            "customer.passport_issued_by": "Кем выдан паспорт",
            "customer.address": "Адрес клиента",
            "customer.registration_address": "Адрес регистрации",
            "customer.residence_address": "Адрес проживания",
        },
    ),
    (
        "Авто",
        {
            "car.plate_number": "Госномер",
            "car.make": "Марка",
            "car.model": "Модель",
            "car.year": "Год",
            "car.vin": "VIN / номер кузова",
            "car.sts_number": "Номер СТС",
            "car.sts_issue_date": "Дата выдачи СТС",
            "car.sts_issued_by": "Кем выдано СТС",
            "car.label": "Госномер + марка",
        },
    ),
    (
        "Договор",
        {
            "rental.contract_number": "Номер договора",
            "rental.start_date": "Дата начала",
            "rental.end_date": "Дата окончания",
            "rental.start_time": "Время начала",
            "rental.end_time": "Время завершения",
            "rental.date_range": "Диапазон дат",
            "rental.duration_days": "Длительность, дней",
            "rental.daily_rate": "Суточная ставка",
            "rental.total_price": "Стоимость",
            "rental.balance_due": "К оплате после предоплаты",
            "rental.prepayment": "Предоплата",
            "rental.discount_amount": "Скидка суммой",
            "rental.discount_percent": "Скидка, %",
            "rental.airport_fee_start": "Сбор при выдаче в аэропорту",
            "rental.airport_fee_end": "Сбор при возврате в аэропорту",
            "rental.night_fee_start": "Ночной выход (выдача)",
            "rental.night_fee_end": "Ночной выход (возврат)",
            "rental.delivery_issue_city": "Город выдачи (доставка)",
            "rental.delivery_issue_fee": "Сумма выдачи (доставка)",
            "rental.delivery_return_city": "Город возврата (доставка)",
            "rental.delivery_return_fee": "Сумма возврата (доставка)",
            "rental.child_seat_included": "Детское кресло включено",
            "rental.child_seat_count": "Количество кресел",
            "rental.booster_included": "Бустер включен",
            "rental.booster_count": "Количество бустеров",
            "rental.ski_rack_included": "Багажник для лыж включен",
            "rental.ski_rack_count": "Количество багажников",
            "rental.roof_box_included": "Бокс на крышу включен",
            "rental.roof_box_count": "Количество боксов",
            "rental.crossbars_included": "Поперечины включены",
            "rental.crossbars_count": "Количество поперечин",
            "rental.equipment_manual_total": "Фиксированная сумма за оборудование",
            "rental.deal_name": "Удобочитаемое имя сделки",
        },
    ),
    (
        "Служебное",
        {
            "meta.today": "Текущая дата",
            "meta.generated_at": "Дата и время генерации",
        },
    ),
]


def get_contract_context(rental: Rental) -> dict:
    start_date = rental.start_date
    end_date = rental.end_date
    duration_days = None
    date_range = ""
    if start_date and end_date:
        duration_days = (end_date - start_date).days + 1
        date_range = f"{_fmt_date(start_date)} — {_fmt_date(end_date)}"
    return {
        "rental": rental,
        "car": rental.car,
        "customer": rental.customer,
        "rental_duration_days": duration_days,
        "rental_date_range": date_range,
        "meta": {
            "generated_at": timezone.localtime(),
            "today": timezone.localdate(),
        },
    }


def _normalize_html_charset(html: str, target: str = "utf-8") -> str:
    """
    Force HTML to declare UTF-8 so browsers decode Russian text correctly.

    Word-exported HTML often keeps a windows-1251 meta tag while Django still
    encodes the response as UTF-8, which results in mojibake. We rewrite the
    charset declaration (both <meta charset=...> and http-equiv variants) or
    inject one if missing.
    """
    charset_re = re.compile(r'(<meta[^>]+charset=)([\"\\\']?)([^\"\\\' >]+)', re.IGNORECASE)
    http_equiv_re = re.compile(
        r'(<meta[^>]+http-equiv=[\"\\\']?content-type[\"\\\']?[^>]+charset=)([^\"\\\' >]+)',
        re.IGNORECASE,
    )

    updated, replaced = charset_re.subn(rf"\1\2{target}", html, count=1)
    if replaced == 0:
        updated, replaced = http_equiv_re.subn(rf"\1{target}", html, count=1)

    if replaced == 0:
        head_match = re.search(r"<head[^>]*>", html, flags=re.IGNORECASE)
        meta_tag = f'<meta charset="{target}">'
        if head_match:
            insert_at = head_match.end()
            updated = html[:insert_at] + meta_tag + html[insert_at:]
        else:
            updated = meta_tag + html

    return updated


def _fmt_date(value) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _fmt_time(value) -> str:
    return value.strftime("%H:%M") if value else ""


def _fmt_decimal(value) -> str:
    if value is None:
        return ""
    try:
        number = Decimal(value)
        return f"{number:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value)


def _fmt_bool(value) -> str:
    return "Да" if bool(value) else "Нет"


def build_placeholder_values(rental: Rental) -> dict[str, str]:
    """Flatten rental/car/customer data into string placeholders."""
    customer = rental.customer
    car = rental.car
    start_date = rental.start_date
    end_date = rental.end_date
    duration_days = (end_date - start_date).days + 1 if start_date and end_date else None
    date_range = ""
    if start_date or end_date:
        date_range = " — ".join(filter(None, (_fmt_date(start_date), _fmt_date(end_date))))

    values = {
        # Client
        "customer.full_name": customer.full_name,
        "customer.phone": customer.phone,
        "customer.email": customer.email,
        "customer.license_number": customer.license_number,
        "customer.passport_series": customer.passport_series,
        "customer.passport_number": customer.passport_number,
        "customer.passport_issue_date": _fmt_date(customer.passport_issue_date),
        "customer.passport_issued_by": customer.passport_issued_by,
        "customer.address": customer.address,
        "customer.registration_address": customer.registration_address,
        "customer.residence_address": customer.residence_address,
        # Car
        "car.plate_number": car.plate_number,
        "car.make": car.make,
        "car.model": car.model,
        "car.year": car.year,
        "car.vin": car.vin,
        "car.sts_number": car.sts_number,
        "car.sts_issue_date": _fmt_date(car.sts_issue_date),
        "car.sts_issued_by": car.sts_issued_by,
        "car.label": str(car),
        # Rental
        "rental.contract_number": rental.contract_number or "",
        "rental.start_date": _fmt_date(start_date),
        "rental.end_date": _fmt_date(end_date),
        "rental.start_time": _fmt_time(rental.start_time),
        "rental.end_time": _fmt_time(rental.end_time),
        "rental.date_range": date_range,
        "rental.duration_days": duration_days or "",
        "rental.daily_rate": _fmt_decimal(rental.daily_rate),
        "rental.total_price": _fmt_decimal(rental.total_price),
        "rental.balance_due": _fmt_decimal(rental.balance_due),
        "rental.prepayment": _fmt_decimal(rental.prepayment),
        "rental.discount_amount": _fmt_decimal(rental.discount_amount),
        "rental.discount_percent": _fmt_decimal(rental.discount_percent),
        "rental.airport_fee_start": _fmt_decimal(rental.airport_fee_start),
        "rental.airport_fee_end": _fmt_decimal(rental.airport_fee_end),
        "rental.night_fee_start": _fmt_decimal(rental.night_fee_start),
        "rental.night_fee_end": _fmt_decimal(rental.night_fee_end),
        "rental.delivery_issue_city": rental.delivery_issue_city,
        "rental.delivery_issue_fee": _fmt_decimal(rental.delivery_issue_fee),
        "rental.delivery_return_city": rental.delivery_return_city,
        "rental.delivery_return_fee": _fmt_decimal(rental.delivery_return_fee),
        "rental.child_seat_included": _fmt_bool(rental.child_seat_included),
        "rental.child_seat_count": rental.child_seat_count,
        "rental.booster_included": _fmt_bool(rental.booster_included),
        "rental.booster_count": rental.booster_count,
        "rental.ski_rack_included": _fmt_bool(rental.ski_rack_included),
        "rental.ski_rack_count": rental.ski_rack_count,
        "rental.roof_box_included": _fmt_bool(rental.roof_box_included),
        "rental.roof_box_count": rental.roof_box_count,
        "rental.crossbars_included": _fmt_bool(rental.crossbars_included),
        "rental.crossbars_count": rental.crossbars_count,
        "rental.equipment_manual_total": _fmt_decimal(rental.equipment_manual_total),
        "rental.deal_name": rental.deal_name,
        # Meta
        "meta.today": _fmt_date(timezone.localdate()),
        "meta.generated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M"),
    }

    return {key: "" if value is None else str(value) for key, value in values.items()}


def placeholder_token_map(rental: Rental) -> dict[str, str]:
    """
    Build a mapping of popular placeholder token variants so DOCX/PDF can
    perform quick replacements.
    """
    mapping: dict[str, str] = {}
    values = build_placeholder_values(rental)
    for dotted, value in values.items():
        flat = dotted.replace(".", "_")
        variants = (
            f"{{{{ {dotted} }}}}",
            f"{{{{{dotted}}}}}",
            f"{{{{ {flat} }}}}",
            f"{{{{{flat}}}}}",
            flat,
        )
        for token in variants:
            mapping[token] = value
    return mapping


def placeholder_guide() -> list[dict]:
    """Provide grouped placeholder hints for the constructor UI."""
    groups = []
    for title, items in PLACEHOLDER_GUIDE:
        groups.append(
            {
                "title": title,
                "items": [
                    {
                        "token": f"{{{{ {key} }}}}",
                        "alt": key.replace(".", "_"),
                        "description": description,
                    }
                    for key, description in items.items()
                ],
            }
        )
    return groups


def render_html_template(contract_template: ContractTemplate, rental: Rental) -> str:
    if not contract_template.body_html:
        raise ValueError("HTML template body is empty.")

    django_engine = engines["django"]
    template = django_engine.from_string(contract_template.body_html)
    context = get_contract_context(rental)
    html = template.render(context)
    return _normalize_html_charset(html)


def render_html_to_pdf(html: str) -> bytes:
    """Convert HTML to PDF bytes using xhtml2pdf."""
    output = BytesIO()
    result = pisa.CreatePDF(html, dest=output, encoding="utf-8")
    output.seek(0)
    if result.err:
        raise ValueError("Could not render PDF from HTML template.")
    return output.getvalue()


def _replace_in_paragraphs(paragraphs: Iterable, mapping: dict[str, str]):
    """Replace placeholders in a list of DOCX paragraphs."""
    for paragraph in paragraphs:
        original = paragraph.text
        updated = original
        for key, value in mapping.items():
            if key in updated:
                updated = updated.replace(key, value)
        if updated != original:
            paragraph.text = updated


def render_docx(contract_template: ContractTemplate, rental: Rental) -> BytesIO:
    """
    Load a DOCX template and replace placeholders like {{ customer.full_name }}.
    Applies replacements in paragraphs, tables, headers and footers.
    """
    document = Document(contract_template.file.path)
    mapping = placeholder_token_map(rental)

    _replace_in_paragraphs(document.paragraphs, mapping)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                _replace_in_paragraphs(cell.paragraphs, mapping)

    for section in document.sections:
        _replace_in_paragraphs(section.header.paragraphs, mapping)
        _replace_in_paragraphs(section.footer.paragraphs, mapping)

    output = BytesIO()
    document.save(output)
    output.seek(0)
    return output


def render_pdf(contract_template: ContractTemplate, rental: Rental) -> BytesIO:
    """
    Render a PDF contract from either HTML body (converted to PDF) or a
    fillable PDF template with AcroForm fields.
    """
    if contract_template.file:
        return _fill_pdf_form(contract_template.file.path, rental)

    if contract_template.body_html:
        html = render_html_template(contract_template, rental)
        pdf_bytes = render_html_to_pdf(html)
        return BytesIO(pdf_bytes)

    raise ValueError("PDF template requires either HTML body or an uploaded PDF file.")


def _fill_pdf_form(template_path: str, rental: Rental) -> BytesIO:
    """
    Fill a PDF with form fields that match placeholder names (underscored),
    e.g. customer_full_name or rental_contract_number.
    """
    field_values = {key.replace(".", "_"): value for key, value in build_placeholder_values(rental).items()}
    reader = PdfReader(template_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    if reader.get_fields():
        for page in writer.pages:
            writer.update_page_form_field_values(page, field_values)

    try:
        acroform = writer._root_object.get("/AcroForm")  # type: ignore[attr-defined]
        if acroform is not None:
            acroform.update({NameObject("/NeedAppearances"): BooleanObject(True)})
    except Exception:
        logger.debug("Could not mark PDF appearance stream; continuing with raw output.")

    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output
