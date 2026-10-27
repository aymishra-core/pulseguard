(function () {
  function readStore(key) {
    try { return localStorage.getItem(key); } catch (err) { return null; }
  }
  function writeStore(key, value) {
    try { localStorage.setItem(key, value); } catch (err) { /* private mode */ }
  }
  var root = document.documentElement;
  var savedTheme = readStore("pulseguard-theme");
  if (savedTheme === "light" || savedTheme === "dark") root.setAttribute("data-theme", savedTheme);
  var themeButton = document.querySelector("#theme-toggle");
  function themeLabel() {
    var current = root.getAttribute("data-theme");
    if (!current) current = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    if (themeButton) {
      themeButton.textContent = current === "dark" ? "Light theme" : "Dark theme";
      themeButton.setAttribute("aria-pressed", current === "dark" ? "true" : "false");
    }
  }
  themeLabel();
  if (themeButton) {
    themeButton.addEventListener("click", function () {
      var current = root.getAttribute("data-theme");
      if (!current) current = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      writeStore("pulseguard-theme", next);
      themeLabel();
    });
  }
  var headline = root.getAttribute("data-headline") || "";
  var dot = root.getAttribute("data-dot") || "unknown";
  if (dot === "down") {
    var count = headline.split(" ")[0];
    document.title = "(" + count + " down) " + document.title;
  }
  var shell = document.querySelector("#shell");
  var collapse = document.querySelector("#nav-collapse");
  if (shell && readStore("pulseguard-sidebar") === "collapsed") {
    shell.classList.add("is-collapsed");
    if (collapse) collapse.setAttribute("aria-pressed", "true");
  }
  if (collapse && shell) {
    collapse.addEventListener("click", function () {
      var closed = shell.classList.toggle("is-collapsed");
      collapse.setAttribute("aria-pressed", closed ? "true" : "false");
      writeStore("pulseguard-sidebar", closed ? "collapsed" : "open");
    });
  }
  var path = location.pathname;
  document.querySelectorAll(".nav a").forEach(function (link) {
    if (link.getAttribute("href") === path) link.setAttribute("aria-current", "page");
  });
  var palette = document.querySelector("#palette");
  var paletteQuery = document.querySelector("#palette-query");
  var paletteList = document.querySelector("#palette-list");
  var paletteIndex = 0;
  var pages = [
    ["Dashboard", "/dashboard"],
    ["Monitors", "/monitors"],
    ["Add monitor", "/monitors/new"],
    ["Incidents", "/incidents"],
    ["Certificates", "/certificates"],
    ["Alerts", "/alerts"],
    ["SLA", "/sla"],
    ["Agents", "/agents"],
    ["Data", "/data"],
    ["Settings", "/settings"],
    ["Help", "/help"],
    ["Public status", "/status"]
  ];
  var namesNode = document.querySelector("#nav-monitors");
  var monitorNames = [];
  if (namesNode) {
    try { monitorNames = JSON.parse(namesNode.textContent || "[]"); } catch (err) { monitorNames = []; }
  }
  function paletteItems(query) {
    var needle = (query || "").toLowerCase();
    var items = pages.map(function (row) { return { label: row[0], href: row[1] }; });
    monitorNames.forEach(function (name) {
      items.push({ label: "Monitor " + name, href: "/monitors/" + encodeURIComponent(name) + "/view" });
    });
    return items.filter(function (item) { return item.label.toLowerCase().indexOf(needle) !== -1; });
  }
  function paintPalette() {
    if (!paletteList || !paletteQuery) return;
    var items = paletteItems(paletteQuery.value);
    if (paletteIndex >= items.length) paletteIndex = 0;
    paletteList.textContent = "";
    items.forEach(function (item, index) {
      var li = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "row";
      button.textContent = item.label;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", index === paletteIndex ? "true" : "false");
      button.addEventListener("click", function () { location.href = item.href; });
      li.appendChild(button);
      paletteList.appendChild(li);
    });
  }
  function openPalette() {
    if (!palette || !paletteQuery) return;
    palette.hidden = false;
    paletteIndex = 0;
    paletteQuery.value = "";
    paintPalette();
    paletteQuery.focus();
  }
  function closePalette() {
    if (palette) palette.hidden = true;
  }
  var paletteOpen = document.querySelector("#palette-open");
  if (paletteOpen) paletteOpen.addEventListener("click", openPalette);
  document.addEventListener("keydown", function (event) {
    var key = event.key.toLowerCase();
    if ((event.metaKey || event.ctrlKey) && key === "k") {
      event.preventDefault();
      if (palette && palette.hidden) openPalette();
      else closePalette();
      return;
    }
    if (!palette || palette.hidden) return;
    if (event.key === "Escape") closePalette();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      var total = paletteList ? paletteList.children.length : 0;
      if (!total) return;
      event.preventDefault();
      paletteIndex = event.key === "ArrowDown" ? (paletteIndex + 1) % total : (paletteIndex - 1 + total) % total;
      paintPalette();
    }
    if (event.key === "Enter" && paletteList) {
      var chosen = paletteList.querySelector('[aria-selected="true"]');
      if (chosen) chosen.click();
    }
  });
  if (paletteQuery) paletteQuery.addEventListener("input", function () { paletteIndex = 0; paintPalette(); });
  if (palette) palette.addEventListener("click", function (event) { if (event.target === palette) closePalette(); });

  var kind = document.querySelector("#kind");
  function showFields() {
    if (!kind) return;
    document.querySelectorAll("[data-kinds]").forEach(function (block) {
      var kinds = block.getAttribute("data-kinds").split(" ");
      block.hidden = kinds.indexOf(kind.value) === -1;
    });
  }
  function showBaseline() {
    var chosen = document.querySelector("input[name=baseline]:checked");
    var mode = chosen ? chosen.value : "learn";
    document.querySelectorAll("[data-baseline]").forEach(function (block) {
      var on = block.getAttribute("data-baseline") === mode;
      block.hidden = !on;
      block.querySelectorAll("input, textarea, select").forEach(function (input) {
        input.disabled = !on;
      });
    });
  }
  if (kind) {
    kind.addEventListener("change", function () {
      showFields();
      showBaseline();
    });
    showFields();
  }
  document.querySelectorAll("input[name=baseline]").forEach(function (radio) {
    radio.addEventListener("change", showBaseline);
  });
  showBaseline();

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    var value = button.getAttribute("data-copy") || "";
    var done = function () { button.textContent = "Copied"; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done);
      return;
    }
    var area = document.createElement("textarea");
    area.value = value;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    done();
  });

  document.querySelectorAll(".toast").forEach(function (toast) {
    setTimeout(function () { toast.remove(); }, 6000);
  });

  var filters = document.querySelector("#filter-kind");
  function applyFilter() {
    var kindValue = document.querySelector("#filter-kind");
    var stateValue = document.querySelector("#filter-state");
    var queryValue = document.querySelector("#filter-query");
    var kindText = kindValue ? kindValue.value : "";
    var stateText = stateValue ? stateValue.value : "";
    var query = queryValue ? queryValue.value.toLowerCase() : "";
    document.querySelectorAll("[data-monitor]").forEach(function (card) {
      var okKind = !kindText || card.getAttribute("data-kind") === kindText;
      var okState = !stateText || card.getAttribute("data-state") === stateText;
      var name = (card.getAttribute("data-name") || "").toLowerCase();
      card.hidden = !(okKind && okState && name.indexOf(query) !== -1);
    });
  }
  if (filters) {
    ["#filter-kind", "#filter-state", "#filter-query"].forEach(function (selector) {
      var node = document.querySelector(selector);
      if (node) node.addEventListener("input", applyFilter);
    });
  }

  var board = document.querySelector("[data-board]");
  var label = document.querySelector("[data-updated]");
  var at = Date.now();

  function spark(points) {
    if (!points || points.length < 2) return null;
    var width = 120;
    var height = 36;
    var peak = Math.max.apply(null, points) || 1;
    var step = width / (points.length - 1);
    var coords = points.map(function (value, index) {
      var y = height - (value / peak) * (height - 8) - 4;
      return (index * step).toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 120 36");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "latency");
    var line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("fill", "none");
    line.setAttribute("points", coords);
    svg.appendChild(line);
    return svg;
  }

  function localTime(value) {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    var seconds = Math.round((Date.now() - date.getTime()) / 1000);
    var text = date.toLocaleString();
    if (seconds < 60) return seconds + " s ago";
    if (seconds < 3600) return Math.round(seconds / 60) + " m ago";
    if (seconds < 86400) return Math.round(seconds / 3600) + " h ago";
    return text;
  }
  function paintTimes() {
    document.querySelectorAll("time[datetime]").forEach(function (node) {
      var exact = new Date(node.getAttribute("datetime"));
      if (Number.isNaN(exact.getTime())) return;
      node.textContent = localTime(node.getAttribute("datetime"));
      node.setAttribute("title", exact.toLocaleString());
    });
    var zone = document.querySelector("#zone");
    if (zone) {
      var name = "";
      try { name = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (err) { name = ""; }
      zone.textContent = "Times are shown in " + (name || "your local time zone") + ".";
    }
    document.querySelectorAll("[data-checked]").forEach(function (card) {
      var label = card.querySelector(".next-check");
      var stamp = card.getAttribute("data-checked");
      var interval = Number(card.getAttribute("data-interval") || "30");
      if (!label || !stamp) return;
      var age = (Date.now() - new Date(stamp).getTime()) / 1000;
      var left = Math.max(0, Math.round(interval - age));
      label.textContent = left ? "Next check in " + left + " s" : "Check due";
    });
  }
  paintTimes();
  setInterval(paintTimes, 1000);
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-open]");
    if (opener) {
      var dialog = document.getElementById(opener.getAttribute("data-open"));
      if (dialog && dialog.showModal) dialog.showModal();
    }
    var closer = event.target.closest("[data-close]");
    if (closer) {
      var box = closer.closest("dialog");
      if (box && box.close) box.close();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll("dialog[open]").forEach(function (dialog) { dialog.close(); });
    }
  });
  var sort = document.querySelector("#filter-sort");
  if (sort && readStore("pulseguard-sort")) sort.value = readStore("pulseguard-sort");
  if (filters && readStore("pulseguard-kind")) {
    var kindFilter = document.querySelector("#filter-kind");
    if (kindFilter) kindFilter.value = readStore("pulseguard-kind");
  }

  function paint(body) {
    var counts = body.counts || {};
    document.querySelectorAll(".summary article").forEach(function (article) {
      var title = article.querySelector("h2");
      var value = article.querySelector("p");
      if (!title || !value) return;
      var name = title.textContent;
      if (name === "Up") value.textContent = counts.up;
      if (name === "Down") value.textContent = counts.down;
      if (name === "Unknown") value.textContent = counts.unknown;
      if (name === "Open incidents") value.textContent = body.open_incidents;
      if (name === "Certificates within 30 days") value.textContent = body.certs;
      if (name === "24 h uptime") value.textContent = body.uptime;
    });
    board.textContent = "";
    (body.monitors || []).forEach(function (monitor) {
      var card = document.createElement("article");
      card.className = "card";
      card.setAttribute("data-monitor", "");
      card.setAttribute("data-kind", monitor.kind);
      card.setAttribute("data-state", monitor.state);
      card.setAttribute("data-name", monitor.name);
      card.setAttribute("data-interval", String(monitor.interval || 30));
      card.setAttribute("data-checked", monitor.checked_at || "");
      var kindText = document.createElement("p");
      kindText.className = "kind";
      kindText.textContent = monitor.kind;
      var heading = document.createElement("h2");
      var link = document.createElement("a");
      link.href = "/monitors/" + encodeURIComponent(monitor.name) + "/view";
      link.textContent = monitor.name;
      heading.appendChild(link);
      var pillRow = document.createElement("p");
      var pill = document.createElement("span");
      pill.className = "pill " + monitor.state;
      pill.textContent = monitor.state;
      pillRow.appendChild(pill);
      var message = document.createElement("p");
      message.textContent = monitor.plain || monitor.message || "no results yet";
      var checked = document.createElement("p");
      checked.className = "muted";
      var time = document.createElement("time");
      if (monitor.checked_at) {
        time.setAttribute("datetime", monitor.checked_at);
        time.textContent = monitor.checked || "not checked";
      } else {
        time.textContent = "not checked";
      }
      checked.appendChild(time);
      var uptime = document.createElement("p");
      uptime.textContent = "24 h uptime " + monitor.uptime;
      card.appendChild(kindText);
      card.appendChild(heading);
      card.appendChild(pillRow);
      card.appendChild(message);
      card.appendChild(checked);
      card.appendChild(uptime);
      var drawn = spark(monitor.points || []);
      if (drawn) card.appendChild(drawn);
      board.appendChild(card);
    });
    var feed = document.querySelector("#activity");
    if (feed && body.activity) {
      feed.textContent = "";
      if (!body.activity.length) {
        var empty = document.createElement("li");
        empty.textContent = "No changes yet.";
        feed.appendChild(empty);
      }
      body.activity.forEach(function (event) {
        var item = document.createElement("li");
        var mark = document.createElement("span");
        mark.className = "pill " + event.state;
        mark.textContent = event.state;
        var when = document.createElement("time");
        when.setAttribute("datetime", event.at);
        when.textContent = event.at;
        item.appendChild(mark);
        item.appendChild(document.createTextNode(" "));
        item.appendChild(when);
        item.appendChild(document.createTextNode(" " + event.text));
        feed.appendChild(item);
      });
    }
    applyFilter();
    paintTimes();
  }

  function pull() {
    fetch("/api/dashboard", { headers: { Accept: "application/json" } }).then(function (response) {
      if (!response.ok) return null;
      return response.json();
    }).then(function (body) {
      if (!body) return;
      paint(body);
      at = Date.now();
    });
  }

  if (board && label) {
    setInterval(pull, 5000);
    setInterval(function () {
      var seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
      label.textContent = "updated " + seconds + " s ago";
    }, 1000);
  }
})();
