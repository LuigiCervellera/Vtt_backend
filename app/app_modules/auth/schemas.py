from dataclasses import dataclass
from typing import Optional

@dataclass
class AuthRequest:
    username: str
    password: str

@dataclass
class RegisterRequest:
    username: str
    password: str
    email: Optional[str] = None

@dataclass
class VerifyEmailRequest:
    token: str

@dataclass
class ResendVerificationRequest:
    email: str

@dataclass
class UpdateUsernameRequest:
    username: str
    current_password: str
    new_username: str

@dataclass
class UpdatePasswordRequest:
    username: str
    current_password: str
    new_password: str
