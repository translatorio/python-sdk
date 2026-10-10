"""The official Python client for Translatorio.

    from translatorio import Client

    tr = Client("tr_live_...")
    result = tr.translate("Add to cart", "es", glossary="shop")

    if result.intact:
        publish(result.translation)
    else:
        hold_for_a_person(result.id)
"""

from . import webhooks
from .client import UNSET, VERSION, Client, Transport, UrllibTransport
from .errors import (
    AuthenticationError,
    InvalidRequest,
    NotFound,
    QuotaExhausted,
    RateLimited,
    ServerError,
    TranslatorioError,
)
from .models import (
    Batch,
    Correction,
    Detection,
    FileTranslation,
    Glossary,
    ImageTranslation,
    Page,
    Translation,
    TranslationRecord,
)

__all__ = [
    "UNSET",
    "VERSION",
    "AuthenticationError",
    "Batch",
    "Client",
    "Correction",
    "Detection",
    "FileTranslation",
    "Glossary",
    "ImageTranslation",
    "InvalidRequest",
    "NotFound",
    "Page",
    "QuotaExhausted",
    "RateLimited",
    "ServerError",
    "Translation",
    "TranslationRecord",
    "TranslatorioError",
    "Transport",
    "UrllibTransport",
    "webhooks",
]

__version__ = VERSION
