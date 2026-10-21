import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'data/zhigou.db').as_posix()}"))
    secret_key: str = field(default_factory=lambda: os.getenv("JWT_SECRET_KEY", ""))
    access_token_minutes: int = field(default_factory=lambda: int(os.getenv("ACCESS_TOKEN_MINUTES", "120")))
    upload_dir: Path = field(default_factory=lambda: Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "storage"))).resolve())
    max_upload_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024)
    cors_origins: list[str] = field(default_factory=lambda: [s.strip() for s in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if s.strip()])

    def prepare(self):
        if self.access_token_minutes < 1 or self.max_upload_bytes < 1:
            raise ValueError("Token期限和上传限制必须为正数")
        if not self.secret_key:
            if self.app_env == "production":
                raise ValueError("生产环境必须设置 JWT_SECRET_KEY")
            secret_path = BASE_DIR / ".dev-jwt-secret"
            try:
                with secret_path.open("x", encoding="utf-8") as f:
                    f.write(secrets.token_urlsafe(48))
            except FileExistsError:
                pass
            self.secret_key = secret_path.read_text(encoding="utf-8").strip()
        if len(self.secret_key) < 32:
            raise ValueError("JWT_SECRET_KEY至少需要32个字符")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
