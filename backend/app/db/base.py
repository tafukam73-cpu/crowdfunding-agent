"""SQLAlchemy の宣言的ベースクラス。全モデルがこれを継承する。"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
