"""Independent local profile test data.

Usernames and passwords are suitable identifiers for local form testing.
Email/CPF/phone and full name are unverified TEST DATA, never real identities.
"""
import secrets


def valid_cpf_digits(base: str) -> str:
    """Return an 11-digit checksum-valid synthetic CPF for LOCAL TESTS only."""
    if len(base) != 9 or not base.isdigit() or len(set(base)) == 1:
        raise ValueError("CPF de teste: base inválida.")
    digits = [int(x) for x in base]
    for length in (9, 10):
        remainder = (sum(digits[i] * (length + 1 - i) for i in range(length)) * 10) % 11
        digits.append(0 if remainder == 10 else remainder)
    return "".join(map(str, digits))


def make_password() -> str:
    """Generate a unique nine-character secret for local profiles."""
    chars = (
        [secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(6)]
        + [secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(1)]
        + [secrets.choice("0123456789") for _ in range(1)]
        + [secrets.choice("#!@?") for _ in range(1)]
    )
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def make_profile_data() -> dict:
    """Produce visually neutral usernames, cryptographic passwords and demo-only contact data.

    example.test intentionally never receives messages. No generated personal
    identity is authorized for a third-party/KYC registration.
    """
    prefix = secrets.choice((
        "aurora", "brisa", "vento", "laguna", "coral", "atlas",
        "mirante", "sereno", "litoral", "solare",
    ))
    token = secrets.token_hex(4)
    username = prefix + token
    nine = "".join(str(secrets.randbelow(10)) for _ in range(9))
    while len(set(nine)) == 1:
        nine = "".join(str(secrets.randbelow(10)) for _ in range(9))
    cpf = valid_cpf_digits(nine)
    return {
        "fullName": "Pessoa Teste " + token[:5].upper(),
        "username": username,
        "password": make_password(),
        "email": username + "@example.test",
        "phone": "+55 (11) 90000-" + str(secrets.randbelow(10000)).zfill(4),
        "cpf": cpf[:3] + "." + cpf[3:6] + "." + cpf[6:9] + "-" + cpf[9:],
        "isTestData": True,
        "cpfVerified": False,
    }
