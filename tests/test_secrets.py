"""The credential advisory of --import / --update: entries that would be copied onto the board but look like a secret.

The fake secrets are assembled at run time from fragments through a function call (which the compiler cannot fold
back into one constant), so no token-shaped string sits in the source or in the compiled .pyc for a secret scanner
(the gitleaks pre-commit hook) to flag."""
from unittest import mock

import pytest

import kopi as cli

from helpers import *  # noqa: F401,F403  (shared boards and runners)


def cat(*parts):
    return "".join(parts)


GITHUB = cat("ghp_", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
SECRETS = {
    "GitHub token": f"Rotated the token {GITHUB} after the leak",
    "AWS access key": cat("Deploy with ", "AKIA", "IOSFODNN7EXAMPLE", " from CI"),
    "Slack token": cat("Posted with ", "xoxb", "-1234567890-abcdefghij"),
    "API key (sk-...)": cat("Set ", "sk-", "proj1234567890abcdefghij", " in the config"),
    "private key": cat("Pasted ", "-----BEGIN ", "RSA PRIVATE KEY-----", " by mistake"),
    "JSON web token": cat("Used ", "eyJ", "hbGciOiJIUzI1NiJ9", ".", "eyJ", "zdWIiOiIxMjM0NTY3", ".c2lnbmF0dXJl", " to log in"),
    "password in a URL": cat("Cloned https://deploy:", "s3cretPw99", "@example.com/repo.git"),
    "secret assignment": cat("Fixed the login with password = ", "Tr0ub4dor9x", " in the script"),
}


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_each_kind_of_credential_is_found_and_masked(kind):
    found = cli.find_secrets(SECRETS[kind])
    assert kind in [k for k, _ in found]
    assert all(len(masked) == 5 and masked.endswith("…") for _, masked in found)           # four characters and an ellipsis, never the whole value


@pytest.mark.parametrize("line", [
    "Added a `--token` option for the GitHub API",
    "The password prompt now hides what you type",
    "`GH_TOKEN` is read from the environment when it is set",
    "Fixed the token refresh when the session expires",
    "Reads the secret from the keyring service `app:host`",
    "Set `api_key = <your key>` in the config",
    "Use `password=xxxxxxxx1` as the example value",
    "token = ${GH_TOKEN}",
    "secret: changeme123",
    "Visit https://github.com/lateralblast/kopi for the source",
    "Cloned git@github.com:o/r.git over ssh",
    "password: your12345pw",
    "Bumped to 1.2.3 and renamed ghp_ prefix handling",
])
def test_ordinary_text_and_placeholders_are_not_flagged(line):
    assert cli.find_secrets(line) == []


def test_only_bullets_are_scanned_and_line_numbers_follow_wrapped_entries():
    text = ("# Changelog\n\n## [1.0.0] - 2026-01-01\n"
            f"Prose outside a bullet mentions {GITHUB} but is not imported.\n"
            "- A harmless entry\n"
            "- A long entry that wraps\n"
            f"  and its second line holds {GITHUB}\n")
    assert cli.secret_lines(text.splitlines()) == [(7, "GitHub token", "ghp_…")]


def test_the_warning_names_the_file_and_line_but_never_the_secret(capsys):
    cli.warn_secrets(f"## [1.0.0]\n- Rotated {GITHUB}\n", f"- [ ] rotate {cat('AKIA', 'IOSFODNN7EXAMPLE')}\n")
    err = capsys.readouterr().err
    assert "2 entries may contain a credential" in err and "rotate it if it was real" in err
    assert "CHANGELOG.md:2 (GitHub token ghp_…)" in err and "TODO.md:1 (AWS access key AKIA…)" in err
    assert GITHUB not in err and "IOSFODNN7EXAMPLE" not in err                              # masked
    cli.warn_secrets("- Fine\n", "- [ ] Fine\n")
    assert capsys.readouterr().err == ""                                                    # silent when clean


def test_the_warning_lists_the_first_five_and_counts_the_rest(capsys):
    cli.warn_secrets("".join(f"- Token {GITHUB} number {n}\n" for n in range(8)), "")
    err = capsys.readouterr().err
    assert "8 entries may contain a credential" in err and "... and 3 more" in err and err.count("CHANGELOG.md:") == 5


def test_code_comment_entries_are_checked_too(capsys):
    cli.warn_secrets("", "", extras=[("Todo", cat("[code] fix the hardcoded password = ", "Tr0ub4dor9x"), "src/app.py:12")])
    err = capsys.readouterr().err
    assert "[code] fix the hardcoded password = Tr0… (secret assignment Tr0u…)" in err or "secret assignment Tr0u…" in err
    assert "Tr0ub4dor9x" not in err


CHANGELOG_WITH_SECRET = f"# Changelog\n\n## [0.1.0] - 2026-01-01\n- Rotated the leaked {GITHUB}\n"


def _import_with(changelog, todo="", argv=("--import", "--dry-run")):
    with mock.patch("kopi.check_auth"), mock.patch("kopi.git_owner", return_value="x"), \
         mock.patch("kopi.load_config", return_value={}), mock.patch("kopi.fetch_linked", return_value=[]), \
         mock.patch("kopi._read", side_effect=lambda p: changelog if p == "CHANGELOG.md" else todo):
        return cli.main(list(argv))


def test_import_warns_but_still_imports_and_never_prints_the_secret(capsys):
    assert _import_with(CHANGELOG_WITH_SECRET) == 0
    cap = capsys.readouterr()
    assert "1 entry may contain a credential" in cap.err and "CHANGELOG.md:4 (GitHub token ghp_…)" in cap.err
    assert "Dry run: 1 items would be imported" in cap.out                                  # advisory: the import is unchanged
    assert GITHUB[:12] not in cap.err                                                       # (the board item title does hold it: that is what the warning is about)


def test_update_warns_and_so_does_sync_but_read_only_commands_do_not(capsys):
    _run_update(SETTLED, argv=("--update", "--dry-run"), changelog=CHANGELOG_WITH_SECRET, todo="# TODO\n")
    assert "may contain a credential" in capsys.readouterr().err
    for argv in (("--diff",), ("--report",), ("--list", "done")):
        _run_update(SETTLED, argv=argv, changelog=CHANGELOG_WITH_SECRET, todo="# TODO\n")
        assert "may contain a credential" not in capsys.readouterr().err


def test_a_single_file_update_only_checks_that_file(capsys):
    rc, _, _ = _run_update(SETTLED, argv=("--update", "TODO.md", "--dry-run"), changelog=CHANGELOG_WITH_SECRET, todo="# TODO\n")
    cap = capsys.readouterr()
    assert rc == 0 and "Dry run:" in cap.out and "may contain a credential" not in cap.err
