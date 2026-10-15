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
    """Generate a nine-character test-profile password, per requested 7-9 range.

    Prefer nine rather than seven for additional entropy. Each secret has mixed
    upper/lowercase letters, numbers and symbols, with OS-backed randomness.
    """
    chars = (
        [secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(5)]
        + [secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
        + [secrets.choice("0123456789") for _ in range(2)]
        + [secrets.choice("#!@?")]
    )
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def make_profile_data() -> dict:
    """Produce visually neutral usernames, cryptographic passwords and demo-only contact data.

    example.test intentionally never receives messages. No generated personal
    identity is authorized for a third-party/KYC registration.
    """
    # Synthetically generated, readable names for isolated local test
    # profiles. These are not verified legal identities.
    first = secrets.choice((
        "Marina", "Clara", "Larissa", "Juliana", "Camila",
        "Felipe", "Lucas", "Bruno", "Rafael", "Daniel",
    ))
    last = secrets.choice((
        "Almeida", "Moraes", "Costa", "Barbosa", "Teixeira",
        "Pereira", "Martins", "Rocha", "Nogueira", "Ferreira",
    ))
    # Keep account usernames short (6-9 chars), readable, and independent
    # of the generated surname. Use CSPRNG rather than predictable counters.
    username_length = 6 + secrets.randbelow(4)
    username = first[:2].lower() + "".join(
        secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789")
        for _ in range(username_length - 2)
    )
    nine = "".join(str(secrets.randbelow(10)) for _ in range(9))
    while len(set(nine)) == 1:
        nine = "".join(str(secrets.randbelow(10)) for _ in range(9))
    cpf = valid_cpf_digits(nine)
    return {
        "fullName": first + " " + last,
        "username": username,
        "password": make_password(),
        "email": username + "@example.test",
        "phone": "+55 (11) 90000-" + str(secrets.randbelow(10000)).zfill(4),
        "cpf": cpf[:3] + "." + cpf[3:6] + "." + cpf[6:9] + "-" + cpf[9:],
        "isTestData": True,
        "cpfVerified": False,
    }
