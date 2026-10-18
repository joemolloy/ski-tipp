TIPP_ASSIST_GROUP = "Tipp-Assist"


def can_use_tipp_assist(user):
    """Members of the Tipp-Assist group (set in Admin -> Groups) may use the Tipp-Assist page."""
    return bool(user and user.is_authenticated and user.groups.filter(name=TIPP_ASSIST_GROUP).exists())


def tipp_assist(request):
    return {"tipp_assist_user": can_use_tipp_assist(getattr(request, "user", None))}
