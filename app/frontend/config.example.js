/* Frontend runtime config.
 *
 * A static site with no bundler can't read a .env file, so this file is the
 * equivalent - plain JS, loaded before app.js, editable in place (no rebuild).
 *
 *   API_BASE  ""                          -> same origin (backend serves this page)
 *             "http://localhost:8000"     -> backend hosted separately
 */
window.__VAHAN_CONFIG__ = {
  API_BASE: "",
};
