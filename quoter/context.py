def role(request):
    """全局模板上下文：角色判断，避免逐视图传 is_manager。"""
    user = request.user
    if user.is_authenticated:
        groups = set(user.groups.values_list('name', flat=True))
        is_manager = user.is_superuser or '经理' in groups
        is_boss = user.is_superuser or '老板' in groups
    else:
        is_manager = is_boss = False
    return {'is_manager': is_manager, 'is_boss': is_boss}
