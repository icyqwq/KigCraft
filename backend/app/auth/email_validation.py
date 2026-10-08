from pydantic import EmailStr, TypeAdapter, ValidationError


_EMAIL_ADAPTER = TypeAdapter(EmailStr)


def normalize_email_or_exit(email: str) -> str:
    try:
        return str(_EMAIL_ADAPTER.validate_python(email.strip())).lower()
    except ValidationError as exc:
        raise SystemExit("invalid_email") from exc
