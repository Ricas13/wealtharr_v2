from django import template

from tenancy import access

register = template.Library()


@register.filter
def can(user, key):
    """``{% if user|can:"study.simulate" %}``"""
    return access.can(user, key)
