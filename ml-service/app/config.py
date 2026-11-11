import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://detektor:detektor@localhost:5432/detektor")
MODEL_DIR = os.getenv("MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "models"))

# Model dianggap "siap" bila sudah melihat minimal sekian sampel per kelas
MIN_SAMPLES_PER_CLASS = _int("MIN_SAMPLES_PER_CLASS", 5)
# Latih ulang penuh otomatis setiap N sampel baru dipelajari (0 = nonaktif)
AUTO_RETRAIN_EVERY = _int("AUTO_RETRAIN_EVERY", 50)
# Jumlah sampel lama per kelas yang diputar ulang saat belajar incremental
REPLAY_PER_CLASS = _int("REPLAY_PER_CLASS", 16)
MAX_TEXT_CHARS = _int("MAX_TEXT_CHARS", 200_000)
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 15)

os.makedirs(MODEL_DIR, exist_ok=True)
