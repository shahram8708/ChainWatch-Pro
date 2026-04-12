"""Validation utilities shared across the ChainWatch Pro codebase."""

from __future__ import annotations

import os
import re
from typing import Any

from werkzeug.utils import secure_filename


SPECIAL_CHARACTERS = r"!@#$%^&*()_+-=[]{}|;':\",.<>?/"
PASSWORD_SPECIAL_REGEX = re.compile(r"[!@#$%^&*()_+\-=\[\]{}|;':\",.<>?/]", re.IGNORECASE)
PHONE_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")
PORT_CODE_REGEX = re.compile(r"^[A-Za-z0-9]{3,5}$")


def validate_password_strength(password: str) -> dict[str, Any]:
    """Validate password strength and return validity, strength, and errors."""

    errors: list[str] = []
    password = password or ""

    has_min_length = len(password) >= 8
    has_upper = any(char.isupper() for char in password)
    has_lower = any(char.islower() for char in password)
    has_digit = any(char.isdigit() for char in password)
    has_special = bool(PASSWORD_SPECIAL_REGEX.search(password))

    if not has_min_length:
        errors.append("Password must be at least 8 characters long.")
    if not has_upper:
        errors.append("Password must include at least one uppercase letter.")
    if not has_lower:
        errors.append("Password must include at least one lowercase letter.")
    if not has_digit:
        errors.append("Password must include at least one digit.")
    if not has_special:
        errors.append(
            "Password must include at least one special character: "
            f"{SPECIAL_CHARACTERS}"
        )

    score = sum([has_min_length, has_upper, has_lower, has_digit, has_special, len(password) >= 12])
    if score >= 6:
        strength = "strong"
    elif score >= 4:
        strength = "medium"
    else:
        strength = "weak"

    return {
        "valid": len(errors) == 0,
        "strength": strength,
        "errors": errors,
    }


def validate_phone_number(phone: str | None) -> bool:
    """Validate an international phone number in +countrycode format."""

    if not phone:
        return False
    return bool(PHONE_REGEX.fullmatch(phone.strip()))


def validate_port_code(code: str | None) -> bool:
    """Validate a 3 to 5 character alphanumeric port code."""

    if not code:
        return False
    return bool(PORT_CODE_REGEX.fullmatch(code.strip().upper()))


def validate_csv_file(file: Any) -> bool:
    """Validate CSV file extension and max size of 5 MB."""

    if file is None or not getattr(file, "filename", ""):
        return False

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".csv"):
        return False

    stream = getattr(file, "stream", None)
    if stream is None:
        return False

    try:
        current_position = stream.tell()
        stream.seek(0, os.SEEK_END)
        file_size = stream.tell()
        stream.seek(current_position, os.SEEK_SET)
    except OSError:
        return False

    return file_size < 5 * 1024 * 1024


__all__ = [
    "validate_password_strength",
    "validate_phone_number",
    "validate_port_code",
    "validate_csv_file",
    "secure_filename",
]
