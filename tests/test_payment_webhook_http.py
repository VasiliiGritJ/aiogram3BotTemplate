import asyncio
import importlib
import inspect
import json
import sys
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from services.payment_webhook import PaymentWebhookReason, PaymentWebhookResult
from storage.payment_webhook_http import (
    PAYMENT_WEBHOOK_MAX_BODY_BYTES,
    PAYMENT_WEBHOOK_PATH,
    PAYMENT_WEBHOOK_LIVENESS_PATH,
    PAYMENT_WEBHOOK_READINESS_PATH,
    PaymentWebhookHttpConfigurationError,
    PaymentWebhookServerConfig,
    build_payment_webhook_http_runtime,
    create_payment_webhook_app,
    create_payment_webhook_handler,
)
from storage.payment_runtime import PaymentMode, PaymentRuntime


class _Request:
    def __init__(self, *, payload=None, content_type="application/json", error=None):
        self.content_type = content_type
        self._payload = payload
        self._error = error

    async def json(self):
        if self._error is not None:
            raise self._error
        return self._payload


class _Processor:
    def __init__(self, *results):
        self._results = list(results)
        self.payloads = []

    def process(self, payload):
        self.payloads.append(payload)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def decode(response: web.Response):
    return json.loads(response.body.decode("utf-8"))


class PaymentWebhookHttpTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def call(self, processor, request):
        return self.run_async(create_payment_webhook_handler(processor)(request))

    def test_valid_and_duplicate_reconciled_notifications_return_200(self) -> None:
        processor = _Processor(
            PaymentWebhookResult(PaymentWebhookReason.RECONCILED),
            PaymentWebhookResult(PaymentWebhookReason.RECONCILED),
        )
        payload = {"type": "notification", "event": "payment.succeeded"}

        first = self.call(processor, _Request(payload=payload))
        duplicate = self.call(processor, _Request(payload=payload))

        self.assertEqual(200, first.status)
        self.assertEqual(200, duplicate.status)
        self.assertEqual({"status": "acknowledged"}, decode(first))
        self.assertEqual([payload, payload], processor.payloads)

    def test_sync_processor_runs_through_thread_boundary(self) -> None:
        processor = _Processor(
            PaymentWebhookResult(PaymentWebhookReason.RECONCILED)
        )

        async def run():
            with patch(
                "storage.payment_webhook_http.asyncio.to_thread",
                side_effect=lambda function, *args: function(*args),
            ) as to_thread:
                response = await create_payment_webhook_handler(processor)(
                    _Request(payload={"event": "payment.succeeded"})
                )
            return response, to_thread

        response, to_thread = self.run_async(run())
        self.assertEqual(200, response.status)
        to_thread.assert_called_once()

    def test_unsupported_and_unknown_payment_are_acknowledged_without_retry(self) -> None:
        processor = _Processor(
            PaymentWebhookResult(PaymentWebhookReason.UNSUPPORTED_EVENT),
            PaymentWebhookResult(PaymentWebhookReason.PAYMENT_NOT_FOUND),
        )

        unsupported = self.call(processor, _Request(payload={"event": "refund"}))
        unknown = self.call(processor, _Request(payload={"event": "payment.succeeded"}))

        self.assertEqual(200, unsupported.status)
        self.assertEqual(200, unknown.status)
        self.assertEqual({"status": "acknowledged"}, decode(unknown))

    def test_malformed_json_and_content_type_are_controlled_4xx(self) -> None:
        processor = _Processor()
        malformed = self.call(
            processor,
            _Request(error=json.JSONDecodeError("secret-body", "secret-body", 0)),
        )
        wrong_type = self.call(
            processor,
            _Request(payload={}, content_type="text/plain"),
        )

        self.assertEqual(400, malformed.status)
        self.assertEqual(415, wrong_type.status)
        self.assertNotIn("secret-body", malformed.text)
        self.assertEqual([], processor.payloads)

    def test_transient_failure_returns_retryable_503_without_details(self) -> None:
        processor = _Processor(
            PaymentWebhookResult(PaymentWebhookReason.PROVIDER_UNAVAILABLE),
            RuntimeError("provider-secret-must-not-leak"),
        )

        controlled = self.call(processor, _Request(payload={}))
        unexpected = self.call(processor, _Request(payload={}))

        self.assertEqual(503, controlled.status)
        self.assertEqual(503, unexpected.status)
        self.assertNotIn("provider-secret-must-not-leak", unexpected.text)
        self.assertEqual({"status": "temporarily_unavailable"}, decode(unexpected))

    def test_reconcile_security_rejection_returns_controlled_non_2xx(self) -> None:
        response = self.call(
            _Processor(PaymentWebhookResult(PaymentWebhookReason.RECONCILE_REJECTED)),
            _Request(payload={}),
        )
        self.assertEqual(409, response.status)
        self.assertEqual({"status": "reconcile_rejected"}, decode(response))

    def test_oversized_body_is_rejected_without_calling_processor(self) -> None:
        processor = _Processor()
        response = self.call(
            processor,
            _Request(
                error=web.HTTPRequestEntityTooLarge(
                    max_size=PAYMENT_WEBHOOK_MAX_BODY_BYTES,
                    actual_size=PAYMENT_WEBHOOK_MAX_BODY_BYTES + 1,
                )
            ),
        )
        self.assertEqual(413, response.status)
        self.assertEqual([], processor.payloads)

    def test_app_has_only_post_route_and_configured_body_limit(self) -> None:
        config = PaymentWebhookServerConfig("127.0.0.1", 8080)
        app = create_payment_webhook_app(_Processor(), config)

        get_match = self.run_async(
            app.router.resolve(make_mocked_request("GET", PAYMENT_WEBHOOK_PATH))
        )
        post_match = self.run_async(
            app.router.resolve(make_mocked_request("POST", PAYMENT_WEBHOOK_PATH))
        )

        self.assertEqual(405, get_match.http_exception.status)
        self.assertIsNone(post_match.http_exception)
        self.assertEqual(PAYMENT_WEBHOOK_MAX_BODY_BYTES, app._client_max_size)

    def test_liveness_and_readiness_are_local_and_non_sensitive(self) -> None:
        processor = _Processor(RuntimeError("provider-must-not-be-called"))
        app = create_payment_webhook_app(
            processor, PaymentWebhookServerConfig("127.0.0.1", 8080)
        )
        live_match = self.run_async(
            app.router.resolve(make_mocked_request("GET", PAYMENT_WEBHOOK_LIVENESS_PATH))
        )
        ready_match = self.run_async(
            app.router.resolve(make_mocked_request("GET", PAYMENT_WEBHOOK_READINESS_PATH))
        )

        live = self.run_async(live_match.handler(make_mocked_request("GET", PAYMENT_WEBHOOK_LIVENESS_PATH)))
        ready = self.run_async(ready_match.handler(make_mocked_request("GET", PAYMENT_WEBHOOK_READINESS_PATH)))

        self.assertEqual(200, live.status)
        self.assertEqual({"status": "live"}, decode(live))
        self.assertEqual(200, ready.status)
        self.assertEqual({"status": "ready"}, decode(ready))
        self.assertEqual([], processor.payloads)

    def test_server_config_is_explicit_and_validated(self) -> None:
        for invalid in (
            ("", 8080),
            ("127.0.0.1", 0),
            ("127.0.0.1", 65536),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    PaymentWebhookServerConfig(*invalid)
        with self.assertRaises(ValueError):
            PaymentWebhookServerConfig("127.0.0.1", 8080, path="not-absolute")

    def test_runtime_composition_uses_injected_config_without_starting_server(self) -> None:
        values = {
            "PAYMENT_WEBHOOK_BIND_HOST": "127.0.0.1",
            "PAYMENT_WEBHOOK_BIND_PORT": "8081",
            "PAYMENT_WEBHOOK_PATH": "/internal/yookassa",
        }
        payment_runtime = PaymentRuntime(object(), object(), PaymentMode.PRODUCTION)

        with patch(
            "storage.payment_webhook_http.build_payment_runtime",
            return_value=payment_runtime,
        ) as payment_builder:
            runtime = build_payment_webhook_http_runtime(values.__getitem__)

        self.assertEqual("127.0.0.1", runtime.config.bind_host)
        self.assertEqual(8081, runtime.config.bind_port)
        self.assertEqual("/internal/yookassa", runtime.config.path)
        payment_builder.assert_called_once_with(values.__getitem__)

        with self.assertRaises(PaymentWebhookHttpConfigurationError):
            build_payment_webhook_http_runtime(
                {"PAYMENT_WEBHOOK_BIND_HOST": "127.0.0.1"}.__getitem__
            )

    def test_test_mode_allows_only_loopback_safe_defaults(self) -> None:
        payment_runtime = PaymentRuntime(object(), object(), PaymentMode.TEST)
        with patch(
            "storage.payment_webhook_http.build_payment_runtime",
            return_value=payment_runtime,
        ):
            runtime = build_payment_webhook_http_runtime({}.__getitem__)
        self.assertEqual("127.0.0.1", runtime.config.bind_host)
        self.assertEqual(8080, runtime.config.bind_port)
        self.assertEqual(PAYMENT_WEBHOOK_PATH, runtime.config.path)

    def test_production_mode_fails_closed_without_all_runtime_values(self) -> None:
        payment_runtime = PaymentRuntime(object(), object(), PaymentMode.PRODUCTION)
        cases = (
            {},
            {
                "PAYMENT_WEBHOOK_BIND_HOST": "127.0.0.1",
                "PAYMENT_WEBHOOK_BIND_PORT": "8080",
            },
            {
                "PAYMENT_WEBHOOK_BIND_HOST": "127.0.0.1",
                "PAYMENT_WEBHOOK_BIND_PORT": "not-a-port",
                "PAYMENT_WEBHOOK_PATH": "/webhook",
            },
        )
        for values in cases:
            with self.subTest(values=values), patch(
                "storage.payment_webhook_http.build_payment_runtime",
                return_value=payment_runtime,
            ):
                with self.assertRaises(PaymentWebhookHttpConfigurationError):
                    build_payment_webhook_http_runtime(values.__getitem__)

    def test_unsafe_paths_are_rejected(self) -> None:
        for path in ("//webhook", "/../webhook", "/webhook?x=1", "/webhook#x"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                PaymentWebhookServerConfig("127.0.0.1", 8080, path=path)

    def test_webhook_logs_only_safe_normalized_metadata(self) -> None:
        secret = "secret-must-not-leak"
        raw_payload = {"event": "payment.succeeded", "token": secret}
        processor = _Processor(PaymentWebhookResult(PaymentWebhookReason.RECONCILED))

        with self.assertLogs("storage.payment_webhook_http", level="INFO") as logs:
            response = self.call(processor, _Request(payload=raw_payload))

        combined = "\n".join(logs.output)
        self.assertEqual(200, response.status)
        self.assertIn("event=payment.succeeded", combined)
        self.assertIn("outcome=reconciled", combined)
        self.assertIn("status=200", combined)
        self.assertNotIn(secret, combined)
        self.assertNotIn("token", combined)
        self.assertNotIn(str(raw_payload), combined)

    def test_malformed_and_transient_logs_have_category_without_raw_content(self) -> None:
        secret = "raw-secret-body"
        processor = _Processor(RuntimeError(secret))
        with self.assertLogs("storage.payment_webhook_http", level="INFO") as logs:
            response = self.call(processor, _Request(payload={"event": "payment.succeeded", "body": secret}))
        combined = "\n".join(logs.output)
        self.assertEqual(503, response.status)
        self.assertIn("event=payment.succeeded", combined)
        self.assertIn("outcome=temporarily_unavailable", combined)
        self.assertNotIn(secret, combined)

    def test_entrypoint_import_has_no_bot_or_environment_side_effects(self) -> None:
        sys.modules.pop("payment_webhook_server", None)
        previous_bot = sys.modules.pop("bot", None)
        try:
            with patch("environs.Env.read_env") as read_env:
                module = importlib.import_module("payment_webhook_server")
            read_env.assert_not_called()
            self.assertNotIn("bot", sys.modules)
            self.assertTrue(callable(module.main))
        finally:
            sys.modules.pop("payment_webhook_server", None)
            if previous_bot is not None:
                sys.modules["bot"] = previous_bot

    def test_http_layer_has_no_access_grant_or_payment_transition_logic(self) -> None:
        import storage.payment_webhook_http as http_module

        source = inspect.getsource(http_module)
        self.assertNotIn("subscription_ends_at", source)
        self.assertNotIn("access_applied_at", source)
        self.assertNotIn("calculate_subscription_grant", source)


if __name__ == "__main__":
    unittest.main()
