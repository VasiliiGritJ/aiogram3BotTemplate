"""Validation and transactional persistence for fitness onboarding."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from db.models import FitnessProfile, User, UserAccess, dbSession
from services.access import as_utc_naive, utc_now


MIN_AGE = 18
MAX_AGE = 120
MIN_HEIGHT_CM = 100
MAX_HEIGHT_CM = 250
MIN_WEIGHT_KG = 30.0
MAX_WEIGHT_KG = 500.0
WORKOUT_FREQUENCY_OPTIONS = (2, 3, 4, 5, 6)
SESSION_DURATION_OPTIONS = (30, 45, 60, 90)
MAX_LIMITATIONS_LENGTH = 500
TRIAL_DURATION = timedelta(days=3)
NO_LIMITATIONS_VALUES = {
    "нет",
    "нету",
    "нет ограничений",
    "отсутствуют",
    "no",
    "none",
    "-",
}

SEX_LABELS = {
    "male": "Мужской",
    "female": "Женский",
    "not_specified": "Не указывать",
}
GOAL_LABELS = {
    "muscle_gain": "Набрать мышечную массу",
    "strength": "Стать сильнее",
    "fat_loss": "Снизить процент жира",
}
GOAL_DESCRIPTIONS = {
    "muscle_gain": "мышцы, рост рабочих весов и массы тела",
    "strength": "силовые тренировки с упором на присед, жим и тягу",
    "fat_loss": "сохранить мышцы и силу, снижая жировую массу",
}
EXPERIENCE_LABELS = {
    "beginner": "Новичок",
    "intermediate": "Средний",
    "advanced": "Продвинутый",
}
TRAINING_ENVIRONMENT_LABELS = {
    "gym": "Тренажёрный зал",
    "functional_gym": "Функциональный зал",
    "street": "Улица",
    "home": "Дом",
}


class OnboardingValidationError(ValueError):
    """A user-facing onboarding value is invalid."""


class OnboardingPersistenceError(RuntimeError):
    """Profile and access records are in an inconsistent state."""


@dataclass(frozen=True)
class OnboardingData:
    age: int
    sex: str
    height_cm: int
    weight_kg: float
    goal: str
    experience_level: str
    training_environment: str
    workouts_per_week: int
    session_duration_minutes: int
    limitations: str | None


@dataclass(frozen=True)
class OnboardingResult:
    profile: FitnessProfile
    access: UserAccess
    created: bool


def _parse_integer(value: str, minimum: int, maximum: int, message: str) -> int:
    try:
        parsed = int(value.strip())
    except (AttributeError, TypeError, ValueError) as error:
        raise OnboardingValidationError(message) from error
    if not minimum <= parsed <= maximum:
        raise OnboardingValidationError(message)
    return parsed


def parse_age(value: str) -> int:
    return _parse_integer(
        value,
        MIN_AGE,
        MAX_AGE,
        f"Введите возраст целым числом от {MIN_AGE} до {MAX_AGE}.",
    )


def parse_height_cm(value: str) -> int:
    return _parse_integer(
        value,
        MIN_HEIGHT_CM,
        MAX_HEIGHT_CM,
        f"Введите рост в сантиметрах от {MIN_HEIGHT_CM} до {MAX_HEIGHT_CM}.",
    )


def parse_weight_kg(value: str) -> float:
    try:
        parsed = float(value.strip().replace(",", "."))
    except (AttributeError, TypeError, ValueError) as error:
        raise OnboardingValidationError(
            f"Введите вес в килограммах от {MIN_WEIGHT_KG:g} до {MAX_WEIGHT_KG:g}."
        ) from error
    if not math.isfinite(parsed) or not MIN_WEIGHT_KG <= parsed <= MAX_WEIGHT_KG:
        raise OnboardingValidationError(
            f"Введите вес в килограммах от {MIN_WEIGHT_KG:g} до {MAX_WEIGHT_KG:g}."
        )
    return parsed


def parse_workouts_per_week(value: str) -> int:
    try:
        parsed = int(value.strip())
    except (AttributeError, TypeError, ValueError) as error:
        raise OnboardingValidationError(
            "Выберите количество тренировок с помощью кнопок."
        ) from error
    if parsed not in WORKOUT_FREQUENCY_OPTIONS:
        raise OnboardingValidationError(
            "Выберите количество тренировок с помощью кнопок."
        )
    return parsed


def parse_session_duration_minutes(value: str) -> int:
    try:
        parsed = int(value.strip())
    except (AttributeError, TypeError, ValueError) as error:
        raise OnboardingValidationError(
            "Выберите длительность тренировки с помощью кнопок."
        ) from error
    if parsed not in SESSION_DURATION_OPTIONS:
        raise OnboardingValidationError(
            "Выберите длительность тренировки с помощью кнопок."
        )
    return parsed


def validate_choice(value: str, choices: dict[str, str], field_name: str) -> str:
    if value not in choices:
        raise OnboardingValidationError(f"Выберите {field_name} с помощью кнопок.")
    return value


def validate_onboarding_data(data: OnboardingData) -> OnboardingData:
    """Validate the complete domain object before any profile write."""
    validate_choice(data.sex, SEX_LABELS, "вариант")
    validate_choice(data.goal, GOAL_LABELS, "цель")
    validate_choice(data.experience_level, EXPERIENCE_LABELS, "опыт")
    validate_choice(
        data.training_environment,
        TRAINING_ENVIRONMENT_LABELS,
        "место тренировки",
    )
    if data.workouts_per_week not in WORKOUT_FREQUENCY_OPTIONS:
        raise OnboardingValidationError(
            "Выберите количество тренировок с помощью кнопок."
        )
    if data.session_duration_minutes not in SESSION_DURATION_OPTIONS:
        raise OnboardingValidationError(
            "Выберите длительность тренировки с помощью кнопок."
        )
    if not MIN_AGE <= data.age <= MAX_AGE:
        raise OnboardingValidationError("Возраст вне допустимого диапазона.")
    if not MIN_HEIGHT_CM <= data.height_cm <= MAX_HEIGHT_CM:
        raise OnboardingValidationError("Рост вне допустимого диапазона.")
    if not math.isfinite(data.weight_kg) or not MIN_WEIGHT_KG <= data.weight_kg <= MAX_WEIGHT_KG:
        raise OnboardingValidationError("Вес вне допустимого диапазона.")
    if data.limitations is not None and len(data.limitations) > MAX_LIMITATIONS_LENGTH:
        raise OnboardingValidationError("Описание ограничений слишком длинное.")
    return data


def normalize_limitations(value: str) -> str | None:
    normalized = value.strip()
    if normalized.casefold() in NO_LIMITATIONS_VALUES:
        return None
    if not normalized:
        raise OnboardingValidationError(
            "Кратко опишите ограничения или напишите «нет»."
        )
    if len(normalized) > MAX_LIMITATIONS_LENGTH:
        raise OnboardingValidationError(
            f"Сократите описание до {MAX_LIMITATIONS_LENGTH} символов."
        )
    return normalized


def get_fitness_profile(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> FitnessProfile | None:
    with session_factory() as session:
        return session.get(FitnessProfile, user_id)


def has_completed_profile(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> bool:
    profile = get_fitness_profile(user_id, session_factory)
    return profile is not None and profile.completed_at is not None


def update_existing_profile(
    user_id: int,
    data: OnboardingData,
    updated_at: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> FitnessProfile:
    """Update an existing profile without changing the user's access record."""
    data = validate_onboarding_data(data)
    changed_at = (
        as_utc_naive(updated_at) if updated_at is not None else utc_now()
    )
    with session_factory() as session:
        with session.begin():
            user = session.get(User, user_id)
            profile = session.get(FitnessProfile, user_id)
            access = session.get(UserAccess, user_id)
            if user is None:
                raise OnboardingPersistenceError("User does not exist.")
            if profile is None or access is None:
                raise OnboardingPersistenceError(
                    "Profile and access must both exist before editing."
                )

            profile.age = data.age
            profile.sex = data.sex
            profile.height_cm = data.height_cm
            profile.weight_kg = data.weight_kg
            profile.goal = data.goal
            profile.experience_level = data.experience_level
            profile.training_environment = data.training_environment
            profile.workouts_per_week = data.workouts_per_week
            profile.session_duration_minutes = data.session_duration_minutes
            profile.limitations = data.limitations
            profile.updated_at = changed_at
            session.flush()

        result = profile

    # Profile confirmation and generated-plan activation are one product action.
    # A user-defined program remains authoritative; a missing plan is still
    # chosen explicitly from the plan-source screen.
    from services.workout_plans import activate_generated_plan_for_profile

    activate_generated_plan_for_profile(
        user_id,
        session_factory,
        create_if_missing=False,
    )
    return result


def save_profile_and_access(
    user_id: int,
    data: OnboardingData,
    confirmed_at: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> OnboardingResult:
    """Create profile and trial-eligible access without starting the trial."""
    data = validate_onboarding_data(data)
    confirmed_at = (
        as_utc_naive(confirmed_at) if confirmed_at is not None else utc_now()
    )

    with session_factory() as session:
        with session.begin():
            user = session.get(User, user_id)
            if user is None:
                raise OnboardingPersistenceError("User does not exist.")

            existing_profile = session.get(FitnessProfile, user_id)
            existing_access = session.get(UserAccess, user_id)
            if existing_profile is not None or existing_access is not None:
                if existing_profile is not None and existing_access is not None:
                    return OnboardingResult(
                        existing_profile,
                        existing_access,
                        created=False,
                    )
                raise OnboardingPersistenceError(
                    "Profile and access must either both exist or both be absent."
                )

            profile = FitnessProfile(
                user_id=user_id,
                age=data.age,
                sex=data.sex,
                height_cm=data.height_cm,
                weight_kg=data.weight_kg,
                goal=data.goal,
                experience_level=data.experience_level,
                training_environment=data.training_environment,
                workouts_per_week=data.workouts_per_week,
                session_duration_minutes=data.session_duration_minutes,
                limitations=data.limitations,
                completed_at=confirmed_at,
                updated_at=confirmed_at,
            )
            access = UserAccess(
                user_id=user_id,
                trial_started_at=None,
                trial_ends_at=None,
                subscription_started_at=None,
                subscription_ends_at=None,
                updated_at=confirmed_at,
            )
            session.add_all((profile, access))
            session.flush()

        return OnboardingResult(profile, access, created=True)


save_profile_and_trial = save_profile_and_access
