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
  var named = document.querySelector("#name");
  var monitorForm = named ? named.form : null;
  var probeButton = document.querySelector("#probe");
  var probeResult = document.querySelector("#probe-result");
  var schemeOffer = document.querySelector("#scheme-offer");
  var targetInput = document.querySelector("#target");
  function offerScheme() {
    if (!schemeOffer || !targetInput || !kind) return;
    var httpKind = kind.value === "http" || kind.value === "keyword" || kind.value === "defacement";
    var value = targetInput.value.trim();
    if (httpKind && value && value.indexOf("://") === -1) {
      schemeOffer.hidden = false;
      schemeOffer.textContent = "";
      var use = document.createElement("button");
      use.type = "button";
      use.className = "btn-secondary";
      var scheme = "https:" + "//";
      use.textContent = "Use " + scheme + value;
      use.addEventListener("click", function () { targetInput.value = scheme + value; offerScheme(); });
      schemeOffer.appendChild(document.createTextNode("This address has no scheme. "));
      schemeOffer.appendChild(use);
    } else {
      schemeOffer.hidden = true;
    }
  }
  if (targetInput) targetInput.addEventListener("input", offerScheme);
  if (kind) kind.addEventListener("change", offerScheme);
  if (probeButton && monitorForm) {
    probeButton.addEventListener("click", function () {
      probeButton.classList.add("is-loading");
      var body = new FormData(monitorForm);
      fetch("/monitors/probe", { method: "POST", body: body, headers: { Accept: "application/json" } })
        .then(function (response) { return response.json().then(function (payload) { return { ok: response.ok, payload: payload }; }); })
        .then(function (result) {
          if (!probeResult) return;
          probeResult.hidden = false;
          if (!result.ok) probeResult.textContent = result.payload.error || "The check was not run.";
          else probeResult.textContent = (result.payload.state || "") + " · " + result.payload.latency_ms + " ms · " + result.payload.message;
        })
        .finally(function () { probeButton.classList.remove("is-loading"); });
    });
  }
  if (monitorForm && monitorForm.querySelector("#name")) {
    var dirty = false;
    monitorForm.addEventListener("input", function () { dirty = true; });
    monitorForm.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (event) {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }
  document.querySelectorAll("[data-fill-target]").forEach(function (button) {
    button.addEventListener("click", function () {
      var input = document.querySelector("#target");
      if (input) input.value = button.getAttribute("data-fill-target") || "";
    });
  });
  var helpSearch = document.querySelector("#help-search");
  if (helpSearch) {
    helpSearch.addEventListener("input", function () {
      var needle = helpSearch.value.toLowerCase();
      document.querySelectorAll("[data-guide] p").forEach(function (node) {
        node.hidden = Boolean(needle) && node.textContent.toLowerCase().indexOf(needle) === -1;
      });
    });
  }
  var showAgain = document.querySelector("#show-onboarding");
  if (showAgain) {
    showAgain.addEventListener("click", function () {
      writeStore("pulseguard-onboarding", "show");
      showAgain.textContent = "The checklist will show on the dashboard.";
    });
  }
  var checklist = document.querySelector("#onboarding");
  if (checklist && readStore("pulseguard-onboarding") === "hide") checklist.hidden = true;
  var hideChecklist = document.querySelector("#hide-onboarding");
  if (hideChecklist && checklist) {
    hideChecklist.addEventListener("click", function () {
      writeStore("pulseguard-onboarding", "hide");
      checklist.hidden = true;
    });
  }
  var password = document.querySelector("#new_password");
  if (password) {
    var hint = document.createElement("p");
    hint.className = "help";
    hint.id = "strength";
    password.insertAdjacentElement("afterend", hint);
    password.addEventListener("input", function () {
      var size = password.value.length;
      hint.textContent = size === 0 ? "" : (size < 8 ? "Too short. Use at least 8 characters." : "Long enough.");
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    var value = button.getAttribute("data-copy") || "";
    var done = function () {
      button.textContent = "Copied";
      var note = document.createElement("p");
      note.className = "toast";
      note.setAttribute("role", "status");
      note.textContent = "Copied";
      document.body.appendChild(note);
      setTimeout(function () { note.remove(); }, 2000);
    };
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
    var svgNs = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 120 36");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "latency");
    var line = document.createElementNS(svgNs, "polyline");
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
  function paintTicks() {
    document.querySelectorAll("[data-tick]").forEach(function (node) {
      var start = new Date(node.getAttribute("data-tick"));
      if (Number.isNaN(start.getTime())) return;
      var seconds = Math.max(0, Math.round((Date.now() - start.getTime()) / 1000));
      var minutes = Math.floor(seconds / 60);
      var hours = Math.floor(minutes / 60);
      minutes %= 60;
      var sec = seconds % 60;
      if (hours) node.textContent = hours + "h " + minutes + "m";
      else if (minutes) node.textContent = minutes + "m " + sec + "s";
      else node.textContent = sec + "s";
    });
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
  paintTicks();
  setInterval(function () { paintTimes(); paintTicks(); }, 1000);
  document.querySelectorAll("[data-pick-kind]").forEach(function (button) {
    button.addEventListener("click", function () {
      var select = document.querySelector("#kind");
      if (!select) return;
      select.value = button.getAttribute("data-pick-kind") || select.value;
      select.dispatchEvent(new Event("change"));
      document.querySelectorAll("[data-pick-kind]").forEach(function (other) {
        other.classList.toggle("is-on", other === button);
      });
    });
  });
  var nameField = document.querySelector("#name");
  if (nameField) {
    nameField.addEventListener("input", function () {
      var bad = nameField.value && !/^[A-Za-z0-9_-]{1,128}$/.test(nameField.value);
      nameField.setAttribute("aria-invalid", bad ? "true" : "false");
    });
  }
  document.querySelectorAll("input[type=file]").forEach(function (input) {
    input.addEventListener("change", function () {
      var meta = input.parentElement && input.parentElement.querySelector(".file-meta");
      var file = input.files && input.files[0];
      if (!meta) return;
      meta.textContent = file ? file.name + " · " + file.size + " bytes" : "";
    });
    var zone = input.closest(".drop");
    if (!zone) return;
    zone.addEventListener("dragover", function (event) { event.preventDefault(); });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      if (event.dataTransfer && event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        input.dispatchEvent(new Event("change"));
      }
    });
  });
  var slaResult = document.querySelector(".result");
  var slaGauge = document.querySelector("#sla-gauge");
  if (slaResult && slaGauge) {
    var achieved = slaResult.textContent.match(/Achieved ([0-9.]+)%/);
    if (achieved) slaGauge.value = Number(achieved[1]);
  }
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

  function paintShell(body, counts) {
    var down = Number(counts.down || 0);
    var up = Number(counts.up || 0);
    var open = Number(body.open_incidents || 0);
    var dot = "unknown";
    var line = "Waiting for the first checks";
    var detail = "Add a monitor or wait for the first check. Nothing here contacts a target until a check runs.";
    if (down === 1) { dot = "down"; line = "1 monitor down"; }
    else if (down > 1) { dot = "down"; line = down + " monitors down"; }
    else if (up > 0) {
      dot = "up";
      line = "All systems operational";
      detail = "Every enabled monitor answered its last check.";
    } else if (Number(counts.paused || 0) > 0) {
      dot = "paused";
      line = "All monitors are paused";
    }
    var headline = document.querySelector("#headline");
    var pill = document.querySelector(".headline .pill");
    var ring = document.querySelector("#hero-ring");
    var title = document.querySelector("#hero-title");
    if (headline) headline.textContent = line;
    if (pill) { pill.className = "pill " + dot; pill.textContent = dot; }
    if (ring) ring.className = "ring " + dot;
    if (title) {
      title.textContent = open
        ? open + (open === 1 ? " incident ongoing" : " incidents ongoing")
        : line;
      if (title.nextElementSibling) {
        title.nextElementSibling.textContent = open
          ? "Open the incident to see what failed and what to check."
          : detail;
      }
    }
    if (dot === "down") document.title = "(" + down + " down) " + document.title.replace(/^\(\d+ down\) /, "");
    else document.title = document.title.replace(/^\(\d+ down\) /, "");
    var icon = document.querySelector("link[rel='icon']");
    if (icon && icon.getAttribute("href")) {
      icon.setAttribute("href", icon.getAttribute("href").replace(/favicon-[a-z]+\.svg/, "favicon-" + dot + ".svg"));
    }
    var banner = document.querySelector(".banner");
    var main = document.querySelector(".main");
    if (open && main) {
      if (!banner) {
        banner = document.createElement("p");
        banner.className = "banner";
        banner.setAttribute("role", "status");
        main.insertBefore(banner, main.querySelector("main"));
      }
      banner.textContent = "";
      banner.appendChild(document.createTextNode(open + (open === 1 ? " open incident. " : " open incidents. ")));
      var see = document.createElement("a");
      see.href = "/incidents";
      see.textContent = "See what to check";
      banner.appendChild(see);
    } else if (banner) {
      banner.remove();
    }
    document.querySelectorAll(".nav a").forEach(function (link) {
      if (link.getAttribute("href") !== "/incidents") return;
      var badge = link.querySelector(".badge");
      if (!open) {
        if (badge) badge.remove();
        return;
      }
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "badge";
        link.appendChild(badge);
      }
      badge.textContent = String(open);
    });
  }

  function paint(body) {
    var counts = body.counts || {};
    paintShell(body, counts);
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
