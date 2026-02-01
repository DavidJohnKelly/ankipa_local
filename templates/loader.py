from functools import lru_cache
from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parent  

@lru_cache()
def load_template(name: str) -> str:
    p = TEMPLATES_DIR / name
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Template not found: {p}")
        return ""