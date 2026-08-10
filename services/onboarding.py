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
MIN_WORKOUTS_PER_WEEK = 1
MAX_WORKOUTS_PER_WEEK = 7
MIN_SESSION_DURATION_MINUTES = 10
MAX_SESSION_DURATION_MINUTES = 300
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
    "muscle_gain": "Набор мышечной массы / веса",
    "fat_loss": "Снижение веса / жира",
}
EXPERIENCE_LABELS = {
    "beginner": "Новичок",
    "some_experience": "Есть небольшой опыт",
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
    return _parse_integer(
        value,
        MIN_WORKOUTS_PER_WEEK,
        MAX_WORKOUTS_PER_WEEK,
        "Введите количество тренировок в неделю от 1 до 7.",
    )


def parse_session_duration_minutes(value: str) -> int:
    return _parse_integer(
        value,
        MIN_SESSION_DURATION_MINUTES,
        MAX_SESSION_DURATION_MINUTES,
        "Введите длительность тренировки в минутах от 10 до 300.",
    )


def validate_choice(value: str, choices: dict[str, str], field_name: str) -> str:
    if value not in choices:
        raise OnboardingValidationError(f"Выберите {field_name} с помощью кнопок.")
    return value


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
            profile.workouts_per_week = data.workouts_per_week
            profile.session_duration_minutes = data.session_duration_minutes
            profile.limitations = data.limitations
            profile.updated_at = changed_at
            session.flush()

        return profile


def save_profile_and_trial(
    user_id: int,
    data: OnboardingData,
    confirmed_at: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> OnboardingResult:
    """Create profile and three-day trial atomically after confirmation."""
    started_at = (
        as_utc_naive(confirmed_at) if confirmed_at is not None else utc_now()
    )
    trial_ends_at = started_at + TRIAL_DURATION

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
                workouts_per_week=data.workouts_per_week,
                session_duration_minutes=data.session_duration_minutes,
                limitations=data.limitations,
                completed_at=started_at,
                updated_at=started_at,
            )
            access = UserAccess(
                user_id=user_id,
                trial_started_at=started_at,
                trial_ends_at=trial_ends_at,
                subscription_started_at=None,
                subscription_ends_at=None,
                updated_at=started_at,
            )
            session.add_all((profile, access))
            session.flush()

        return OnboardingResult(profile, access, created=True)
