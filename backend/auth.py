"""
auth.py — JWT authentication module using bcrypt directly (bypassing passlib bugs) and python-jose
"""

import os
import secrets
import logging
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

# Set up logging context
logger = logging.getLogger("auth")

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_hex(32)
    # LOUD WARNING as requested
    logger.warning(
        "\n============================================================"
        "\n⚠️  WARNING: JWT_SECRET IS NOT SET IN ENVIRONMENT!"
        "\nGenerated a transient, random secret for local session use."
        "\nThis is INSECURE beyond local demo / testing runs."
        "\n============================================================"
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 4

# OAuth2 Password Flow scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    """Return the bcrypt hash of a plain text password using bcrypt directly."""
    # Bcrypt requires bytes
    pwd_bytes = password.encode('utf-8')
    # Generate salt and hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plain text password against its bcrypt hash using bcrypt directly."""
    try:
        pwd_bytes = password.encode('utf-8')
        hashed_bytes = hashed.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except Exception as e:
        logger.error(f"Password verification failed: {e}")
        return False


def create_access_token(merchant_id: str, email: str) -> str:
    """Generate a JWT access token expiring in 4 hours."""
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode = {
        "sub": merchant_id,
        "email": email,
        "exp": expire
    }
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_merchant(token: Optional[str] = Depends(oauth2_scheme)) -> dict:
    """
    FastAPI dependency bypassed to always return the default merchant for the demo.
    """
    return {"merchant_id": "merchant_demo_1", "email": "demo1@reclaim.test"}
