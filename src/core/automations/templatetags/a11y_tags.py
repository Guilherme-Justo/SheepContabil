from django import template
from django.forms import BoundField

register = template.Library()

@register.filter
def a11y(field: BoundField) -> str:
    """
    Renders a Django form field with accessibility attributes.
    """
    if not isinstance(field, BoundField):
        return str(field)

    attrs = {}
    described_by = []

    if field.help_text:
        described_by.append(f"{field.id_for_label}_helptext")
    
    if field.errors:
        attrs["aria-invalid"] = "true"
        described_by.append(f"{field.id_for_label}_errors")

    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)

    return field.as_widget(attrs=attrs)
