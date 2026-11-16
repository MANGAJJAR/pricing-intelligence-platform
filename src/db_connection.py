# db_connection.py

import os
import dotenv
from sqlalchemy import create_engine
from trino.auth import BasicAuthentication
from tenacity import retry, stop_after_attempt, wait_exponential
import logging

dotenv.load_dotenv("config/.env")
logger = logging.getLogger("db_connection")

class TrinoConnection:
    def __init__(self):
        self.trino_host = os.environ["trino_host"]
        self.trino_port = os.environ["trino_port"]
        self.trino_catalog = os.environ["trino_catalog"]
        self.trino_user = os.environ["trino_user"]
        self.trino_password = os.environ["trino_password"]

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
    def get_engine(self, schema):
        logger.info(f"db engine creation started")
        return create_engine(
            f"trino://{self.trino_user}:{self.trino_password}@{self.trino_host}:{self.trino_port}/{self.trino_catalog}/{schema}",
            connect_args={
                "auth": BasicAuthentication(self.trino_user, self.trino_password),
                "http_scheme": "https",
                "verify": False
            }
        )