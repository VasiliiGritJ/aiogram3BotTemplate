"""Deterministic, in-memory payment provider for payment service tests."""

from collections import defaultdict, deque
from collections.abc import Iterable

from services.payment_provider import (
    PaymentCreateRequest,
    PaymentProviderError,
    ProviderPayment,
)


class FakePaymentProvider:
    def __init__(self, create_results: Iterable[ProviderPayment | Exception] = ()) -> None:
        self._create_results = deque(create_results)
        self._by_key: dict[str, ProviderPayment] = {}
        self._get_results: dict[str, deque[ProviderPayment | Exception]] = defaultdict(deque)
        self.create_calls: list[tuple[PaymentCreateRequest, str]] = []
        self.get_calls: list[str] = []

    def queue_get(
        self, provider_payment_id: str, *results: ProviderPayment | Exception
    ) -> None:
        self._get_results[provider_payment_id].extend(results)

    def create_payment(
        self, request: PaymentCreateRequest, idempotency_key: str
    ) -> ProviderPayment:
        self.create_calls.append((request, idempotency_key))
        existing = self._by_key.get(idempotency_key)
        if existing is not None:
            return existing
        if not self._create_results:
            raise PaymentProviderError("No fake create response configured.")
        result = self._create_results.popleft()
        if isinstance(result, Exception):
            raise result
        self._by_key[idempotency_key] = result
        return result

    def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        self.get_calls.append(provider_payment_id)
        queued = self._get_results[provider_payment_id]
        if queued:
            result = queued.popleft()
            if isinstance(result, Exception):
                raise result
            return result
        for payment in self._by_key.values():
            if payment.provider_payment_id == provider_payment_id:
                return payment
        raise PaymentProviderError("Unknown fake provider payment.")
