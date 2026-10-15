"""Inspect and optionally remove obsolete Nexora generated TEST account records.

Runs on the Windows agent (where LOCALAPPDATA/Nexora/accounts.json lives).
Does not delete browser profiles, passwords of legitimate accounts, or the DB.
Always prints only safe record IDs / indexes, never profileData.
"""
import argparse
from nexora_accounts.service import AccountsRepository


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Preview/cleanup safely identifiable unsubmitted test records"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Remove the previewed TEST records (creates a JSON backup first)"
    )
    args = parser.parse_args(argv)
    repo = AccountsRepository(migrate_test_passwords=False)
    result = repo.cleanup_test_candidates(apply=args.apply)
    print("Armazenamento: " + str(repo.path))
    print("Registros de teste elegíveis: " + str(len(result["candidates"])))
    for row in result["candidates"]:
        print("{scope} #{index} · {stage} · ID {id}".format(**row))
    if args.apply:
        print("Registros removidos do JSON: " + str(result["deleted"]))
        print("Backup: " + str(result["backup"] or "não necessário"))
        print("Pastas Chromium e perfis originais NÃO foram apagados.")
    else:
        print("Prévia: nenhum registro foi apagado. Revise os IDs acima.")
        print("Para confirmar depois, execute novamente com --apply.")


if __name__ == "__main__":
    main()
