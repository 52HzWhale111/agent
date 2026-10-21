from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


def issue_token(user_id: str, settings) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes), "iss": "zhigou-backend", "aud": "zhigou"}, settings.secret_key, algorithm="HS256")


def read_token(token: str, settings) -> str:
    claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"], issuer="zhigou-backend", audience="zhigou", options={"require": ["sub", "exp", "iat"]})
    return claims["sub"]
