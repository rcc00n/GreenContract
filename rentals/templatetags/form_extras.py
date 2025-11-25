from django import template

register = template.Library()


@register.filter
def add_class(field, css_class):
    """Return field rendered with an extra CSS class."""
    existing = field.field.widget.attrs.get("class", "")
    classes = f"{existing} {css_class}".strip()
    return field.as_widget(attrs={**field.field.widget.attrs, "class": classes})
