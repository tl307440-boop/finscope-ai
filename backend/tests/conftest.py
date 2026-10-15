import os

os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-not-for-production-32ch"
os.environ["DATA_MODE"] = "live"
os.environ["LOG_LEVEL"] = "WARNING"
