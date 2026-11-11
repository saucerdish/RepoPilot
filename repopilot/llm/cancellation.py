"""A user-requested stop, separate from provider and transport failures."""


class RequestCancelled(Exception):
    pass
