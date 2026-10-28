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
    if (typeof nextMs !== "number" || !isFinite(nextMs) || nextMs <= nowMs) return "due now";
    var left = Math.round((nextMs - nowMs) / 1000);
    return left >= 100 ? Math.round(left / 60) + "m" : left + "s";
  },
  newerRows: function (rows, sinceId) {
    return (rows || [])
      .filter(function (row) { return row.id > sinceId; })
      .sort(function (a, b) { return String(a.at).localeCompare(String(b.at)); });
  }
};
if (typeof module !== "undefined" && module.exports) module.exports = pgLive;
