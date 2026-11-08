'use strict';
// Detects the email/UPN of the Windows account currently signed into this PC, so the sign-in
// screen can suggest it instead of asking the user to type their email. Works only when the
// machine is Microsoft Entra ID (Azure AD) joined or hybrid-joined — `whoami /upn` returns the
// joined account's UPN in that case, and errors out (caught, returns null) on a purely local or
// non-joined Windows account, or on any other OS. Best-effort, never blocks or prompts.

const { execFile } = require('child_process');

const UPN_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function detectPcAccountHint() {
  return new Promise((resolve) => {
    if (process.platform !== 'win32') return resolve(null);
    execFile('whoami', ['/upn'], { timeout: 4000, windowsHide: true }, (err, stdout) => {
      if (err) return resolve(null); // not Entra/hybrid-joined, or command unavailable
      const upn = String(stdout || '').trim();
      resolve(UPN_RE.test(upn) ? upn : null);
    });
  });
}

module.exports = { detectPcAccountHint };
