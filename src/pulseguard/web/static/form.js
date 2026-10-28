/* Kind-specific monitor form rules. Words match field_rules.py. No DOM. */
var pgForm = {
  messages: {
    http: "Enter a full URL, for example https://example.com",
    keyword: "Enter a full URL, for example https://example.com",
    defacement: "Enter a full URL, for example https://example.com",
    tcp: "Enter a host only, for example 127.0.0.1. Put the port in the Port field.",
    tls: "Enter a hostname only, for example www.example.com (no https://).",
    dns: "Enter a hostname, for example www.example.com.",
    port: "Enter a port from 1 to 65535"
  },
  placeholders: {
    http: "https://example.com",
    keyword: "https://example.com",
    defacement: "https://example.com",
    tcp: "127.0.0.1",
    tls: "www.example.com",
    dns: "www.example.com"
  },
  stepProblems: function (input) {
    var kind = input.kind, problems = [], name = String(input.name || "").trim();
    if (!input.editing && !/^[A-Za-z0-9_-]{1,128}$/.test(name)) {
      problems.push({field: "name", message: "Use letters, digits, _ or - (no spaces)."});
    }
    if (kind === "heartbeat") return problems;
    var target = String(input.target || "").trim();
    if (!pgForm.targetOk(kind, target)) {
      problems.push({field: "target", message: pgForm.messages[kind] || pgForm.messages.http});
    }
    if (kind === "tcp" || kind === "tls") {
      var port = String(input.port == null ? "" : input.port).trim();
      if (!(kind === "tls" && port === "") && !pgForm.portOk(port)) {
        problems.push({field: "port", message: pgForm.messages.port});
      }
    }
    return problems;
  },
  targetOk: function (kind, target) {
    var text = String(target || "").trim();
    if (kind === "http" || kind === "keyword" || kind === "defacement") return pgForm.schemeOf(text) !== "";
    if (kind === "tcp" || kind === "tls" || kind === "dns") {
      return text !== "" && text.indexOf("://") < 0 && text.indexOf("/") < 0 && text.indexOf(" ") < 0 && text.indexOf(":") < 0;
    }
    return true;
  },
  portOk: function (raw) {
    if (!/^\d+$/.test(String(raw || ""))) return false;
    var number = Number(raw);
    return number >= 1 && number <= 65535;
  },
  schemeOf: function (value) {
    var text = String(value || "").replace(/^\s+/, "");
    var head = text.slice(0, 8).toLowerCase();
    if (head.slice(0, 5) === "https" && head.slice(5, 8) === "://") return "https";
    if (head.slice(0, 4) === "http" && head.slice(4, 7) === "://") return "http";
    return "";
  },
  urlFix: function (kind, value) {
    if (kind !== "tcp" && kind !== "tls" && kind !== "dns") return null;
    var text = String(value || "").trim();
    var scheme = pgForm.schemeOf(text);
    if (!scheme) return null;
    var rest = text.slice(scheme.length + 3);
    var at = rest.indexOf("@");
    if (at >= 0) rest = rest.slice(at + 1);
    var cut = rest.search(/[\/?#]/);
    if (cut >= 0) rest = rest.slice(0, cut);
    if (!rest) return null;
    var host = rest, port = scheme === "https" ? 443 : 80;
    if (host.charAt(0) === "[") {
      var end = host.indexOf("]");
      if (end < 0) return null;
      var tail = host.slice(end + 1);
      host = host.slice(1, end);
      if (tail.charAt(0) === ":" && /^\d+$/.test(tail.slice(1))) port = Number(tail.slice(1));
      if (!host) return null;
    } else {
      var colon = host.lastIndexOf(":");
      if (colon >= 0 && /^\d+$/.test(host.slice(colon + 1))) {
        port = Number(host.slice(colon + 1));
        host = host.slice(0, colon);
        if (!host) return null;
      }
    }
    var label = kind === "dns" ? "Use " + host + " instead" : "Use " + host + " and port " + port + " instead";
    return {host: host, port: port, label: label};
  },
  blockedNames: function (edges, selfName) {
    if (!selfName) return [];
    var dependents = {}, found = [], seen = {}, stack = [selfName], current, child;
    seen[selfName] = true;
    Object.keys(edges || {}).forEach(function (name) {
      (edges[name] || []).forEach(function (parent) {
        if (!dependents[parent]) dependents[parent] = [];
        dependents[parent].push(name);
      });
    });
    while (stack.length) {
      current = stack.pop();
      (dependents[current] || []).forEach(function (item) {
        if (seen[item]) return;
        seen[item] = true;
        found.push(item);
        stack.push(item);
      });
    }
    return found;
  },
  choices: function (names, chosen, blocked, selfName) {
    var skip = {};
    (chosen || []).forEach(function (name) { skip[name] = true; });
    (blocked || []).forEach(function (name) { skip[name] = true; });
    if (selfName) skip[selfName] = true;
    return (names || []).filter(function (name) { return !skip[name]; });
  },
  addChoice: function (chosen, name, limit) {
    var next = (chosen || []).slice();
    if (!name || next.indexOf(name) >= 0 || next.length >= (limit || 8)) return next;
    next.push(name);
    return next;
  },
  removeChoice: function (chosen, name) {
    return (chosen || []).filter(function (item) { return item !== name; });
  },
  menuLabel: function (count) {
    return count ? "Add another dependency" : "None";
  }
};
if (typeof module !== "undefined" && module.exports) module.exports = pgForm;
