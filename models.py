"""
Sugar Bee - SQLAlchemy 模型定义

本文件用于 Alembic 迁移管理。
当前应用的数据库操作仍使用原生 SQL（SQLite），
未来可逐步迁移到 SQLAlchemy ORM。
"""

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, MetaData, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

# 使用命名约定，方便 Alembic 自动生成迁移
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=convention))


class Record(Base):
    """健康记录表（血糖、血压、运动、饮食、体重等）"""
    __tablename__ = 'records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    value = Column(Float)
    unit = Column(Text)
    type = Column(Text)
    notes = Column(Text)
    timestamp = Column(DateTime)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # 迁移列
    calories = Column(Integer, default=0)
    diet_analysis = Column(Text, default='')
    is_predicted = Column(Boolean, default=False)
    distance = Column(Float)
    duration = Column(Text)
    heart_rate = Column(Integer)
    pace = Column(Text)
    cadence = Column(Integer)
    systolic_pressure = Column(Integer)
    diastolic_pressure = Column(Integer)
    pulse_rate = Column(Integer)
    spo2 = Column(Integer)
    weight = Column(Float)
    bmi = Column(Float)
    verified_by_real_id = Column(Integer)
    prediction_error = Column(Float)
    carbs_grams = Column(Float)
    gi_value = Column(Float)
    medication_name = Column(Text)
    vo2max = Column(Float)
    max_heart_rate = Column(Integer)
    steps = Column(Integer)
    max_pace = Column(Text)
    user_id = Column(Integer, default=1)
    external_id = Column(Text)
    source = Column(Text)

    __table_args__ = (
        Index('idx_records_user_ts', 'user_id', 'timestamp', unique=False),
        Index('idx_records_user_pred', 'user_id', 'is_predicted', 'timestamp', unique=False),
        Index('idx_records_weight', 'user_id', 'weight', 'timestamp', unique=False),
        Index('idx_records_bp', 'user_id', 'systolic_pressure', 'timestamp', unique=False),
    )


class MedicationPlan(Base):
    """用药方案表"""
    __tablename__ = 'medication_plans'

    id = Column(Integer, primary_key=True, autoincrement=True)
    medication_name = Column(Text, nullable=False)
    dosage = Column(Text)
    times_per_day = Column(Integer, default=1)
    timing_notes = Column(Text)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)
    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    user_id = Column(Integer, default=1)

    # 迁移列
    frequency = Column(Text, default='daily')
    frequency_detail = Column(Text)
    category = Column(Text, default='long_term')
    dose_quantity = Column(Text, default='1')
    dose_unit = Column(Text, default='片')
    med_type = Column(Text, default='')


class DosageHistory(Base):
    """剂量调整历史表"""
    __tablename__ = 'dosage_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey('medication_plans.id'), nullable=False)
    old_dosage = Column(Text)
    new_dosage = Column(Text)
    changed_at = Column(DateTime, server_default=func.current_timestamp())


class MedicationLog(Base):
    """用药记录表"""
    __tablename__ = 'medication_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey('medication_plans.id'), nullable=False)
    log_date = Column(Date, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    taken = Column(Boolean, default=True)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    user_id = Column(Integer, default=1)

    __table_args__ = (
        Index('idx_medlogs_plan', 'plan_id', 'log_date', unique=False),
    )


class HealthAnalysis(Base):
    """健康分析报告表"""
    __tablename__ = 'health_analyses'

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_date = Column(Date, nullable=False)
    health_score = Column(Integer)
    glucose_summary = Column(Text)
    blood_pressure_summary = Column(Text)
    exercise_summary = Column(Text)
    medication_summary = Column(Text)
    recommendations = Column(Text)
    full_analysis = Column(Text)
    is_auto_generated = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    user_id = Column(Integer, default=1)
    days = Column(Integer, default=7)

    __table_args__ = (
        Index('idx_analyses_user', 'user_id', 'created_at', unique=False),
    )


class AppUser(Base):
    """用户表"""
    __tablename__ = 'app_users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False, unique=True)
    display_name = Column(Text, nullable=False)
    avatar = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    # 迁移列
    password_hash = Column(Text)
    phone = Column(Text)
    email = Column(Text)


class UserProfile(Base):
    """用户档案表"""
    __tablename__ = 'user_profiles'

    user_id = Column(Integer, ForeignKey('app_users.id'), primary_key=True)
    name = Column(Text)
    birth_year = Column(Integer)
    height = Column(Integer)
    weight = Column(Integer)
    gender = Column(Text)
    default_meals = Column(Text)
    target_ranges = Column(Text)
    enabled_modules = Column(Text)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp())

    # 迁移列
    target_weight = Column(Float)
    birth_month = Column(Integer)
    birth_day = Column(Integer)


class ChatMessage(Base):
    """聊天消息表"""
    __tablename__ = 'chat_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    session_id = Column(Text, nullable=False)
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        Index('idx_chat_user_session', 'user_id', 'session_id', 'created_at', unique=False),
    )


class UserAuthProvider(Base):
    """认证提供商绑定表"""
    __tablename__ = 'user_auth_providers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('app_users.id'), nullable=False)
    provider = Column(Text, nullable=False)
    provider_uid = Column(Text, nullable=False)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        Index('idx_auth_provider_uid', 'provider', 'provider_uid', unique=True),
    )
