"""
@author: Ken Mwaura
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()


db = os.getenv(
    "DB", "postgresql+psycopg2://test:testpassword@localhost:5432/xp_db"
)
# Note: no eager engine.connect() here. Connecting at import time made every
# module that imports this one (models, scrapers, tests) fail whenever the
# database was unreachable; connections are now opened lazily on first use.
engine = create_engine(db)
Session = sessionmaker(bind=engine)

Base = declarative_base()
