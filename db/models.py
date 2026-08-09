from datetime import datetime
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class SqliteSession:
    def __init__(self, database_url: str = "sqlite:///db.db"):
        self._engine = create_engine(database_url)
        event.listen(self._engine, "connect", self._enable_foreign_keys)
        self._session = sessionmaker(bind=self._engine, expire_on_commit=False)

    @staticmethod
    def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @property
    def engine(self):
        return self._engine
    
    def __call__(self):
        return self._session()

    def __getattr__(self, name):
        return getattr(self._session, name)
    
    def create_all(self) -> None:
        """Create mapped tables synchronously; migrations remain authoritative."""
        Base.metadata.create_all(self._engine)

    def migrate(self) -> tuple[int, ...]:
        from db.migrations import run_migrations

        return run_migrations(self._engine)

    def dispose(self) -> None:
        self._engine.dispose()

dbSession = SqliteSession()


from sqlalchemy import exc
from sqlalchemy.schema import ForeignKey
from sqlalchemy.types import Text, BigInteger 
from sqlalchemy.sql import select, insert, update as sqlalchemy_update
from sqlalchemy.sql.functions import func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.strategy_options import load_only


class ModelAdmin:
    @classmethod
    def create(cls, **kwargs) -> int:
        """
        # Создаем новый объект
        :param kwargs: Поля и значения для объекта
        :return: Идентификатор PK
        """

        with dbSession() as session:
            res = session.execute(insert(cls).values(**kwargs))
            session.commit()
            return res.lastrowid

    @classmethod
    def add(cls, **kwargs) -> None:
        """
        # Добавляем новый объект
        :param kwargs: Поля и значения для объекта
        """

        with dbSession() as session:
            session.add(cls(**kwargs))
            session.commit()

    def update(self, **kwargs) -> None:
        """
        # Обновляем текущий объект
        :param kwargs: поля и значения, которые надо поменять
        """

        with dbSession() as session:
            session.execute(
                sqlalchemy_update(self.__class__), [{"id": self.id, **kwargs}]
            )
            session.commit()

    def delete(self) -> None:
        """
        # Удаляем объект
        """
        with dbSession() as session:
            session.delete(self)
            session.commit()

    @classmethod
    def get(cls, **kwargs):
        """
        # Возвращаем одну запись, которая удовлетворяет введенным параметрам
        :param kwargs: поля и значения
        :return: Объект или None
        """

        params = [getattr(cls, key) == val for key, val in kwargs.items()]
        query = select(cls).where(*params)
        try:
            with dbSession() as session:
                results = session.execute(query)
                (result,) = results.one()
                result: cls
                return result
        except exc.NoResultFound:
            return None

    @classmethod
    def filter(cls, **kwargs):
        """
        # Возвращаем все записи, которые удовлетворяют фильтру
        :param kwargs: поля и значения
        :return: ScalarResult, если нашли записи и пустой tuple, если нет
        """

        params = [getattr(cls, key) == val for key, val in kwargs.items()]
        query = select(cls).where(*params)
        try:
            with dbSession() as session:
                results = session.execute(query)
                return results.scalars().all()
        except exc.NoResultFound:
            return ()

    @classmethod
    def all(cls, values=None):
        """
        # Получаем все записи
        :param values: Список полей, которые надо вернуть, если нет, то все (default None)
        :return: Список Class(object)
        """

        if values and isinstance(values, list):
            # Определенные поля
            values = [getattr(cls, val) for val in values if isinstance(val, str)]
            query = select(cls).options(load_only(*values))
        else:
            # Все поля
            query = select(cls)

        with dbSession() as session:
            result = session.execute(query)
            return result.scalars().all()


class User(Base, ModelAdmin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger(), unique=True)
    fullname: Mapped[str] = mapped_column(Text())
    username: Mapped[str] = mapped_column(Text())
    inviter_id: Mapped[int] = mapped_column(BigInteger())
    reg_datetime: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=True
    )

    @classmethod
    def get_or_create(cls, tg_id: int, fullname: str = None,
                      username: str = None, inviter_id: int = None) -> "User":
        user: User = User.get(tg_id=tg_id)
        if user is None:
            user: User = User.get(id=User.create(tg_id=tg_id, fullname=fullname,
                                                 username=username, inviter_id=inviter_id))
        return user


class Payments(Base, ModelAdmin):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user: Mapped[int] = mapped_column(ForeignKey("users.id"))
    yoo_id: Mapped[str] = mapped_column(Text())
    link: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(Text())
    reg_datetime: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=True
    )


class FitnessProfile(Base):
    __tablename__ = "fitness_profiles"
    __table_args__ = (
        CheckConstraint(
            "goal IN ('muscle_gain', 'fat_loss')",
            name="ck_fitness_profiles_goal",
        ),
        CheckConstraint(
            "experience_level IN ('beginner', 'some_experience')",
            name="ck_fitness_profiles_experience_level",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    age: Mapped[int] = mapped_column(Integer())
    sex: Mapped[str] = mapped_column(Text())
    height_cm: Mapped[int] = mapped_column(Integer())
    weight_kg: Mapped[float] = mapped_column(Float())
    goal: Mapped[str] = mapped_column(Text())
    experience_level: Mapped[str] = mapped_column(Text())
    workouts_per_week: Mapped[int] = mapped_column(Integer())
    session_duration_minutes: Mapped[int] = mapped_column(Integer())
    limitations: Mapped[str | None] = mapped_column(Text(), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now()
    )


class UserAccess(Base):
    __tablename__ = "user_access"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    trial_started_at: Mapped[datetime] = mapped_column(DateTime())
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime())
    subscription_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )
    subscription_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now()
    )


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text(), unique=True)
    name: Mapped[str] = mapped_column(Text())
    muscle_group: Mapped[str] = mapped_column(Text())
    equipment: Mapped[str] = mapped_column(Text())
    hint: Mapped[str] = mapped_column(Text())
    restriction_tags: Mapped[str] = mapped_column(Text(), server_default="")


class WorkoutTemplate(Base):
    __tablename__ = "workout_templates"
    __table_args__ = (
        CheckConstraint(
            "goal IN ('muscle_gain', 'fat_loss')",
            name="ck_workout_templates_goal",
        ),
        CheckConstraint(
            "experience_level IN ('beginner', 'some_experience')",
            name="ck_workout_templates_experience_level",
        ),
        CheckConstraint(
            "workouts_per_week BETWEEN 1 AND 4",
            name="ck_workout_templates_workouts_per_week",
        ),
        CheckConstraint(
            "duration_bucket IN ('short', 'standard')",
            name="ck_workout_templates_duration_bucket",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text(), unique=True)
    name: Mapped[str] = mapped_column(Text())
    goal: Mapped[str] = mapped_column(Text())
    experience_level: Mapped[str] = mapped_column(Text())
    workouts_per_week: Mapped[int] = mapped_column(Integer())
    duration_bucket: Mapped[str] = mapped_column(Text())
    equipment: Mapped[str] = mapped_column(Text())


class WorkoutTemplateDay(Base):
    __tablename__ = "workout_template_days"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "day_number",
            name="uq_workout_template_days_order",
        ),
        CheckConstraint(
            "day_number >= 1",
            name="ck_workout_template_days_day_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("workout_templates.id", ondelete="CASCADE")
    )
    day_number: Mapped[int] = mapped_column(Integer())
    title: Mapped[str] = mapped_column(Text())


class WorkoutTemplateExercise(Base):
    __tablename__ = "workout_template_exercises"
    __table_args__ = (
        UniqueConstraint(
            "template_day_id",
            "exercise_order",
            name="uq_workout_template_exercises_order",
        ),
        CheckConstraint(
            "exercise_order >= 1",
            name="ck_workout_template_exercises_order",
        ),
        CheckConstraint("sets >= 1", name="ck_workout_template_exercises_sets"),
        CheckConstraint(
            "reps_min >= 1 AND reps_max >= reps_min",
            name="ck_workout_template_exercises_reps",
        ),
        CheckConstraint(
            "rest_seconds >= 0",
            name="ck_workout_template_exercises_rest",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_day_id: Mapped[int] = mapped_column(
        ForeignKey("workout_template_days.id", ondelete="CASCADE")
    )
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT")
    )
    exercise_order: Mapped[int] = mapped_column(Integer())
    sets: Mapped[int] = mapped_column(Integer())
    reps_min: Mapped[int] = mapped_column(Integer())
    reps_max: Mapped[int] = mapped_column(Integer())
    rest_seconds: Mapped[int] = mapped_column(Integer())


class UserWorkoutPlan(Base):
    __tablename__ = "user_workout_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("workout_templates.id", ondelete="RESTRICT")
    )
    profile_signature: Mapped[str] = mapped_column(Text())
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now()
    )


class UserWorkoutPlanDay(Base):
    __tablename__ = "user_workout_plan_days"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "day_number",
            name="uq_user_workout_plan_days_order",
        ),
        CheckConstraint(
            "day_number >= 1",
            name="ck_user_workout_plan_days_day_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("user_workout_plans.id", ondelete="CASCADE")
    )
    day_number: Mapped[int] = mapped_column(Integer())
    title: Mapped[str] = mapped_column(Text())


class UserWorkoutPlanExercise(Base):
    __tablename__ = "user_workout_plan_exercises"
    __table_args__ = (
        UniqueConstraint(
            "plan_day_id",
            "exercise_order",
            name="uq_user_workout_plan_exercises_order",
        ),
        CheckConstraint(
            "exercise_order >= 1",
            name="ck_user_workout_plan_exercises_order",
        ),
        CheckConstraint("sets >= 1", name="ck_user_workout_plan_exercises_sets"),
        CheckConstraint(
            "reps_min >= 1 AND reps_max >= reps_min",
            name="ck_user_workout_plan_exercises_reps",
        ),
        CheckConstraint(
            "rest_seconds >= 0",
            name="ck_user_workout_plan_exercises_rest",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_day_id: Mapped[int] = mapped_column(
        ForeignKey("user_workout_plan_days.id", ondelete="CASCADE")
    )
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT")
    )
    exercise_order: Mapped[int] = mapped_column(Integer())
    exercise_name: Mapped[str] = mapped_column(Text())
    sets: Mapped[int] = mapped_column(Integer())
    reps_min: Mapped[int] = mapped_column(Integer())
    reps_max: Mapped[int] = mapped_column(Integer())
    rest_seconds: Mapped[int] = mapped_column(Integer())
    hint: Mapped[str] = mapped_column(Text())
