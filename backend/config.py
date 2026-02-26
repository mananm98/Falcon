"""
Configuration — reads from environment variables with local dev defaults.
"""

import os

# PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/falcon")
DB_MIN_CONNECTIONS = int(os.getenv("DB_MIN_CONNECTIONS", "2"))
DB_MAX_CONNECTIONS = int(os.getenv("DB_MAX_CONNECTIONS", "10"))

# Ingestion
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(500 * 1024)))  # 500KB
