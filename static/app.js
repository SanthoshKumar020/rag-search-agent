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
      loadActivity();
      loadMemory();
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

  // ---- Voice (Web Speech API, client-side only) ---------------------

  const micBtn = document.getElementById("mic");
  const voiceStatus = document.getElementById("voice-status");
  const listenBtn = document.getElementById("listen");
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRec) {
    micBtn.disabled = true;
    voiceStatus.textContent = "Voice input isn't supported in this browser.";
  } else {
    let recognising = false;
    const rec = new SpeechRec();
    rec.lang = "en-IN";
    rec.interimResults = false;
    rec.addEventListener("result", function (e) {
      const transcript = e.results[0][0].transcript;
      queryEl.value = transcript;
      voiceStatus.textContent = "Heard: “" + transcript + "”";
      queryEl.focus();
    });
    rec.addEventListener("end", function () {
      recognising = false;
      micBtn.textContent = "🎤 Speak";
    });
    rec.addEventListener("error", function (e) {
      voiceStatus.textContent = "Voice error: " + e.error;
    });
    micBtn.addEventListener("click", function () {
      if (recognising) { rec.stop(); return; }
      try {
        rec.start();
        recognising = true;
        micBtn.textContent = "⏹️ Stop";
        voiceStatus.textContent = "Listening… speak now.";
      } catch (err) { /* already running */ }
    });
  }

  if (!("speechSynthesis" in window)) {
    listenBtn.classList.add("hidden");
  } else {
    // reveal the Listen button whenever an answer is rendered
    const origRender = renderAnswer;
    renderAnswer = function (data, mode) {
      origRender(data, mode);
      listenBtn.classList.remove("hidden");
    };
    listenBtn.addEventListener("click", function () {
      if (window.speechSynthesis.speaking) {
        window.speechSynthesis.cancel();
        listenBtn.textContent = "🔊 Listen";
        return;
      }
      const text = (answerEl.textContent || "").slice(0, 1500);
      if (!text) return;
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = "en-IN";
      utter.addEventListener("end", function () {
        listenBtn.textContent = "🔊 Listen";
      });
      listenBtn.textContent = "⏹️ Stop";
      window.speechSynthesis.speak(utter);
    });
  }

  // ---- Live cards ----------------------------------------------------

  async function loadCards() {
    const wEl = document.getElementById("card-weather");
    const fxEl = document.getElementById("card-fx");
    const nEl = document.getElementById("card-news");
    try {
      const resp = await fetch("/api/cards");
      const c = await resp.json();
      wEl.innerHTML = "";
      wEl.appendChild(el("div", "mini-title", "🌡️ Weather · " + (c.city || "")));
      if (c.weather) {
        wEl.appendChild(el("p", null,
          c.weather.temp_min + "°C – " + c.weather.temp_max + "°C, " +
          c.weather.conditions +
          (c.weather.rain_chance == null ? "" :
            " · rain " + Math.round(c.weather.rain_chance) + "%")));
      } else wEl.appendChild(el("p", "muted", "Unavailable"));
      fxEl.innerHTML = "";
      fxEl.appendChild(el("div", "mini-title", "💱 USD → INR"));
      if (c.fx) {
        fxEl.appendChild(el("p", null,
          "1 USD = ₹" + Number(c.fx.rate).toFixed(2)));
        fxEl.appendChild(el("p", "muted mini-sub", c.fx.updated || ""));
      } else fxEl.appendChild(el("p", "muted", "Unavailable"));
      nEl.innerHTML = "";
      nEl.appendChild(el("div", "mini-title", "📰 Top headlines"));
      const ul = el("ul", "mini-news");
      (c.headlines || []).forEach(function (h) {
        const li = el("li");
        const a = document.createElement("a");
        a.href = h.link; a.target = "_blank"; a.rel = "noopener";
        a.textContent = h.title;
        li.appendChild(a);
        ul.appendChild(li);
      });
      if (!ul.children.length) ul.appendChild(el("li", "muted", "Unavailable"));
      nEl.appendChild(ul);
    } catch (e) { /* cards stay in loading state */ }
  }

  // ---- Ideas ----------------------------------------------------------

  async function loadIdeas() {
    const list = document.getElementById("ideas-list");
    try {
      const resp = await fetch("/api/ideas");
      const data = await resp.json();
      list.innerHTML = "";
      (data.ideas || []).forEach(function (idea) {
        const li = el("li", "idea-item");
        li.appendChild(el("span", null, idea));
        const b = el("button", "small-btn", "Run this");
        b.type = "button";
        b.addEventListener("click", function () {
          const radio = document.querySelector(
            'input[name="mode"][value="agent"]');
          if (radio) { radio.checked = true; updateModeUI(); }
          queryEl.value = idea;
          window.scrollTo({ top: 0, behavior: "smooth" });
          queryEl.focus();
        });
        li.appendChild(b);
        list.appendChild(li);
      });
      if (!list.children.length) {
        list.appendChild(el("li", "muted", "No ideas right now."));
      }
    } catch (e) { /* panel stays empty */ }
  }

  // ---- Memory panel ----------------------------------------------------

  const memoryListEl = document.getElementById("memory-list");
  const memoryInput = document.getElementById("memory-input");
  const memoryKind = document.getElementById("memory-kind");
  const memoryMsg = document.getElementById("memory-msg");

  function renderMemory(data) {
    memoryListEl.innerHTML = "";
    (data.lines || []).forEach(function (line) {
      const li = el("li", "memory-item");
      li.appendChild(el("span", null, line));
      const b = el("button", "del-btn", "✕");
      b.type = "button";
      b.title = "Forget this";
      b.addEventListener("click", async function () {
        try {
          const resp = await fetch("/api/memory/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ line: line }),
          });
          const d = await resp.json();
          memoryMsg.textContent = d.result || "";
          renderMemory(d);
        } catch (e) { memoryMsg.textContent = "Delete failed."; }
      });
      li.appendChild(b);
      memoryListEl.appendChild(li);
    });
    if (!memoryListEl.children.length) {
      memoryListEl.appendChild(el("li", "muted", "Memory is empty."));
    }
  }

  async function loadMemory() {
    try {
      const resp = await fetch("/api/memory");
      renderMemory(await resp.json());
    } catch (e) { /* panel stays empty */ }
  }

  document.getElementById("memory-add").addEventListener("click", async function () {
    const fact = memoryInput.value.trim();
    if (!fact) { memoryMsg.textContent = "Type something first."; return; }
    try {
      const resp = await fetch("/api/memory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fact: fact, kind: memoryKind.value }),
      });
      const d = await resp.json();
      memoryMsg.textContent = d.result || d.error || "";
      if (resp.ok) { memoryInput.value = ""; renderMemory(d); }
    } catch (e) { memoryMsg.textContent = "Could not save."; }
  });

  // ---- Create-a-skill form ----------------------------------------------

  document.getElementById("skill-create").addEventListener("click", async function () {
    const msg = document.getElementById("skill-msg");
    const payload = {
      name: document.getElementById("skill-name").value.trim(),
      description: document.getElementById("skill-desc").value.trim(),
      instructions: document.getElementById("skill-instructions").value.trim(),
    };
    try {
      const resp = await fetch("/api/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await resp.json();
      if (!resp.ok) { msg.textContent = d.error || "Could not save skill."; return; }
      msg.textContent = "Saved skill: " + d.created.name;
      document.getElementById("skill-name").value = "";
      document.getElementById("skill-desc").value = "";
      document.getElementById("skill-instructions").value = "";
      loadSkills();
    } catch (e) { msg.textContent = "Could not save skill."; }
  });

  // ---- Activity panel -----------------------------------------------------

  async function loadActivity() {
    const list = document.getElementById("activity-list");
    try {
      const resp = await fetch("/api/activity");
      const data = await resp.json();
      list.innerHTML = "";
      (data.activity || []).forEach(function (a) {
        const when = a.timestamp ? new Date(a.timestamp).toLocaleString() : "";
        const li = el("li", "history-item");
        li.appendChild(el("span", "history-meta", when + " — "));
        li.appendChild(el("span", null, a.task || ""));
        if (a.tools_used && a.tools_used.length) {
          li.appendChild(el("span", "history-meta",
            "  · tools: " + a.tools_used.join(", ")));
        }
        list.appendChild(li);
      });
      if (!list.children.length) {
        list.appendChild(el("li", "muted", "No agent runs yet."));
      }
    } catch (e) { /* panel stays empty */ }
  }

  // ---- Scheduled tasks panel (v3) ---------------------------------------

  const tasksListEl = document.getElementById("tasks-list");
  const taskMsg = document.getElementById("task-msg");

  function renderTasks(tasks) {
    tasksListEl.innerHTML = "";
    (tasks || []).forEach(function (t) {
      const li = el("li", "history-item");
      const when = t.run_at ? new Date(t.run_at).toLocaleString("en-IN") : "";
      li.appendChild(el("span", "history-meta",
        when + " · " + (t.status || "") + " — "));
      li.appendChild(el("span", null, t.instruction || ""));
      if (t.status === "pending") {
        const runBtn = el("button", "del-btn", "▶");
        runBtn.type = "button";
        runBtn.title = "Run now";
        runBtn.addEventListener("click", async function () {
          taskMsg.textContent = "Running task…";
          try {
            const resp = await fetch("/api/tasks/run", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id: t.id }),
            });
            const d = await resp.json();
            taskMsg.textContent = d.error || ("Done: " + (d.answer || "").slice(0, 160));
            loadTasks();
            loadActivity();
          } catch (e) { taskMsg.textContent = "Run failed."; }
        });
        li.appendChild(runBtn);
        const cancelBtn = el("button", "del-btn", "✕");
        cancelBtn.type = "button";
        cancelBtn.title = "Cancel task";
        cancelBtn.addEventListener("click", async function () {
          try {
            const resp = await fetch("/api/tasks/cancel", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id: t.id }),
            });
            const d = await resp.json();
            if (d.tasks) renderTasks(d.tasks);
          } catch (e) { /* ignore */ }
        });
        li.appendChild(cancelBtn);
      }
      if (t.result) {
        li.appendChild(el("div", "history-meta", "→ " + t.result.slice(0, 200)));
      }
      tasksListEl.appendChild(li);
    });
    if (!tasksListEl.children.length) {
      tasksListEl.appendChild(el("li", "muted", "No scheduled tasks yet."));
    }
  }

  async function loadTasks() {
    try {
      const resp = await fetch("/api/tasks");
      const data = await resp.json();
      renderTasks(data.tasks);
    } catch (e) { /* panel stays empty */ }
  }

  document.getElementById("task-add").addEventListener("click", async function () {
    const instruction = document.getElementById("task-instruction").value.trim();
    const when = document.getElementById("task-when").value.trim();
    if (!instruction || !when) {
      taskMsg.textContent = "Type an instruction and a time (e.g. tomorrow 11:00 am).";
      return;
    }
    try {
      const resp = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: instruction, run_at: when }),
      });
      const d = await resp.json();
      if (!resp.ok) { taskMsg.textContent = d.error || "Could not schedule."; return; }
      taskMsg.textContent = "Scheduled for " +
        new Date(d.created.run_at).toLocaleString("en-IN") + " IST.";
      document.getElementById("task-instruction").value = "";
      document.getElementById("task-when").value = "";
      renderTasks(d.tasks);
    } catch (e) { taskMsg.textContent = "Could not schedule."; }
  });

  // ---- Mood / friend mode (v3) -------------------------------------------

  const moodResults = document.getElementById("mood-results");
  const MOOD_LINES = {
    sad: "Hey… it's okay to feel low sometimes. Here are some gentle picks for you 💛",
    stressed: "Take a breath — you've got a lot on. Let these help you unwind 💛",
    lonely: "You're not alone — I'm right here. Something warm for company 💛",
    happy: "Love to see it! Let's keep the good mood rolling 🎉",
    romantic: "Ooh, someone's in a love mood ❤️ These are for you:",
    energetic: "Full power mode! ⚡ Here's your fuel:",
    bored: "Boredom ends now — these will hook you:",
    relaxed: "Stay in that calm zone 😌 — perfect picks to match:",
  };

  document.querySelectorAll(".mood-btn").forEach(function (b) {
    b.addEventListener("click", async function () {
      const mood = b.getAttribute("data-mood");
      moodResults.innerHTML = "";
      moodResults.appendChild(el("p", null, MOOD_LINES[mood] || "Here you go:"));
      moodResults.appendChild(el("p", "muted", "Finding songs, shows and movies…"));
      try {
        const resp = await fetch("/api/mood?mood=" + encodeURIComponent(mood));
        const d = await resp.json();
        moodResults.innerHTML = "";
        moodResults.appendChild(el("p", null, MOOD_LINES[mood] || "Here you go:"));
        if (d.songs && d.songs.length) {
          moodResults.appendChild(el("h3", null, "🎵 Songs"));
          const ul = el("ul", "history-list");
          d.songs.forEach(function (s) {
            const li = el("li", "history-item");
            const a = el("a", null, s.title + (s.artist ? " — " + s.artist : ""));
            a.href = s.url || "#";
            a.target = "_blank";
            a.rel = "noopener";
            li.appendChild(a);
            if (s.preview) {
              const p = el("a", "history-meta", "  ▶ preview");
              p.href = s.preview;
              p.target = "_blank";
              p.rel = "noopener";
              li.appendChild(p);
            }
            ul.appendChild(li);
          });
          moodResults.appendChild(ul);
        }
        if (d.shows && d.shows.length) {
          moodResults.appendChild(el("h3", null, "📺 Shows"));
          const ul2 = el("ul", "history-list");
          d.shows.forEach(function (s) {
            const li = el("li", "history-item");
            const a = el("a", null, s.title);
            a.href = s.url || "#";
            a.target = "_blank";
            a.rel = "noopener";
            li.appendChild(a);
            if (s.summary) {
              li.appendChild(el("span", "history-meta", " — " + s.summary.slice(0, 140)));
            }
            ul2.appendChild(li);
          });
          moodResults.appendChild(ul2);
        }
        if (d.movies && d.movies.length) {
          moodResults.appendChild(el("h3", null, "🎬 Movie lists"));
          const ul3 = el("ul", "history-list");
          d.movies.forEach(function (m) {
            const li = el("li", "history-item");
            const a = el("a", null, m.title);
            a.href = m.url || "#";
            a.target = "_blank";
            a.rel = "noopener";
            li.appendChild(a);
            ul3.appendChild(li);
          });
          moodResults.appendChild(ul3);
        }
        if (d.note) moodResults.appendChild(el("p", "muted", d.note));
      } catch (e) {
        moodResults.appendChild(el("p", "muted", "Could not fetch suggestions right now."));
      }
    });
  });

  checkHealth();
  loadBriefing();
  loadCards();
  loadIdeas();
  loadMemory();
  loadSkills();
  loadHistory();
  loadActivity();
  loadTasks();
})();
