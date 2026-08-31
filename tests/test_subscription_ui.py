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
)
from services.receipt_email import ReceiptEmailValidationError
from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT
from storage.payment_runtime import (
    CommercialPublicConfig,
    PaymentMode,
    PaymentRuntime,
    PaymentRuntimeConfigurationError,
)


class _Dispatcher:
    def callback_query(self, *args, **kwargs):
        return lambda handler: handler

    def message(self, *args, **kwargs):
        return lambda handler: handler


def _load_handler_module():
    config = python_types.ModuleType("storage.config")
    config.dp = _Dispatcher()
    with patch.dict(sys.modules, {"storage.config": config}):
        sys.modules.pop("handlers.subscription", None)
        return importlib.import_module("handlers.subscription")


subscription_ui = _load_handler_module()
PRODUCT = LAUNCH_COMMERCIAL_PRODUCT.payment_product


@dataclass
class _User:
    id: int = 7


@dataclass
class _FromUser:
    id: int = 101


class _Message:
    def __init__(self) -> None:
        self.edits: list[tuple[str, object]] = []
        self.answers: list[tuple[str, object]] = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


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
        self.states = []

    async def clear(self):
        self.clear_count += 1

    async def set_state(self, value):
        self.states.append(value)


class _IncomingMessage(_Message):
    def __init__(self, text, user_id=101) -> None:
        super().__init__()
        self.text = text
        self.from_user = _FromUser(user_id)


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
    def setUp(self) -> None:
        self.receipt_email_patcher = patch.object(
            subscription_ui,
            "get_receipt_email",
            new=lambda _user_id: None,
        )
        self.receipt_email_patcher.start()

    def tearDown(self) -> None:
        self.receipt_email_patcher.stop()

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def runtime(self, service, commercial=CommercialPublicConfig()):
        return PaymentRuntime(PRODUCT, service, PaymentMode.TEST, commercial)

    @staticmethod
    def callbacks(markup):
        return [button.callback_data for row in markup.inline_keyboard for button in row]

    def test_menu_is_visible_and_displays_server_owned_purchase_disclosure(self):
        service = _PaymentService()
        runtime = self.runtime(service)
        text = subscription_ui.format_subscription_screen(
            runtime,
            AccessDecision(AccessStatus.TRIAL, datetime(2026, 8, 14, 12, 0)),
            None,
        )
        self.assertIn("пробный период до 14.08.2026 12:00", text)
        self.assertIn("💳 Доступ на 30 дней", text)
        self.assertIn("Стоимость: 499 ₽", text)
        self.assertIn("Автопродления нет.", text)
        self.assertIn("доступ активируется на 30 дней", text)
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

    def test_purchase_disclosure_shows_known_receipt_email_only_in_screen_text(self):
        text = subscription_ui.format_subscription_screen(
            self.runtime(_PaymentService()),
            AccessDecision(AccessStatus.TRIAL_AVAILABLE, None),
            None,
            "user@example.com",
        )
        self.assertIn("Чек отправим на: user@example.com", text)
        markup = subscription_ui._payment_markup(
            None,
            AccessDecision(AccessStatus.TRIAL_AVAILABLE, None),
            self.runtime(_PaymentService()),
        )
        self.assertNotIn("user@example.com", str(markup.inline_keyboard))

    def test_purchase_markup_exposes_valid_legal_actions_support_and_menu(self):
        runtime = self.runtime(
            _PaymentService(),
            CommercialPublicConfig(
                public_site_url="https://example.invalid",
                terms_url="https://example.invalid/terms",
                privacy_url="https://example.invalid/privacy",
                refund_url="https://example.invalid/refund",
                support_email="support@example.invalid",
            ),
        )
        markup = subscription_ui._payment_markup(
            None,
            AccessDecision(AccessStatus.TRIAL_AVAILABLE, None),
            runtime,
        )
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertEqual(
            {
                "🌐 О сервисе",
                "📄 Условия",
                "🔒 Политика конфиденциальности",
                "↩️ Условия отказа и возврата",
            },
            {button.text for button in buttons if button.url},
        )
        self.assertIn("subscription:support", self.callbacks(markup))
        self.assertIn("start", self.callbacks(markup))

    def test_development_without_public_urls_omits_broken_legal_actions(self):
        markup = subscription_ui._payment_markup(
            None,
            AccessDecision(AccessStatus.TRIAL_AVAILABLE, None),
            self.runtime(_PaymentService()),
        )
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertFalse(any(button.url for button in buttons))
        self.assertNotIn("subscription:support", self.callbacks(markup))

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
            patch.object(subscription_ui, "get_receipt_email", return_value="user@example.com"),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)),
        ):
            self.run_async(subscription_ui.subscription_pay(call, _State()))
            self.run_async(subscription_ui.subscription_pay(call, _State()))
        self.assertEqual([7, 7], service.create_calls)
        markup = call.message.edits[-1][1]
        self.assertIn("subscription:check:11", self.callbacks(markup))
        self.assertEqual("https://example.invalid/pay", markup.inline_keyboard[0][0].url)

    def test_missing_receipt_email_prompts_before_any_payment_is_created(self):
        service = _PaymentService(create=result(reason=PaymentServiceReason.CREATED))
        call = _Call("subscription:pay")
        state = _State()
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)),
            patch.object(subscription_ui, "get_receipt_email", return_value=None),
        ):
            self.run_async(subscription_ui.subscription_pay(call, state))
        self.assertEqual([], service.create_calls)
        self.assertIn("Куда отправлять чеки", call.message.edits[-1][0])
        self.assertEqual([subscription_ui.ReceiptEmail.awaiting_payment_email], state.states)

    def test_invalid_receipt_email_does_not_create_payment(self):
        service = _PaymentService(create=result())
        message = _IncomingMessage("bad address")
        state = _State()
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "save_receipt_email", side_effect=ReceiptEmailValidationError("bad")),
        ):
            self.run_async(subscription_ui.subscription_receipt_email_message(message, state))
        self.assertEqual([], service.create_calls)
        self.assertIn("Проверьте email", message.answers[-1][0])
        self.assertEqual(0, state.clear_count)

    def test_valid_receipt_email_is_saved_then_continues_the_same_payment_flow(self):
        pending = result(reason=PaymentServiceReason.CREATED)
        service = _PaymentService(create=pending)
        message = _IncomingMessage("User+tag@Example.com")
        state = _State()
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "save_receipt_email", return_value="user+tag@example.com") as saved,
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)),
        ):
            self.run_async(subscription_ui.subscription_receipt_email_message(message, state))
        saved.assert_called_once_with(7, "User+tag@Example.com")
        self.assertEqual([7], service.create_calls)
        self.assertEqual(1, state.clear_count)
        self.assertIn("Оплата ожидает подтверждения", message.answers[-1][0])
        self.assertIn("Чек отправим на: user+tag@example.com", message.answers[-1][0])

    def test_repeated_pay_after_email_uses_existing_service_idempotency(self):
        pending = result(reason=PaymentServiceReason.CREATED)
        service = _PaymentService(create=pending)
        call = _Call("subscription:pay")
        with (
            patch.object(subscription_ui.User, "get", return_value=_User()),
            patch.object(subscription_ui, "get_payment_runtime", return_value=self.runtime(service)),
            patch.object(subscription_ui, "get_access_decision", return_value=AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)),
            patch.object(subscription_ui, "get_receipt_email", return_value="user@example.com"),
        ):
            self.run_async(subscription_ui.subscription_pay(call, _State()))
            self.run_async(subscription_ui.subscription_pay(call, _State()))
        self.assertEqual([7, 7], service.create_calls)

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

    def test_documents_and_support_use_validated_actions_with_safe_back_navigation(self):
        runtime = self.runtime(
            _PaymentService(),
            CommercialPublicConfig(
                public_site_url="https://example.invalid",
                terms_url="https://example.invalid/terms",
                privacy_url="https://example.invalid/privacy",
                refund_url="https://example.invalid/refund",
                support_email="support@example.invalid",
            ),
        )
        documents = _Call("subscription:documents")
        support = _Call("subscription:support")
        with patch.object(subscription_ui, "get_payment_runtime", return_value=runtime):
            self.run_async(subscription_ui.subscription_documents(documents, _State()))
            self.run_async(subscription_ui.subscription_support(support, _State()))
        document_text, document_markup = documents.message.edits[-1]
        self.assertIn("не заменяет медицинскую помощь", document_text)
        self.assertIn("profile", self.callbacks(document_markup))
        self.assertIn("start", self.callbacks(document_markup))
        support_text, support_markup = support.message.edits[-1]
        self.assertIn("support@example.invalid", support_text)
        self.assertIn("subscription", self.callbacks(support_markup))
        self.assertIn("start", self.callbacks(support_markup))


if __name__ == "__main__":
    unittest.main()
