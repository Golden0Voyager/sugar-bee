import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# 将项目根目录加入路径，以便导入模型
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 导入模型元数据
from models import Base

target_metadata = Base.metadata

# 从环境变量读取数据库 URL（支持 SQLite / PostgreSQL）
def get_database_url():
    url = os.environ.get('SUGAR_BEE_DATABASE_URL')
    if not url:
        # 默认回退到项目 SQLite 路径
        from core.config import DB_NAME
        url = f"sqlite:///{DB_NAME}"
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    url = get_database_url()

    # SQLite 不需要连接池
    if url.startswith('sqlite'):
        connectable = create_engine(url, poolclass=pool.NullPool)
    else:
        connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
