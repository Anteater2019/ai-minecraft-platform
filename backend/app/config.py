import os


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-coder")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
