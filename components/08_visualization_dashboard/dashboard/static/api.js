// The only place that talks to the network. Same-origin calls with the HttpOnly session cookie; no token is ever read or stored here.
import { dispositionName, errorText, query } from "./logic.js";

export class ApiError extends Error {
  constructor(status, text) { super(text); this.status = status; }
}

const HEADERS = { "X-Requested-With": "scats-ui" };    // cookie sessions need this on every change

async function request(method, path, body) {
  const opts = { method, credentials: "same-origin", headers: { ...HEADERS } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, opts); } catch { throw new ApiError(0, "The service could not be reached."); }
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail; } catch { /* not JSON */ }
    throw new ApiError(r.status, errorText(r.status, typeof detail === "string" ? detail : JSON.stringify(detail)));
  }
  return r;
}

export const get = async (path, params = {}) => (await request("GET", path + query(params))).json();
export const post = async (path, body) => (await request("POST", path, body)).json();

// A download is fetched, so a refusal (413, 429 ...) is shown as text and not saved as a file. The name comes from the server.
export async function download(path, params) {
  const r = await request("GET", path + query(params));
  const name = dispositionName(r.headers.get("Content-Disposition"));
  if (!name) throw new ApiError(0, "The server did not name the file, so it was not saved.");
  return { blob: await r.blob(), name };
}
