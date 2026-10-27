"""A plain-language page on what the tool is and is not. Read-only, no data."""
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET

from .core.presentation import NOT_ADVICE
from .v2_views import _render


@login_required
@require_GET
def about_page(request):
    return _render(request, page_key="about", page_title="How this works", not_advice=NOT_ADVICE)
