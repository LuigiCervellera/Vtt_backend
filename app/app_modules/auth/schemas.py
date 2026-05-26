from dataclasses import dataclass

@dataclass
class AuthRequest:
    username: str
    password: str

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
