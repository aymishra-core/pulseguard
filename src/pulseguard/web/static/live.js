/* Pure decisions for the live dashboard. No DOM. */
var pgLive = {
  shouldApply: function (currentMs, incomingMs) {
    return typeof incomingMs === "number" && incomingMs > currentMs;
  },
  isStale: function (msSinceSuccess) {
    return msSinceSuccess >= 15000;
  },
  backoffMs: function (fails) {
    var steps = [5000, 10000, 20000];
    var index = fails < 0 ? 0 : fails;
    if (index >= steps.length) index = steps.length - 1;
    return steps[index];
  },
  pollMs: function (down, openCount) {
    return down || openCount ? 3000 : 5000;
  },
  liveLabel: function (generatedMs, nowMs, stale) {
    if (stale) return "Connection lost, retrying";
    var seconds = Math.max(0, Math.floor((nowMs - generatedMs) / 1000));
    if (seconds < 1) return "Live · updated just now";
    return "Live · updated " + seconds + "s ago";
  },
  dueLabel: function (nextMs, nowMs) {
    if (typeof nextMs !== "number" || !isFinite(nextMs) || nextMs <= nowMs) return "now";
    var left = Math.round((nextMs - nowMs) / 1000);
    if (left < 1) return "now";
    return left >= 100 ? Math.round(left / 60) + "m" : left + "s";
  },
  dueText: function (nextMs, nowMs, report) {
    var noun = report ? "report" : "check";
    if (typeof nextMs !== "number" || !isFinite(nextMs) || nextMs <= nowMs) return "Due now";
    var left = Math.max(0, Math.round((nextMs - nowMs) / 1000));
    if (left < 1) return "Due now";
    return "Next " + noun + " in " + left + " s";
  },
  clockLabel: function (iso) {
    var when = new Date(iso);
    if (isNaN(when.getTime())) return "";
    return when.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
  },
  newerRows: function (rows, sinceId) {
    return (rows || [])
      .filter(function (row) { return row.id > sinceId; })
      .sort(function (a, b) { return String(a.at).localeCompare(String(b.at)); });
  },
  mergeChecks: function (existing, incoming, cap) {
    var maxId = 0;
    (existing || []).forEach(function (row) {
      var id = +row.id || 0;
      if (id > maxId) maxId = id;
    });
    var fresh = (incoming || []).filter(function (row) {
      return row && +row.id > maxId;
    });
    var merged = fresh.concat(existing || []);
    var limit = cap || 50;
    return merged.slice(0, limit);
  },
  logBadge: function (lastOkMs, nowMs, hidden, pollMs) {
    if (hidden) return "paused";
    var windowMs = 2 * (pollMs || 5000);
    if (typeof lastOkMs !== "number" || nowMs - lastOkMs > windowMs) return "reconnecting";
    return "streaming";
  }
};
if (typeof module !== "undefined" && module.exports) module.exports = pgLive;
