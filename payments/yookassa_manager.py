"""Deprecated compatibility import for the injected YooKassa adapter.

The legacy template used to read credentials and configure the SDK while this
module was imported. Runtime composition now belongs exclusively to
``storage.payment_runtime``.
"""

from services.yookassa_provider import YooKassaProvider


YOOKassa = YooKassaProvider

__all__ = ("YOOKassa",)
