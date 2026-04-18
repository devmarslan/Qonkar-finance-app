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
