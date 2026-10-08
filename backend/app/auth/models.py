import enum

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class VerificationPurpose(str, enum.Enum):
    register = "register"
    login = "login"
    reset_password = "reset_password"


class CurrentUserOut(BaseModel):
    id: str
    email: EmailStr
    role: UserRole
    is_banned: bool = Field(default=False)
    daily_generation_remaining: int = 10
    daily_generation_reset_at: str | None = None
    terms_version: str | None = None
    terms_accepted_at: str | None = None
    generation_quality_improvement_consent: bool = False
    generation_quality_improvement_consent_updated_at: str | None = None


class CurrentTermsVersionOut(BaseModel):
    version: str
