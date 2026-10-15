"""Fictional, isolated profile data. Never represents a verified legal identity.

These values belong to local browser profiles; do not send them to real
registration/KYC forms or infer that a CPF belongs to a real person.
"""
import secrets


def valid_cpf_digits(base: str) -> str:
    """Return a syntactically valid 11-digit CPF from a nine-digit seed."""
    if len(base) != 9 or not base.isdigit() or len(set(base)) == 1:
        raise ValueError("CPF de teste: base inválida.")
    digits = [int(x) for x in base]
    for length in (9, 10):
        remainder = (sum(digits[i] * (length + 1 - i) for i in range(length)) * 10) % 11
        digits.append(0 if remainder == 10 else remainder)
    return "".join(map(str, digits))


def make_profile_data() -> dict:
    """Use OS entropy, reserved example.test email domain and unverified CPF."""
    token = secrets.token_hex(5)
    username = "teste" + token
    nine = "".join(str(secrets.randbelow(10)) for _ in range(9))
    while len(set(nine)) == 1:
        nine = "".join(str(secrets.randbelow(10)) for _ in range(9))
    cpf = valid_cpf_digits(nine)
    # 9 characters like "piXez#v3q": six lowercase, an uppercase, digit
    # and special character, with cryptographically secure randomness.
    chars = (
        [secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(6)]
        + [secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
        + [secrets.choice("0123456789")]
        + [secrets.choice("#!@?")]
    )
    secrets.SystemRandom().shuffle(chars)
    password = "".join(chars)
    return {
        "fullName": "Pessoa Teste " + token[:5].upper(),
        "username": username,
        "password": password,
        "email": username + "@example.test",
        "phone": "+55 (11) 90000-" + str(secrets.randbelow(10000)).zfill(4),
        "cpf": cpf[:3] + "." + cpf[3:6] + "." + cpf[6:9] + "-" + cpf[9:],
        "isTestData": True,
        "cpfVerified": False,
    }
