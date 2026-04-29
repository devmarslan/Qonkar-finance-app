from django import template

register = template.Library()

@register.filter(name='split')
def split(value, arg):
    return value.split(arg)

@register.filter(name='get_attr')
def get_attr(obj, attr_name):
    """
    Get an attribute of an object dynamically from a template.
    """
    return getattr(obj, attr_name, None)

@register.simple_tag
def increment(value):
    return value + 1

@register.filter(name='count_admins')
def count_admins(users):
    return len([u for u in users if u.is_superuser])
