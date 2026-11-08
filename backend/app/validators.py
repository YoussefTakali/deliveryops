"""Shared input types."""

import re
from typing import Annotated

from pydantic import AfterValidator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 200 or not _EMAIL.match(value):
        raise ValueError("adresse e-mail invalide")
    return value


# Pydantic's EmailStr rejects internal domains (".local", ".lan", ".corp", ".internal") as
# "special-use", yet those are common for company accounts — and the first-boot admin defaults to
# admin@deliveryops.local. Accounts only need a well-formed, unique address here.
Email = Annotated[str, AfterValidator(_email)]
