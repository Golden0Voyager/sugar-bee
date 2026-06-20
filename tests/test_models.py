"""
SQLAlchemy 模型定义测试
"""
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from models import (
    AppUser,
    Base,
    ChatMessage,
    DosageHistory,
    HealthAnalysis,
    MedicationLog,
    MedicationPlan,
    Record,
    UserAuthProvider,
    UserProfile,
)


@pytest.fixture
def engine():
    """创建内存数据库引擎"""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session(engine):
    """创建数据库会话"""
    with Session(engine) as session:
        yield session


class TestRecordModel:
    """Record 模型测试"""

    def test_table_name(self):
        assert Record.__tablename__ == 'records'

    def test_create_record_minimal(self, session):
        record = Record(value=5.6, type='空腹')
        session.add(record)
        session.commit()
        assert record.id is not None
        assert record.value == 5.6

    def test_record_defaults(self, session):
        record = Record()
        session.add(record)
        session.commit()
        assert record.calories == 0
        assert record.diet_analysis == ''
        assert record.is_predicted is False
        assert record.user_id == 1

    def test_record_all_fields(self, session):
        record = Record(
            value=7.2, unit='mmol/L', type='餐后2小时',
            notes='午餐后', calories=500, carbs_grams=75.0,
            gi_value=65.0, is_predicted=True,
            systolic_pressure=120, diastolic_pressure=80,
            pulse_rate=72, weight=75.0, bmi=24.5,
            medication_name='二甲双胍', user_id=1
        )
        session.add(record)
        session.commit()
        assert record.carbs_grams == 75.0
        assert record.systolic_pressure == 120
        assert record.medication_name == '二甲双胍'

    def test_record_indexes(self, engine):
        inspector = inspect(engine)
        indexes = inspector.get_indexes('records')
        index_names = [idx['name'] for idx in indexes]
        assert 'idx_records_user_ts' in index_names
        assert 'idx_records_user_pred' in index_names


class TestMedicationPlanModel:
    """MedicationPlan 模型测试"""

    def test_table_name(self):
        assert MedicationPlan.__tablename__ == 'medication_plans'

    def test_create_plan(self, session):
        import datetime
        plan = MedicationPlan(
            medication_name='二甲双胍',
            dosage='500mg',
            times_per_day=2,
            start_date=datetime.date.today()
        )
        session.add(plan)
        session.commit()
        assert plan.id is not None
        assert plan.frequency == 'daily'
        assert plan.category == 'long_term'
        assert plan.dose_quantity == '1'
        assert plan.dose_unit == '片'


class TestDosageHistoryModel:
    """DosageHistory 模型测试"""

    def test_table_name(self):
        assert DosageHistory.__tablename__ == 'dosage_history'


class TestMedicationLogModel:
    """MedicationLog 模型测试"""

    def test_table_name(self):
        assert MedicationLog.__tablename__ == 'medication_logs'

    def test_indexes(self, engine):
        inspector = inspect(engine)
        indexes = inspector.get_indexes('medication_logs')
        index_names = [idx['name'] for idx in indexes]
        assert 'idx_medlogs_plan' in index_names


class TestHealthAnalysisModel:
    """HealthAnalysis 模型测试"""

    def test_table_name(self):
        assert HealthAnalysis.__tablename__ == 'health_analyses'

    def test_defaults(self, session):
        import datetime
        analysis = HealthAnalysis(analysis_date=datetime.date(2024, 1, 1))
        session.add(analysis)
        session.commit()
        assert analysis.is_auto_generated is False
        assert analysis.days == 7

    def test_indexes(self, engine):
        inspector = inspect(engine)
        indexes = inspector.get_indexes('health_analyses')
        index_names = [idx['name'] for idx in indexes]
        assert 'idx_analyses_user' in index_names


class TestAppUserModel:
    """AppUser 模型测试"""

    def test_table_name(self):
        assert AppUser.__tablename__ == 'app_users'

    def test_create_user(self, session):
        user = AppUser(username='test_user', display_name='Test User')
        session.add(user)
        session.commit()
        assert user.id is not None
        assert user.is_active is True


class TestUserProfileModel:
    """UserProfile 模型测试"""

    def test_table_name(self):
        assert UserProfile.__tablename__ == 'user_profiles'


class TestChatMessageModel:
    """ChatMessage 模型测试"""

    def test_table_name(self):
        assert ChatMessage.__tablename__ == 'chat_messages'

    def test_create_message(self, session):
        msg = ChatMessage(user_id=1, session_id='abc', role='user', content='Hello')
        session.add(msg)
        session.commit()
        assert msg.id is not None

    def test_indexes(self, engine):
        inspector = inspect(engine)
        indexes = inspector.get_indexes('chat_messages')
        index_names = [idx['name'] for idx in indexes]
        assert 'idx_chat_user_session' in index_names


class TestUserAuthProviderModel:
    """UserAuthProvider 模型测试"""

    def test_table_name(self):
        assert UserAuthProvider.__tablename__ == 'user_auth_providers'

    def test_indexes(self, engine):
        inspector = inspect(engine)
        indexes = inspector.get_indexes('user_auth_providers')
        index_names = [idx['name'] for idx in indexes]
        assert 'idx_auth_provider_uid' in index_names


class TestNamingConvention:
    """测试命名约定"""

    def test_convention_keys(self):
        from models import convention
        assert 'ix' in convention
        assert 'uq' in convention
        assert 'fk' in convention
        assert 'pk' in convention
