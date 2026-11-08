/** Messages for the `auth_error` code the server puts on the URL when a Microsoft / Google sign-in
 *  (or linking) can't complete — see backend routes/auth.py oidc_callback. */
const MESSAGES: Record<string, string> = {
  cancelled: 'Connexion annulée.',
  session_expired: 'La connexion a expiré ou a été interrompue : recommencez.',
  session_required: 'Connectez-vous d\'abord pour lier une nouvelle méthode de connexion.',
  method_disabled: 'Cette méthode de connexion n\'est pas activée sur cette plateforme.',
  provider_unavailable: 'Le fournisseur d\'identité ne répond pas. Réessayez dans un instant.',
  provider_error: 'Le fournisseur d\'identité a refusé la connexion.',
  token_exchange_failed: 'La connexion n\'a pas pu être finalisée auprès du fournisseur. Réessayez.',
  invalid_token: 'La réponse du fournisseur d\'identité est invalide pour cette plateforme.',
  email_unverified: 'Votre fournisseur ne certifie pas votre adresse e-mail : impossible de vous identifier. Utilisez un compte professionnel.',
  domain_not_allowed: 'Les comptes de ce domaine ne sont pas autorisés. Contactez un administrateur.',
  account_disabled: 'Votre compte est désactivé. Contactez un administrateur.',
  identity_in_use: 'Ce compte Microsoft / Google est déjà lié à un autre utilisateur DeliveryOps.',
};

/** Reads (and removes from the address bar) the sign-in error left by the server, if any. */
export function takeSsoError(): string | null {
  const url = new URL(window.location.href);
  const code = url.searchParams.get('auth_error');
  if (!code) return null;
  url.searchParams.delete('auth_error');
  history.replaceState(null, '', url.pathname + url.search + url.hash);
  return MESSAGES[code] ?? 'La connexion n\'a pas abouti.';
}
