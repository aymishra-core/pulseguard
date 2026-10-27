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
  if (!board || !label) return;
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
      message.textContent = monitor.message || "no results yet";
      var checked = document.createElement("p");
      checked.className = "muted";
      checked.textContent = monitor.checked || "not checked";
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
    applyFilter();
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

  setInterval(pull, 5000);
  setInterval(function () {
    var seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
    label.textContent = "updated " + seconds + " s ago";
  }, 1000);
})();
