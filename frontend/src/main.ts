import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

// MSAL's login/token popup and its silent-SSO hidden iframe both navigate back to our own origin
// once Microsoft's sign-in flow finishes (the redirect URI is the SPA itself). If our app fully
// bootstraps in there too, its own startup logic (Auth.init() calling ssoSilent(), the gate button
// wired to click handlers, etc.) runs a second time inside a window MSAL itself is actively using
// for an interaction — which is exactly what throws "BLOCK_NESTED_POPUPS"
// (https://aka.ms/msal.js.errors#block_nested_popups). MSAL doesn't need our app's JS to do
// anything there: for a popup it reads the popup's location bar directly from the opener and closes
// it itself; for the silent iframe it reads the iframe's location the same way. So the fix is simply
// never bootstrapping our own app inside either.
const isMsalPopupOrIframe = window.opener !== null || window.parent !== window;

if (!isMsalPopupOrIframe) {
  bootstrapApplication(App, appConfig)
    .catch((err) => console.error(err));
}
