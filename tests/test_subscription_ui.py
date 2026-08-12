import asyncio
from dataclasses import dataclass
from datetime import datetime
import importlib
import sys
import types as python_types
import unittest
from unittest.mock import patch

from services.access import AccessDecision, AccessStatus
from services.payment_domain import PaymentStatus
from services.payment_provider import PaymentProviderUnavailable
from services.payment_service import (
    PaymentServiceReason,
    PaymentServiceResult,
    SubscriptionProduct,
)
from storage.payment_runtime import (
    PaymentRuntime,
    PaymentRuntimeConfigurationError,
    build_payment_runtime,
)


class _Dispatcher:
    def callback_query(self, *args, **kwargs):
        return lambda handler: handler


def _load_handler_module():
    config = python_types.ModuleType("storage.config")
    config.dp = _Dispatcher()
    with patch.dict(sys.modules, {"storage.config": config}):
        sys.modules.pop("handlers.subscription", None)
        return importlib.import_module("handlers.subscription")


subscription_ui = _load_handler_module()
PRODUCT = SubscriptionProduct("monthly_30d_v1", 99000, "RUB", 30)


@dataclass
class _User:
    id: int = 7


@dataclass
class _FromUser:
    id: int = 101


class _Message:
    def __init__(self) -> None:
        self.edits: list[tuple[str, object]] = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class _Call:
    def __init__(self, data="subscription", user_id=101) -> None:
        self.message = _Message()
        self.from_user = _FromUser(user_id)
        self.data = data
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class _State:
    def __init__(self) -> None:
        self.clear_count = 0

    async def clear(self):
        self.clear_count += 1


class _PaymentService:
    def __init__(self, *, latest=None, create=None, reconcile=None) -> None:
        self.latest = latest
        self.create = create
        self.reconcile = reconcile
        self.create_calls = []
        self.reconcile_calls = []

    def get_latest_payment(self, user_id):
        return self.latest

    def get_or_create_payment(self, user_id):
        self.create_calls.append(user_id)
        if isinstance(self.create, Exception):
            raise self.create
        return self.create

    def reconcile_payment(self, payment_id, *, user_id):
        self.reconcile_calls.append((payment_id, user_id))
        if isinstance(self.reconcile, Exception):
            raise self.reconcile
        return self.reconcile


def result(
    status=PaymentStatus.PENDING,
    *,
    reason=PaymentServiceReason.NOT_SUCCEEDED,
    payment_id=11,
    url="https://example.invalid/pay",
    start=None,
    end=None,
):
    return PaymentServiceResult(
        payment_id=payment_id,
        status=status,
        confirmation_url=url,
        reason=reason,
        grant_started_at=start,
        grant_ends_at=end,
    )


class SubscriptionUiTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def runtime(self, service):
        return PaymentRuntime(PRODUCT, service)

    @staticmethod
    def callbacks(markup):
        return [button.callback_data for row in markup.inline_keyboard for button in row]

    def test_menu_is_visible_and_displays_trial_price_without_fallback(self):
        service = _PaymentService()
        runtime = self.runtime(service)
        text = subscription_ui.format_subscription_screen(
            runtime,
            AccessDecision(AccessStatus.TRIAL, datetime(2026, 8, 14, 12, 0)),
            None,
        )
        self.assertIn("пробный период до 14.08.2026 12:00", text)
        self.assertIn("Подписка: 30 дней", text)
        self.assertIn("990 ₽", text)
        self.assertNotIn(PRODUCT.product_code, text)
        self.assertIn(
            "subscription:pay",
            self.callbacks(
                subscription_ui._payment_markup(
                    None,
                    AccessDecision(
                        AccessStatus.TRIAL,
                        datetime(2026, 8, 14, 12, 0),
                    ),
                )
            ),
        )
        for invalid_amount in ("not-a-price", "0"):
            with self.subTest(invalid_amount=invalid_amount):
                with self.assertRaises(PaymentRuntimeConfigurationError):
                    build_payment_runtime(lambda _: invalid_amount)

    def test_paid_and_expired_access_are_displayed(self):
        runtime = self.runtime(_PaymentService())
        paid = subscription_ui.format_subscription_screen(
            runtime,
            AccessDecision(AccessStatus.ACTIVE, datetime(2026, 9, 1, 9, 0)),
            result(PaymentStatus.SUCCEEDED, end=datetime(2026, 9, 1, 9, 0)),
        )
        expired = subscription_ui.format_subscription_screen(
            runtime, AccessDecision(AccessStatus.EXPIRED, None), None
        )
        self.assertIn("подписка активна до 01.09.2026 09:00", paid)
        self.assertIn("активной подписки нет", expired)

    def test_paid_active_hides_pay_and_check_buttons(self):
        paid = result(
            PaymentStatus.SUCCEEDED,
            end=datetime(2026, 9, 1, 9, 0),
        )
        decision = AccessDecision(AccessStatus.ACTIVE, datetime(2026, 9, 1, 9, 0))
        markup = subscription_ui._payment_markup(paid, decision)
        callbacks = self.callbacks(markup)
        self.assertNotIn("subscription:pay", callbacks)
        self.assertNotIn("subscription:check:11", callbacks)
        self.assertIn("Доступ: подписка активна до 01.09.2026 09:00", subscription_ui.format_subscription_screen(self.runtime(_PaymentService()), decision, paid))

    def test_trial_with_paid_reserved_hides_payment_actions_and_explains_dates(self):
        payment = result(
            PaymentStatus.SUCCEEDED,
            start=datetime(2026, 8, 14, 12, 0),
            end=datetime(2026, 9, 13, 12, 0),
        )
        decision = AccessDecision(AccessStatus.TRIAL, datetime(2026, 8, 14, 12, 0))
        with patch.object(
            subscription_ui,
            "utc_now",
            return_value=datetime(2026, 8, 12, 12, 0),
        ):
            text = subscription_ui.format_subscription_screen(
                self.runtime(_PaymentService()), decision, payment
            )
            callbacks = self.callbacks(subscription_ui._payment_markup(payment, decision))
        self.assertIn("Доступ: пробный период до 14.08.2026 12:00", text)
        self.assertIn("Подписка оплачена", text)
        self.assertIn("Платный период: 14.08.2026 12:00 — 13.09.2026 12:00", text)
        self.assertNotIn("Подписка активирована", text)
        self.assertNotIn("subscription:pay", callbacks)
        self.assertNotIn("subscription:check:11", callbacks)

    def test_paid_reserved_stale_pay_callback_does_not_create_payment(self):
        payment = result(
            PaymentStatus.SUCCEEDED,
            start=datetime(2026, 8, 14, 12, 0),
            end=datetime(2026, 9, 13, 12, 0),
        )
        service = _PaymentService(latest=payment)
        call = _Call("subscription:pay")
        with (
            patch.object(
                subscription_ui,
                "utc_now",
                return_value=datetime(2026, 8, 12, 12, 0),
            ),
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(
                subscription_ui,
                "get_payment_runtime",
                return_value=self.runtime(service),
            ),
            patch.object(
                subscription_ui,
                "get_access_decision",
                return_value=AccessDecision(
                    AccessStatus.TRIAL,
                    datetime(2026, 8, 14, 12, 0),
                ),
            ),
        ):
            self.run_async(subscription_ui.subscription_pay(call, _State()))
        self.assertEqual([], service.create_calls)
        text, markup = call.message.edits[-1]
        self.assertIn("Подписка оплачена", text)
        self.assertNotIn("subscription:pay", self.callbacks(markup))

    def test_paid_active_stale_pay_callback_does_not_create_payment(self):
        service = _PaymentService(latest=result(PaymentStatus.SUCCEEDED))
        call = _Call("subscription:pay")
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.ACTIVE, datetime(2026, 9, 1, 9, 0))),
        ):
            self.run_async(subscription_ui.subscription_pay(call, _State()))
        self.assertEqual([], service.create_calls)
        self.assertNotIn("subscription:pay", self.callbacks(call.message.edits[-1][1]))

    def test_subscription_screen_reads_latest_durable_payment(self):
        pending = result()
        service = _PaymentService(latest=pending)
        call = _Call()
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)),
        ):
            self.run_async(subscription_ui.subscription_screen(call, _State()))
        text, markup = call.message.edits[-1]
        self.assertIn("Оплата ожидает подтверждения", text)
        self.assertIn("subscription:check:11", self.callbacks(markup))
        self.assertIn("subscription:pay", self.callbacks(markup))

    def test_restart_style_recreation_reads_the_same_durable_payment(self):
        pending = result()
        call = _Call()
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(
                subscription_ui,
                "get_payment_runtime",
                side_effect=[
                    self.runtime(_PaymentService(latest=pending)),
                    self.runtime(_PaymentService(latest=pending)),
                ],
            ),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)),
        ):
            self.run_async(subscription_ui.subscription_screen(call, _State()))
            self.run_async(subscription_ui.subscription_screen(call, _State()))
        self.assertEqual(call.message.edits[0][0], call.message.edits[1][0])

    def test_pay_creates_once_and_reuses_payment_via_service(self):
        pending = result(reason=PaymentServiceReason.CREATED)
        service = _PaymentService(create=pending)
        call = _Call("subscription:pay")
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)),
        ):
            self.run_async(subscription_ui.subscription_pay(call, _State()))
            self.run_async(subscription_ui.subscription_pay(call, _State()))
        self.assertEqual([7, 7], service.create_calls)
        markup = call.message.edits[-1][1]
        self.assertIn("subscription:check:11", self.callbacks(markup))
        self.assertEqual("https://example.invalid/pay", markup.inline_keyboard[0][0].url)

    def test_pending_payment_keeps_link_and_check_button(self):
        pending = result()
        markup = subscription_ui._payment_markup(
            pending,
            AccessDecision(AccessStatus.TRIAL_AVAILABLE, None),
        )
        self.assertEqual("https://example.invalid/pay", markup.inline_keyboard[0][0].url)
        self.assertIn("subscription:check:11", self.callbacks(markup))
        self.assertIn("subscription:pay", self.callbacks(markup))

    def test_check_callback_rejects_product_or_price_fields(self):
        self.assertEqual(11, subscription_ui._safe_payment_id("subscription:check:11"))
        for callback_data in (
            "subscription:check:11:10000",
            "subscription:check:11:RUB",
            "subscription:check:11:monthly_30d_v1",
        ):
            with self.subTest(callback_data=callback_data):
                self.assertIsNone(subscription_ui._safe_payment_id(callback_data))

    def test_check_pending_waiting_succeeded_and_terminal_statuses(self):
        cases = (
            (PaymentStatus.PENDING, "Оплата ожидает подтверждения"),
            (PaymentStatus.WAITING_FOR_CAPTURE, "Платёж обрабатывается"),
            (PaymentStatus.SUCCEEDED, "Доступ: подписка активна до 15.09.2026 12:00"),
            (PaymentStatus.CANCELED, "Платёж отменён"),
            (PaymentStatus.EXPIRED, "Срок оплаты истёк"),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                service = _PaymentService(
                    reconcile=result(
                        status,
                        end=datetime(2026, 9, 15, 12, 0)
                        if status is PaymentStatus.SUCCEEDED
                        else None,
                    )
                )
                call = _Call("subscription:check:11")
                with (
                    patch.object(subscription_ui.User, "get", return_value=_User()),
                    patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
                    patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.ACTIVE if status is PaymentStatus.SUCCEEDED else AccessStatus.EXPIRED, datetime(2026, 9, 15, 12, 0) if status is PaymentStatus.SUCCEEDED else None)),
                ):
                    self.run_async(subscription_ui.subscription_check(call, _State()))
                self.assertIn(expected, call.message.edits[-1][0])
                self.assertEqual([(11, 7)], service.reconcile_calls)

    def test_check_twice_delegates_idempotency_to_service(self):
        succeeded = result(
            PaymentStatus.SUCCEEDED,
            reason=PaymentServiceReason.ACCESS_ALREADY_APPLIED,
            end=datetime(2026, 9, 15, 12, 0),
        )
        service = _PaymentService(reconcile=succeeded)
        call = _Call("subscription:check:11")
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.ACTIVE, datetime(2026, 9, 15, 12, 0))),
        ):
            self.run_async(subscription_ui.subscription_check(call, _State()))
            self.run_async(subscription_ui.subscription_check(call, _State()))
        self.assertEqual([(11, 7), (11, 7)], service.reconcile_calls)
        self.assertNotIn(
            "subscription:check:11", self.callbacks(call.message.edits[-1][1])
        )

    def test_provider_error_ownership_and_malformed_callbacks_are_safe(self):
        service = _PaymentService(reconcile=PaymentProviderUnavailable("timeout"))
        unavailable = _Call("subscription:check:11")
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
        ):
            self.run_async(subscription_ui.subscription_check(unavailable, _State()))
        self.assertIn("не удалось проверить оплату", unavailable.message.edits[-1][0])

        foreign = _Call("subscription:check:11")
        foreign_service = _PaymentService(reconcile=PermissionError("foreign"))
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(foreign_service)),
        ):
            self.run_async(subscription_ui.subscription_check(foreign, _State()))
        self.assertTrue(foreign.answers[-1][1]["show_alert"])

        malformed = _Call("subscription:check:11:999")
        with patch.object(subscription_ui.User, "get", return_value=_User()):
            self.run_async(subscription_ui.subscription_check(malformed, _State()))
        self.assertTrue(malformed.answers[-1][1]["show_alert"])

    def test_runtime_configuration_error_is_neutral_and_has_no_sdk_in_handler(self):
        call = _Call()
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", side_effect=PaymentRuntimeConfigurationError()),
        ):
            self.run_async(subscription_ui.subscription_screen(call, _State()))
        self.assertIn("временно недоступна", call.message.edits[-1][0])
        self.assertNotIn("yookassa", subscription_ui.__dict__)


if __name__ == "__main__":
    unittest.main()
