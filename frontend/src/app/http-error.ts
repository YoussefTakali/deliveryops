/** The human-readable message behind an HTTP error — including when the response was requested as a
 *  Blob (file downloads), where FastAPI's `{"detail": …}` would otherwise be lost inside the Blob. */
export async function errorText(e: any, fallback = 'Une erreur est survenue.'): Promise<string> {
  let body = e?.error;
  if (typeof Blob !== 'undefined' && body instanceof Blob) {
    try { body = JSON.parse(await body.text()); } catch { body = null; }
  }
  const detail = body?.detail ?? body;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) return detail.map((d: any) => d?.msg ?? String(d)).join(' · ');
  if (e?.status === 0) return 'Le serveur ne répond pas (réseau ou service arrêté).';
  return e?.message && !String(e.message).startsWith('Http failure') ? e.message : fallback;
}
