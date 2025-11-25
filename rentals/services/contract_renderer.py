from io import BytesIO
import re

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


def render_html_template(contract_template: ContractTemplate, rental: Rental) -> str:
    django_engine = engines["django"]
    template = django_engine.from_string(contract_template.body_html)
    context = get_contract_context(rental)
    html = template.render(context)
    return _normalize_html_charset(html)


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
