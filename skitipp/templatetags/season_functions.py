
from skitipp.models import Season

from django import template

register = template.Library()

@register.inclusion_tag('includes/nav_season_list.html', takes_context=True)
def nav_seasons_list(context):
    return {
        'selected_season' : context['selected_season'],
        'seasons': Season.objects.all(),
    }

@register.simple_tag(takes_context=True)
def get_selected_season(context):
    return context['selected_season']

@register.simple_tag(takes_context=True)
def selected_season_is_current_season(context):
    return 'selected_season' in context and context['selected_season'] == Season.objects.filter(current=True).first()

@register.simple_tag
def get_disciplines():
    return ["Slalom", "Giant Slalom", "Super G", "Downhill", "Alpine combined"]