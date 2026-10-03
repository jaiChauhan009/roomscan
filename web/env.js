// Deployment setting, loaded by index.html before the app (a classic script, not a module).
// For a hosted front end set the API origin here (https, no trailing slash), e.g.
//   window.ROOMSCAN_API = "https://<user>-roomscan.hf.space";
// Left at the local default, the page talks to `uvicorn ... --port 8000` on this machine.
// A ?api=https://... query parameter still overrides it per browser (see config.js).
window.ROOMSCAN_API = window.ROOMSCAN_API || ((location.hostname === "localhost" || location.hostname === "127.0.0.1") ? "http://localhost:8000" : "https://34-14-174-240.sslip.io");
