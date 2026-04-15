/**
 * VaaniSeva v2 — Self-refreshing WhatsApp relay for Databricks App.
 *
 * Uses Databricks Service Principal (client_credentials) to get OAuth tokens
 * automatically. NO manual T1-T4 token rotation needed.
 *
 * Twilio Function Environment Variables (set in Twilio Console):
 *   SP_CLIENT_ID     — Databricks Service Principal application ID
 *   SP_CLIENT_SECRET — Databricks Service Principal OAuth secret
 *   DB_HOST          — Databricks workspace hostname (without https://)
 *   APP_HOST         — Databricks App hostname (without https://)
 *   SARVAM_KEY       — Sarvam AI API key (for /audio function)
 */

const https = require("https");

// In-memory token cache (survives across warm invocations)
var cachedToken = null;
var tokenExpiry = 0;

function getToken(context) {
  return new Promise(function(resolve, reject) {
    var now = Date.now();
    // Return cached token if still valid (with 5 min buffer)
    if (cachedToken && now < tokenExpiry - 300000) {
      console.log("Using cached token, len:", cachedToken.length);
      return resolve(cachedToken);
    }

    var host = context.DB_HOST || "";
    var clientId = context.SP_CLIENT_ID || "";
    var clientSecret = context.SP_CLIENT_SECRET || "";

    if (!host || !clientId || !clientSecret) {
      return reject(new Error("Missing SP_CLIENT_ID, SP_CLIENT_SECRET, or DB_HOST"));
    }

    var body = "grant_type=client_credentials"
      + "&client_id=" + encodeURIComponent(clientId)
      + "&client_secret=" + encodeURIComponent(clientSecret)
      + "&scope=all-apis";

    console.log("Refreshing token from", host);
    var rq = https.request({
      hostname: host, path: "/oidc/v1/token", method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "Content-Length": Buffer.byteLength(body) }
    }, function(rs) {
      var d = ""; rs.on("data", function(c) { d += c; }); rs.on("end", function() {
        try {
          var resp = JSON.parse(d);
          if (resp.access_token) {
            cachedToken = resp.access_token;
            tokenExpiry = now + (resp.expires_in || 3600) * 1000;
            console.log("Token refreshed OK, len:", cachedToken.length);
            resolve(cachedToken);
          } else {
            console.log("Token error:", d.substring(0, 200));
            reject(new Error("No access_token"));
          }
        } catch(e) { reject(new Error("Token parse: " + e.message)); }
      });
    });
    rq.on("error", function(e) { reject(e); });
    rq.setTimeout(8000, function() { rq.destroy(); reject(new Error("Token timeout")); });
    rq.write(body); rq.end();
  });
}

function callApp(token, appHost, from, msg) {
  return new Promise(function(ok) {
    var body = JSON.stringify({from: from, message: msg});
    console.log("Calling App:", appHost, "token len:", token.length);
    var rq = https.request({
      hostname: appHost, path: "/api/whatsapp/process", method: "POST",
      headers: {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body)}
    }, function(rs) {
      var d = ""; rs.on("data", function(c) { d += c; }); rs.on("end", function() {
        console.log("App resp:", d.substring(0, 150));
        try { ok(JSON.parse(d).reply || "Error. Reply *menu*"); }
        catch(e) { ok("App error: " + d.substring(0, 100) + " Reply *menu*"); }
      });
    });
    rq.on("error", function(e) { console.log("Net err:", e.message); ok("Connection error. Reply *menu*"); });
    rq.setTimeout(9000, function() { rq.destroy(); ok("Timeout. Reply *menu*"); });
    rq.write(body); rq.end();
  });
}

exports.handler = async function(context, event, callback) {
  var body = (event.Body || "").trim();
  var from = event.From || "";
  console.log("IN:", from, body);
  try {
    var token = await getToken(context);
    var reply = await callApp(token, context.APP_HOST || "", from, body);
    console.log("OUT:", reply.substring(0, 80));
    var twiml = new Twilio.twiml.MessagingResponse();
    twiml.message(reply);
    return callback(null, twiml);
  } catch(e) {
    console.log("Error:", e.message);
    var twiml = new Twilio.twiml.MessagingResponse();
    twiml.message("Service temporarily unavailable. Reply *menu* to try again.");
    return callback(null, twiml);
  }
};
