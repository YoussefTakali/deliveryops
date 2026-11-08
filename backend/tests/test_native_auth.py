"""Native auth needs AUTH_MODE=native set before the app imports, which the shared dev-mode app
fixture can't provide — so the full flow runs in its own process (tests/_native_auth_flow.py) and
this test just asserts it passed. Unit tests for the pieces are in test_security.py."""

import pathlib
import subprocess
import sys


def _run(script_name: str, marker: str) -> None:
    import os
    script = pathlib.Path(__file__).parent / script_name
    env = {"PYTHONPATH": str(pathlib.Path(__file__).parents[1]), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env={**os.environ, **env})
    assert marker in result.stdout, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr[-2000:]}"
    assert result.returncode == 0


def test_native_auth_flow_end_to_end():
    _run("_native_auth_flow.py", "NATIVE AUTH FLOW OK")


def test_native_account_security_end_to_end():
    """Internal-domain e-mails, lockout, forgot/reset password, two-factor, per-device sessions."""
    _run("_native_security_flow.py", "NATIVE SECURITY FLOW OK")


def test_sign_in_with_microsoft_and_google_end_to_end():
    """Pending accounts, linking by verified e-mail (nOAuth-safe), allowlist, link/unlink, migration."""
    _run("_sso_flow.py", "SSO FLOW OK")
