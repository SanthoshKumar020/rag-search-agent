(function () {
  const btn = document.getElementById("go");
  const queryEl = document.getElementById("query");
  const webEl = document.getElementById("use-web");
  const webToggleWrap = document.getElementById("web-toggle-wrap");
  const browseHint = document.getElementById("browse-hint");
  const agentHint = document.getElementById("agent-hint");
  const statusEl = document.getElementById("status");
  const statusTextEl = document.getElementById("status-text");
  const errorEl = document.getElementById("error");
  const resultEl = document.getElementById("result");
  const answerEl = document.getElementById("answer");
  const sourcesEl = document.getElementById("sources");
  const sourcesHeading = document.getElementById("sources-heading");
  const stepsEl = document.getElementById("steps");
  const noteEl = document.getElementById("note");
  const titleEl = document.getElementById("result-title");
  const liveEl = document.getElementById("live");
  const liveTitleEl = document.getElementById("live-title");
  const liveStateEl = document.getElementById("live-state");
  const liveSearchEl = document.getElementById("live-search");
  const liveFeedEl = document.getElementById("live-feed");
  const liveRetrievedEl = document.getElementById("live-retrieved");
  const liveHitsEl = document.getElementById("live-hits");
  const modelEl = document.getElementById("model");
  const modelWrap = document.getElementById("model-wrap");
  const browseHostedEl = document.getElementById("browse-hosted");
  const briefingBody = document.getElementById("briefing-body");
  const skillsListEl = document.getElementById("skills-list");
  const historyListEl = document.getElementById("history-list");

  let browseAvailable = null; // null = not checked yet

  const PLACEHOLDERS = {
    ask: "e.g. What is Delta Lake medallion architecture?",
    reply: "e.g. Hi, can you explain your refund policy?",
    browse: "e.g. Open BookMyShow, pick 2 seats, stop before payment",
    agent: "e.g. Research top 3 vector DBs and save a comparison markdown",
  };

  const cards = new Map();
  const stepLines = [];
  let sawEvents = false;

  function currentMode() {
    const el = document.querySelector('input[name="mode"]:checked');
    return el ? el.value : "ask";
  }

  function showBrowseHosted() {
    browseHostedEl.classList.remove("hidden");
    browseHostedEl.scrollIntoView({ block: "nearest" });
  }

  function updateModeUI() {
    const mode = currentMode();
    queryEl.placeholder = PLACEHOLDERS[mode] || PLACEHOLDERS.ask;
    webToggleWrap.classList.toggle("hidden", mode === "browse" || mode === "agent");
    modelWrap.classList.toggle("hidden", mode !== "ask" && mode !== "reply");
    browseHint.classList.toggle("hidden", mode !== "browse");
    agentHint.classList.toggle("hidden", mode !== "agent");
    // On hosts without a real browser, Browse gets the explanation card
    // up front instead of a raw error after submit.
    browseHostedEl.classList.toggle(
      "hidden", !(mode === "browse" && browseAvailable === false));
    if (mode === "browse") liveTitleEl.textContent = "🖱️ Browser agent";
    else if (mode === "agent") liveTitleEl.textContent = "🤖 Agent tools";
    else liveTitleEl.textContent = "🌐 Live activity";
  }

  document.querySelectorAll('input[name="mode"]').forEach(function (el) {
    el.addEventListener("change", updateModeUI);
  });
  updateModeUI();

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.remove("hidden");
  }

  function setLiveState(text, cls) {
    liveStateEl.textContent = text;
    liveStateEl.className = "live-state" + (cls ? " " + cls : "");
  }

  function addStep(line) { stepLines.push(line); }

  function resetLive() {
    cards.clear();
    stepLines.length = 0;
    sawEvents = false;
    liveFeedEl.innerHTML = "";
    liveHitsEl.innerHTML = "";
    liveRetrievedEl.classList.add("hidden");
    liveSearchEl.classList.add("hidden");
    liveSearchEl.textContent = "";
    liveEl.classList.remove("hidden");
    setLiveState("starting…", "busy");
  }

  function makeCard(url, title) {
    const card = document.createElement("div");
    card.className = "page-card reading";
    const head = document.createElement("div");
    head.className = "page-head";
    const badge = document.createElement("span");
    badge.className = "page-badge spinner small";
    const a = document.createElement("a");
    a.className = "page-title";
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    a.textContent = title || url;
    head.appendChild(badge); head.appendChild(a);
    const urlEl = document.createElement("div");
    urlEl.className = "page-url"; urlEl.textContent = url;
    const meta = document.createElement("div");
    meta.className = "page-meta"; meta.textContent = "opening…";
    card.appendChild(head); card.appendChild(urlEl); card.appendChild(meta);
    liveFeedEl.appendChild(card);
    const entry = { card: card, meta: meta, badge: badge };
    cards.set(url, entry);
    return entry;
  }

  function cardFor(url, title) { return cards.get(url) || makeCard(url, title); }
  function fmt(n) { return n.toLocaleString("en-US"); }

  function renderAnswer(data, mode) {
    if (mode === "reply") titleEl.textContent = "Draft reply";
    else if (mode === "browse") titleEl.textContent = "Browser result";
    else if (mode === "agent") titleEl.textContent = "Agent result";
    else titleEl.textContent = "Answer";

    answerEl.textContent = data.answer || data.text || "";
    if (data.note) {
      noteEl.textContent = data.note;
      noteEl.classList.remove("hidden");
    } else noteEl.classList.add("hidden");

    const sources = data.sources || [];
    sourcesEl.innerHTML = "";
    if (!sources.length) {
      sourcesHeading.classList.add("hidden");
      sourcesEl.classList.add("hidden");
    } else {
      sourcesHeading.classList.remove("hidden");
      sourcesEl.classList.remove("hidden");
      sources.forEach(function (s) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = s.url; a.target = "_blank"; a.rel = "noopener";
        a.textContent = s.title || s.url;
        li.appendChild(a); sourcesEl.appendChild(li);
      });
    }

    stepsEl.innerHTML = "";
    (data.steps && data.steps.length ? data.steps : stepLines).forEach(function (s) {
      const li = document.createElement("li");
      li.textContent = s;
      stepsEl.appendChild(li);
    });
    resultEl.classList.remove("hidden");
  }

  function appendToolCard(label, detail) {
    const card = document.createElement("div");
    card.className = "page-card done";
    card.innerHTML =
      '<div class="page-head"><span class="page-badge ok">✓</span>' +
      '<span class="page-title">' + label + '</span></div>' +
      (detail ? '<div class="page-meta">' + detail + '</div>' : '');
    liveFeedEl.appendChild(card);
  }

  function handleEvent(ev, mode) {
    sawEvents = true;
    switch (ev.type) {
      case "search":
        setLiveState("searching…", "busy");
        statusTextEl.textContent = "Searching the web…";
        liveSearchEl.textContent = "🔍 Searching for: “" + ev.query + "”";
        liveSearchEl.classList.remove("hidden");
        addStep("searching: " + ev.query);
        break;
      case "results":
        setLiveState("found " + ev.results.length + " pages", "busy");
        addStep("search returned " + ev.results.length + " results");
        break;
      case "local":
        setLiveState("local search…", "busy");
        addStep("local index (" + ev.chunks + " chunks)");
        break;
      case "fetch": {
        const entry = cardFor(ev.url, ev.title);
        entry.meta.textContent = "reading…";
        break;
      }
      case "fetched": {
        const entry = cardFor(ev.url, ev.title);
        entry.card.classList.remove("reading");
        entry.card.classList.add("done");
        entry.badge.className = "page-badge ok";
        entry.badge.textContent = "✓";
        entry.meta.textContent =
          "read " + fmt(ev.chars) + " chars · " + ev.chunks + " chunks";
        addStep("fetched " + (ev.title || ev.url));
        break;
      }
      case "fetch_failed": {
        const entry = cardFor(ev.url, ev.title);
        entry.card.classList.remove("reading");
        entry.card.classList.add("failed");
        entry.badge.className = "page-badge bad";
        entry.badge.textContent = "✗";
        entry.meta.textContent = "skipped — " + (ev.reason || "failed");
        break;
      }
      case "indexed":
        setLiveState("indexing…", "busy");
        addStep("indexed " + ev.chunks + " chunks from " + ev.pages + " pages");
        break;
      case "retrieved": {
        setLiveState("evidence ready", "busy");
        (ev.hits || []).forEach(function (h) {
          const entry = cards.get(h.url);
          if (entry) entry.card.classList.add("used");
        });
        if (ev.hits && ev.hits.length) {
          liveHitsEl.innerHTML = "";
          ev.hits.forEach(function (h) {
            const li = document.createElement("li");
            const a = document.createElement("a");
            a.href = h.url; a.target = "_blank"; a.rel = "noopener";
            a.textContent = h.title || h.url;
            li.appendChild(a);
            const score = document.createElement("span");
            score.className = "hit-score";
            score.textContent = "score " + Number(h.score).toFixed(3);
            li.appendChild(score);
            liveHitsEl.appendChild(li);
          });
          liveRetrievedEl.classList.remove("hidden");
        }
        addStep("retrieved " + (ev.hits || []).length + " chunks");
        break;
      }
      case "generating":
        setLiveState("writing…", "busy");
        statusTextEl.textContent = "Writing the answer…";
        break;

      case "browse_start":
        setLiveState("browser running…", "busy");
        liveSearchEl.textContent = "🖱️ " + (ev.task || "").slice(0, 160);
        liveSearchEl.classList.remove("hidden");
        addStep("browse started");
        break;
      case "browse_done":
        setLiveState("browser finished", "ok");
        addStep("browse done");
        break;

      case "agent_start":
        setLiveState("agent running…", "busy");
        statusTextEl.textContent = "Agent is using tools…";
        liveSearchEl.textContent = "🤖 Task: “" + (ev.task || "").slice(0, 160) + "”";
        liveSearchEl.classList.remove("hidden");
        addStep("agent started");
        break;
      case "agent_thinking":
        setLiveState("step " + ev.step + "/" + ev.max_steps, "busy");
        statusTextEl.textContent = "Thinking (step " + ev.step + ")…";
        break;
      case "tool":
        appendToolCard(
          "Tool: " + (ev.tool || "?"),
          ev.query || ev.url || ev.task || ev.fact || ev.title || ""
        );
        addStep("tool " + ev.tool);
        break;
      case "tool_result":
        addStep("result " + (ev.tool || "") + ": " + (ev.preview || "").slice(0, 80));
        break;
      case "agent_done":
        setLiveState("agent finished", "ok");
        addStep("agent finished");
        break;
      case "skill_active":
        appendToolCard("🧩 Skill active: " + (ev.name || ""), "");
        addStep("skill active: " + (ev.name || ""));
        break;

      case "browse_unavailable":
        showBrowseHosted();
        setLiveState("local only", "bad");
        addStep("browse unavailable on this host — see the explanation card");
        break;
      case "answer":
        renderAnswer(ev, mode);
        break;
      case "error":
        showError(ev.message);
        setLiveState("error", "bad");
        addStep("error: " + ev.message);
        break;
      case "done":
        setLiveState("done", "ok");
        break;
    }
  }

  async function runStream(query, mode) {
    const resp = await fetch("/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: query, mode: mode, use_web: webEl.checked,
        model: modelEl.value,
      }),
    });
    if (!resp.ok || !resp.body) throw new Error("stream failed (" + resp.status + ")");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = raw.split("\n").find(function (l) { return l.startsWith("data:"); });
        if (!line) continue;
        try { handleEvent(JSON.parse(line.slice(5).trim()), mode); }
        catch (e) { /* ignore */ }
      }
    }
    if (!sawEvents) throw new Error("stream ended with no events");
  }

  async function runClassic(query, mode) {
    const resp = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: query, mode: mode, use_web: webEl.checked,
        model: modelEl.value,
      }),
    });
    const data = await resp.json();
    if (data.error_code === "browse_unavailable_hosted") {
      showBrowseHosted();
      return;
    }
    if (!resp.ok && data.error && !data.answer) {
      showError(data.error);
      if (data.note) { noteEl.textContent = data.note; noteEl.classList.remove("hidden"); }
      return;
    }
    renderAnswer(data, mode);
  }

  async function run() {
    const query = queryEl.value.trim();
    errorEl.classList.add("hidden");
    resultEl.classList.add("hidden");
    if (!query) {
      showError("Please enter something first.");
      return;
    }
    const mode = currentMode();
    btn.disabled = true;
    statusEl.classList.remove("hidden");
    statusTextEl.textContent = mode === "agent" ? "Starting agent…" : "Working…";
    resetLive();
    try {
      await runStream(query, mode);
    } catch (err) {
      try {
        await runClassic(query, mode);
        setLiveState("done (non-streamed)", "ok");
      } catch (err2) {
        showError("Request failed: " + err2.message);
        setLiveState("error", "bad");
      }
    } finally {
      btn.disabled = false;
      statusEl.classList.add("hidden");
      loadHistory();
    }
  }

  btn.addEventListener("click", run);
  queryEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
  });

  // ---- Panels loaded on page load ------------------------------------

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  async function checkHealth() {
    try {
      const resp = await fetch("/api/health");
      const data = await resp.json();
      browseAvailable = !!data.browse_available;
    } catch (e) {
      browseAvailable = false;
    }
    updateModeUI();
  }

  async function loadBriefing() {
    try {
      const resp = await fetch("/api/briefing");
      const b = await resp.json();
      if (!resp.ok || b.error) throw new Error(b.error || resp.status);
      briefingBody.innerHTML = "";
      briefingBody.appendChild(el("p", "briefing-date",
        "☀️ " + (b.date || "") + (b.city ? " · " + b.city : "")));
      if (b.weather) {
        const w = b.weather;
        const rain = (w.rain_chance == null) ? "rain n/a"
          : "rain " + Math.round(w.rain_chance) + "%";
        briefingBody.appendChild(el("p", null,
          "🌡️ " + w.temp_min + "°C – " + w.temp_max + "°C, " +
          w.conditions + ", " + rain));
      } else {
        briefingBody.appendChild(el("p", "muted", "🌡️ Weather unavailable right now."));
      }
      if (b.reminders_due && b.reminders_due.length) {
        const p = el("p", null, "⏰ Due: ");
        p.appendChild(el("span", null, b.reminders_due.map(function (r) {
          return r.text + " (" + r.due + ")";
        }).join("; ")));
        briefingBody.appendChild(p);
      }
      if (b.goals && b.goals.length) {
        const p = el("p", null, "🎯 Goals: ");
        p.appendChild(el("span", null, b.goals.map(function (g) {
          return g.title;
        }).join(" · ")));
        briefingBody.appendChild(p);
      }
      if (b.memory_facts && b.memory_facts.length) {
        const ul = el("ul", "briefing-facts");
        b.memory_facts.forEach(function (f) {
          ul.appendChild(el("li", null, f));
        });
        briefingBody.appendChild(ul);
      }
      if (b.artifacts && b.artifacts.length) {
        briefingBody.appendChild(el("p", "muted",
          "🗂️ Latest artifacts: " + b.artifacts.map(function (a) {
            return a.title;
          }).join(" · ")));
      }
    } catch (e) {
      briefingBody.innerHTML = "";
      briefingBody.appendChild(el("p", "muted", "Briefing unavailable right now."));
    }
  }

  async function loadSkills() {
    try {
      const resp = await fetch("/api/skills");
      const data = await resp.json();
      skillsListEl.innerHTML = "";
      (data.skills || []).forEach(function (s) {
        const li = el("li");
        li.appendChild(el("strong", null, s.name));
        li.appendChild(el("span", null, " — " + (s.description || "")));
        skillsListEl.appendChild(li);
      });
      if (!skillsListEl.children.length) {
        skillsListEl.appendChild(el("li", "muted", "No skills installed."));
      }
    } catch (e) { /* panel stays empty */ }
  }

  async function loadHistory() {
    try {
      const resp = await fetch("/api/history");
      const data = await resp.json();
      historyListEl.innerHTML = "";
      (data.history || []).forEach(function (entry) {
        const li = el("li", "history-item");
        const when = entry.timestamp
          ? new Date(entry.timestamp).toLocaleString() : "";
        li.appendChild(el("span", "history-meta",
          "[" + entry.mode + "] " + when + " — "));
        li.appendChild(el("span", "history-q", entry.query));
        li.title = "Click to restore this answer";
        li.addEventListener("click", function () {
          stepLines.length = 0;
          renderAnswer(
            { answer: entry.answer, sources: entry.sources || [], steps: [] },
            entry.mode);
          resultEl.scrollIntoView({ block: "nearest" });
        });
        historyListEl.appendChild(li);
      });
      if (!historyListEl.children.length) {
        historyListEl.appendChild(el("li", "muted", "No runs yet."));
      }
    } catch (e) { /* panel stays empty */ }
  }

  checkHealth();
  loadBriefing();
  loadSkills();
  loadHistory();
})();
