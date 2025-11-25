from io import BytesIO

from django.http import HttpResponse
from django.template import engines
from docx import Document

from ..models import ContractTemplate, Rental


def get_contract_context(rental: Rental) -> dict:
    return {
        "rental": rental,
        "car": rental.car,
        "customer": rental.customer,
    }


def render_html_template(contract_template: ContractTemplate, rental: Rental) -> str:
    django_engine = engines["django"]
    template = django_engine.from_string(contract_template.body_html)
    context = get_contract_context(rental)
    return template.render(context)


def render_html_to_pdf(html: str) -> bytes:
    """
    Placeholder - you can use WeasyPrint, xhtml2pdf, or external service.
    For the plan, just return HTML bytes and call it a 'downloadable HTML'.
    """
    return html.encode("utf-8")


def render_docx(contract_template: ContractTemplate, rental: Rental) -> BytesIO:
    """
    Load a DOCX template and replace placeholders like {{ customer.full_name }}.
    Simple implementation using string replace over each paragraph.
    """
    document = Document(contract_template.file.path)
    context = get_contract_context(rental)

    mapping = {
        "{{ customer.full_name }}": context["customer"].full_name,
        "{{ car.plate_number }}": context["car"].plate_number,
        "{{ rental.start_date }}": str(context["rental"].start_date),
        "{{ rental.end_date }}": str(context["rental"].end_date),
        "{{ rental.total_price }}": str(context["rental"].total_price),
    }

    for paragraph in document.paragraphs:
        for key, value in mapping.items():
            if key in paragraph.text:
                paragraph.text = paragraph.text.replace(key, value)

    output = BytesIO()
    document.save(output)
    output.seek(0)
    return output
