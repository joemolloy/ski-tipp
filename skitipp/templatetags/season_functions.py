
from skitipp.models import Season
from skitipp.views import get_selected_season

from django import template

register = template.Library()

@register.inclusion_tag('includes/nav_season_list.html', takes_context=True)
def nav_seasons_list(context):
    return {
        'selected_season': get_selected_season(context.request),
        'seasons': Season.objects.all(),
    }

@register.simple_tag(takes_context=True)
def selected_season(context):
    return get_selected_season(context.request)

@register.simple_tag(takes_context=True)
def selected_season_is_current_season(context):
    return get_selected_season(context.request) == Season.objects.filter(current=True).first()

