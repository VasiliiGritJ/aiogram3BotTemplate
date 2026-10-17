"""Deterministic receipt provider with no network or external state."""

from collections import deque
from collections.abc import Iterable

from services.receipt_domain import ReceiptIntent, ReceiptProviderOutcome
from services.receipt_provider import ReceiptProviderResult


class FakeReceiptProvider:
    def __init__(
        self,
        results: Iterable[ReceiptProviderResult | Exception] = (),
    ) -> None:
        self._results = deque(results)
        self._by_key: dict[str, ReceiptProviderResult] = {}
        self.register_calls: list[tuple[ReceiptIntent, str]] = []
        self.get_calls: list[str] = []
        self.void_calls: list[tuple[str, str]] = []

    def register_income_receipt(
        self,
        intent: ReceiptIntent,
        idempotency_key: str,
    ) -> ReceiptProviderResult:
        self.register_calls.append((intent, idempotency_key))
        existing = self._by_key.get(idempotency_key)
        if existing is not None:
            return existing
        if not self._results:
            raise RuntimeError("No fake receipt response configured.")
        result = self._results.popleft()
        if isinstance(result, Exception):
            raise result
        if result.outcome is ReceiptProviderOutcome.SUCCEEDED:
            self._by_key[idempotency_key] = result
        return result

    def get_receipt(self, provider_receipt_id: str) -> ReceiptProviderResult:
        self.get_calls.append(provider_receipt_id)
        for result in self._by_key.values():
            if result.provider_receipt_id == provider_receipt_id:
                return result
        raise RuntimeError("Unknown fake receipt.")

    def void_receipt(
        self,
        provider_receipt_id: str,
        idempotency_key: str,
    ) -> ReceiptProviderResult:
        self.void_calls.append((provider_receipt_id, idempotency_key))
        raise RuntimeError("Receipt corrections are outside Stage 8D1.")
