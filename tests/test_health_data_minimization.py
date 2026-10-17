"""MVP health collection is disabled, including legacy FSM recovery."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, StorageKey
from sqlalchemy import inspect

from db.models import FitnessProfile
from handlers import onboarding
from handlers import profile as profile_ui
from services.training_safety import TRAINING_SAFETY_NOTICE
from services.user_programs import AdaptationMode, DeterministicRussianProgramParser, UserProgramDraft
from services.workout_plans import normalize_profile, _profile_signature
from storage.states import Onboarding


ROOT = Path(__file__).resolve().parents[1]


class NoHealthMessage:
    """Fail if a legacy handler reads unsolicited text or media."""
    def __init__(self):
        self.answer = AsyncMock()

    @property
    def text(self):
        raise AssertionError("Health text must not be read")


class HealthMinimizationTests(unittest.TestCase):
    def data(self):
        return dict(age=30, sex="male", height_cm=180, weight_kg=80,
                    goal="muscle_gain", experience_level="beginner",
                    training_environment="gym", workouts_per_week=3,
                    session_duration_minutes=60)

    def test_weight_goes_directly_to_confirmation_without_health_question(self):
        async def scenario():
            storage = MemoryStorage()
            state = FSMContext(storage, StorageKey(bot_id=1, chat_id=2, user_id=2))
            await state.set_data({**self.data(), "limitations": "private legacy fixture"})
            await state.set_state(Onboarding.weight_kg)
            message = SimpleNamespace(text="80", answer=AsyncMock())
            await onboarding.onboarding_weight(message, state)
            self.assertEqual(Onboarding.confirmation.state, await state.get_state())
            self.assertNotIn("limitations", await state.get_data())
            text = message.answer.call_args.args[0]
            self.assertNotIn("Травмы или ограничения", text)
            self.assertIn(TRAINING_SAFETY_NOTICE, text)
            self.assertNotIn("private legacy fixture", text)
            await storage.close()
        asyncio.run(scenario())

    def test_legacy_health_state_discards_payload_without_read_echo_or_fsm_write(self):
        async def scenario():
            storage = MemoryStorage()
            state = FSMContext(storage, StorageKey(bot_id=1, chat_id=2, user_id=2))
            await state.set_data({**self.data(), "limitations": "private legacy fixture"})
            await state.set_state(Onboarding.limitations)
            message = NoHealthMessage()
            await onboarding.onboarding_limitations(message, state)
            self.assertNotIn("limitations", await state.get_data())
            self.assertNotIn("private legacy fixture", message.answer.call_args.args[0])
            self.assertEqual(Onboarding.confirmation.state, await state.get_state())
            await storage.close()
        asyncio.run(scenario())

    def test_legacy_health_column_not_selected_or_used_in_plan_signature(self):
        self.assertTrue(inspect(FitnessProfile).attrs.limitations.deferred)
        class Profile(SimpleNamespace):
            @property
            def limitations(self):
                raise AssertionError("Historical health must not be used")
        profile = normalize_profile(Profile(**self.data()))
        self.assertFalse(profile.has_limitations)
        self.assertEqual(_profile_signature(profile), _profile_signature(replace(profile, has_limitations=True)))

    def test_confirmation_failure_cannot_retain_legacy_health_in_fsm(self):
        async def scenario():
            storage = MemoryStorage()
            state = FSMContext(storage, StorageKey(bot_id=1, chat_id=2, user_id=2))
            await state.set_data({**self.data(), "limitations": "private legacy fixture"})
            call = SimpleNamespace(from_user=SimpleNamespace(id=2), answer=AsyncMock(),
                                   message=SimpleNamespace(edit_text=AsyncMock()))
            with patch.object(onboarding.User, "get", return_value=SimpleNamespace(id=2)), \
                 patch.object(onboarding, "save_profile_and_access", side_effect=onboarding.OnboardingPersistenceError()):
                await onboarding.onboarding_confirm(call, state)
            self.assertNotIn("limitations", await state.get_data())
            await storage.close()
        asyncio.run(scenario())

    def test_arbitrary_annotations_and_names_never_enter_structured_program(self):
        parser = DeterministicRussianProgramParser()
        result = parser.parse("День 1 — private medical fixture\nЖим лежа 3x10", AdaptationMode.STRICT)
        self.assertTrue(result.valid)
        self.assertNotIn("private medical fixture", str(result.draft.to_payload()))
        payload = result.draft.to_payload()
        payload["days"][0]["title"] = "private medical fixture"
        payload["days"][0]["exercises"][0]["exercise_name"] = "private medical fixture"
        restored = UserProgramDraft.from_payload(payload)
        self.assertNotIn("private medical fixture", str(restored.to_payload()))
        invalid = parser.parse("private medical fixture 3x10", AdaptationMode.STRICT)
        self.assertFalse(invalid.valid)
        self.assertNotIn("private medical fixture", str(invalid.issues))

    def test_legacy_support_message_not_forwarded(self):
        async def scenario():
            message = NoHealthMessage()
            state = SimpleNamespace(clear=AsyncMock())
            with patch.object(profile_ui, "bot", SimpleNamespace(copy_message=AsyncMock(), send_message=AsyncMock())) as bot:
                await profile_ui.contactWithDevsGoCall(message, state)
                bot.copy_message.assert_not_called()
                bot.send_message.assert_not_called()
            self.assertIn("не пересылаются", message.answer.call_args.args[0])
        asyncio.run(scenario())

    def test_no_active_question_markup_or_profile_health_use(self):
        source = (ROOT / "handlers/onboarding.py").read_text(encoding="utf-8")
        self.assertNotIn("set_state(Onboarding.limitations)", source)
        self.assertNotIn("onboarding_limitations_mkp", source)
        self.assertNotIn("profile.limitations", (ROOT / "handlers/profile.py").read_text(encoding="utf-8"))
        self.assertNotIn("Нет ограничений", (ROOT / "handlers/markups.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
