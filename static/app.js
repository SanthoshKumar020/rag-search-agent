/* Personal Agent dashboard (v5).
   Layout: sidebar (modes + panel nav) · main column (conversation
   thread with a streamed step timeline + composer) · tabbed panels.
   Agent runs stream over SSE; act-tier tools pause for an
   Approve/Deny modal backed by /api/approvals. */
(function () {
  "use strict";

  // ---------------- helpers ----------------
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function fmt(n) { return Number(n).toLocaleString("en-US"); }
  function timeAgo(iso) {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    const secs = Math.max(0, (Date.now() - then) / 1000);
    if (secs < 45) return "just now";
    if (secs < 3600) return Math.floor(secs / 60) + "m ago";
    if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
    if (secs < 86400 * 7) return Math.floor(secs / 86400) + "d ago";
    return new Date(iso).toLocaleDateString();
  }
  function toast(msg, kind) {
    const box = $("toasts");
    const t = el("div", "toast" + (kind ? " " + kind : ""), msg);
    box.appendChild(t);
    setTimeout(function () { t.remove(); }, 4600);
  }
  async function api(path, opts) {
    const resp = await fetch(path, opts || {});
    let data = {};
    try { data = await resp.json(); } catch (e) { /* empty body */ }
    return { resp: resp, data: data };
  }
  function postJSON(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }
  function tierBadge(tier) {
    if (!tier) return null;
    return el("span", "tier-badge tier-" + tier, tier);
  }
  function pill(status) {
    return el("span", "pill " + (status || ""), status || "");
  }

  // ---------------- theme ----------------
  const themeToggle = $("theme-toggle");
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") || "dark";
  }
  function applyTheme(theme, persist) {
    document.documentElement.setAttribute("data-theme", theme);
    themeToggle.textContent = theme === "light" ? "☀️ Light" : "🌙 Dark";
    const sel = $("settings-theme");
    if (sel) sel.value = theme;
    if (persist) {
      try { localStorage.setItem("pa-theme", theme); } catch (e) {}
      postJSON("/api/settings", { theme: theme }).catch(function () {});
    }
  }
  themeToggle.addEventListener("click", function () {
    applyTheme(currentTheme() === "light" ? "dark" : "light", true);
  });
  applyTheme(currentTheme(), false);

  // ---------------- modes ----------------
  const queryEl = $("query");
  const webEl = $("use-web");
  const modelEl = $("model");
  let browseAvailable = null;

  const PLACEHOLDERS = {
    ask: "e.g. What is Delta Lake medallion architecture?",
    reply: "e.g. Hi, can you explain your refund policy?",
    browse: "e.g. Open BookMyShow, pick 2 seats, stop before payment",
    agent: "e.g. Research top 3 vector DBs and save a comparison markdown",
    friend: "e.g. I feel sad… or just talk to me",
  };
  const MODE_TITLES = {
    ask: "Answer", reply: "Draft reply", browse: "Browser result",
    agent: "Agent result", friend: "💛 Friend",
  };

  function currentMode() {
    const btn = document.querySelector(".mode-btn.active");
    return btn ? btn.getAttribute("data-mode") : "ask";
  }
  function setMode(mode) {
    document.querySelectorAll(".mode-btn").forEach(function (b) {
      const on = b.getAttribute("data-mode") === mode;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    updateModeUI();
  }
  function updateModeUI() {
    const mode = currentMode();
    queryEl.placeholder = PLACEHOLDERS[mode] || PLACEHOLDERS.ask;
    $("web-toggle-wrap").classList.toggle(
      "hidden", mode === "browse" || mode === "agent" || mode === "friend");
    $("model-wrap").classList.toggle(
      "hidden", mode !== "ask" && mode !== "reply");
    $("browse-hint").classList.toggle("hidden", mode !== "browse");
    $("agent-hint").classList.toggle("hidden", mode !== "agent");
    $("friend-view").classList.toggle("hidden", mode !== "friend");
    $("browse-hosted").classList.toggle(
      "hidden", !(mode === "browse" && browseAvailable === false));
    if (mode === "friend") {
      const h = new Date().getHours();
      $("friend-greeting").textContent =
        (h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening") + " 💛";
    }
  }
  document.querySelectorAll(".mode-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      setMode(b.getAttribute("data-mode"));
      queryEl.focus();
    });
  });

  // ---------------- panels ----------------
  function showPanel(name, scroll) {
    document.querySelectorAll(".panel-tab").forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-panel") === name);
    });
    document.querySelectorAll(".panel-link").forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-panel") === name);
    });
    document.querySelectorAll("[data-panel-body]").forEach(function (p) {
      p.classList.toggle("active", p.getAttribute("data-panel-body") === name);
    });
    if (scroll && window.innerWidth <= 900) {
      $("panels").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
  document.querySelectorAll(".panel-tab").forEach(function (t) {
    t.addEventListener("click", function () {
      showPanel(t.getAttribute("data-panel"), false);
    });
  });
  document.querySelectorAll(".panel-link").forEach(function (t) {
    t.addEventListener("click", function () {
      showPanel(t.getAttribute("data-panel"), true);
    });
  });

  // ---------------- approvals ----------------
  let approvalShownFor = null; // approval id currently in the modal
  let approvalPoll = null;

  function openApproval(a) {
    if (!a || !a.id || approvalShownFor === a.id) return;
    approvalShownFor = a.id;
    $("approval-tool").textContent = a.tool || "tool";
    $("approval-args").textContent = a.args || "(no arguments)";
    $("approval-modal").classList.remove("hidden");
  }
  function closeApproval() {
    approvalShownFor = null;
    $("approval-modal").classList.add("hidden");
  }
  async function decideApproval(approved) {
    const id = approvalShownFor;
    if (!id) return;
    closeApproval();
    try {
      const r = await postJSON("/api/approvals/" + encodeURIComponent(id) + "/decide",
        { approved: approved });
      if (!r.resp.ok) toast(r.data.error || "Approval already resolved.", "error");
    } catch (e) {
      toast("Could not send the decision.", "error");
    }
  }
  $("approval-approve").addEventListener("click", function () { decideApproval(true); });
  $("approval-deny").addEventListener("click", function () { decideApproval(false); });

  function startApprovalPolling() {
    stopApprovalPolling();
    approvalPoll = setInterval(async function () {
      try {
        const r = await api("/api/approvals");
        const pending = (r.data.approvals || [])[0];
        if (pending) openApproval(pending);
        else if (approvalShownFor) closeApproval();
      } catch (e) { /* transient */ }
    }, 1500);
  }
  function stopApprovalPolling() {
    if (approvalPoll) { clearInterval(approvalPoll); approvalPoll = null; }
    closeApproval();
  }

  // ---------------- conversation turns ----------------
  const thread = $("thread");
  const statusEl = $("status");
  const statusTextEl = $("status-text");
  const errorEl = $("error");
  const goBtn = $("go");

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.remove("hidden");
  }

  function newTurn(query, mode) {
    const empty = $("empty-state");
    if (empty) empty.remove();
    const turn = el("div", "turn");
    const userMsg = el("div", "msg user");
    userMsg.appendChild(el("div", null, query));
    userMsg.appendChild(el("span", "msg-meta",
      mode + " · " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })));
    turn.appendChild(userMsg);

    const live = el("div", "live-block");
    const head = el("div", "live-head");
    const title = el("span", "live-title",
      mode === "browse" ? "🖱️ Browser agent"
        : (mode === "agent" || mode === "friend") ? "🤖 Agent tools"
        : "🌐 Live activity");
    const state = el("span", "pill busy", "starting…");
    head.appendChild(title); head.appendChild(state);
    const searchLine = el("p", "live-search hidden");
    const timeline = el("div", "timeline");
    const feed = el("div", "feed");
    const retrieved = el("div", "live-retrieved hidden");
    retrieved.appendChild(el("h4", null, "⭐ Used as evidence"));
    const hitsList = el("ul");
    retrieved.appendChild(hitsList);
    live.appendChild(head); live.appendChild(searchLine);
    live.appendChild(timeline); live.appendChild(feed);
    live.appendChild(retrieved);
    turn.appendChild(live);
    thread.appendChild(turn);
    turn.scrollIntoView({ behavior: "smooth", block: "start" });

    return {
      turn: turn, live: live, state: state, searchLine: searchLine,
      timeline: timeline, feed: feed, retrieved: retrieved,
      hitsList: hitsList, cards: new Map(), rows: [],
      stepLines: [], sawEvents: false, mode: mode,
    };
  }

  function setState(view, text, cls) {
    view.state.textContent = text;
    view.state.className = "pill" + (cls ? " " + cls : "");
  }
  function addStepLine(view, line) { view.stepLines.push(line); }

  function makePageCard(view, url, title) {
    const card = el("div", "page-card reading");
    const head = el("div", "page-head");
    const badge = el("span", "page-badge spinner small");
    const a = el("a", "page-title", title || url);
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    head.appendChild(badge); head.appendChild(a);
    const urlEl = el("div", "page-url", url);
    const meta = el("div", "page-meta", "opening…");
    card.appendChild(head); card.appendChild(urlEl); card.appendChild(meta);
    view.feed.appendChild(card);
    const entry = { card: card, meta: meta, badge: badge };
    view.cards.set(url, entry);
    return entry;
  }
  function cardFor(view, url, title) {
    return view.cards.get(url) || makePageCard(view, url, title);
  }

  function addToolRow(view, tool, tier, detail) {
    const row = el("div", "step-row");
    const ico = el("span", "step-ico");
    ico.appendChild(el("span", "spinner small"));
    const main = el("div", "step-main");
    const nameLine = el("span");
    nameLine.appendChild(el("span", "step-name", tool));
    const badge = tierBadge(tier);
    if (badge) nameLine.appendChild(badge);
    main.appendChild(nameLine);
    if (detail) main.appendChild(el("div", "step-detail", detail));
    const preview = el("div", "step-preview hidden");
    main.appendChild(preview);
    row.appendChild(ico); row.appendChild(main);
    view.timeline.appendChild(row);
    const rec = { tool: tool, row: row, ico: ico, preview: preview, done: false };
    view.rows.push(rec);
    return rec;
  }
  function finishToolRow(view, tool, previewText, blocked) {
    for (let i = view.rows.length - 1; i >= 0; i--) {
      const rec = view.rows[i];
      if (rec.tool === tool && !rec.done) {
        rec.done = true;
        rec.ico.textContent = blocked ? "🚫" : "✓";
        if (blocked) rec.row.classList.add("blocked");
        if (previewText) {
          rec.preview.textContent = previewText.slice(0, 400);
          rec.preview.classList.remove("hidden");
        }
        return rec;
      }
    }
    // no open row (e.g. classic fallback) — add a completed one
    const rec = addToolRow(view, tool, null, "");
    rec.done = true;
    rec.ico.textContent = blocked ? "🚫" : "✓";
    if (blocked) rec.row.classList.add("blocked");
    if (previewText) {
      rec.preview.textContent = previewText.slice(0, 400);
      rec.preview.classList.remove("hidden");
    }
    return rec;
  }

  function renderAnswer(view, data, mode) {
    const msg = el("div", "msg assistant");
    const head = el("div", "result-head");
    head.appendChild(el("h3", null, MODE_TITLES[mode] || "Answer"));
    if ("speechSynthesis" in window) {
      const listen = el("button", "small-btn", "🔊 Listen");
      listen.type = "button";
      listen.addEventListener("click", function () {
        if (window.speechSynthesis.speaking) {
          window.speechSynthesis.cancel();
          listen.textContent = "🔊 Listen";
          return;
        }
        const utter = new SpeechSynthesisUtterance(
          (data.answer || data.text || "").slice(0, 1500));
        utter.lang = "en-IN";
        utter.addEventListener("end", function () {
          listen.textContent = "🔊 Listen";
        });
        listen.textContent = "⏹️ Stop";
        window.speechSynthesis.speak(utter);
      });
      head.appendChild(listen);
    }
    msg.appendChild(head);
    if (data.note) msg.appendChild(el("p", "note", data.note));
    msg.appendChild(el("div", "answer", data.answer || data.text || ""));

    const sources = data.sources || [];
    if (sources.length) {
      const wrap = el("div", "sources-wrap");
      wrap.appendChild(el("h4", null, "Sources"));
      const ol = el("ol", "sources");
      sources.forEach(function (s) {
        const li = el("li");
        const a = el("a", null, s.title || s.url);
        a.href = s.url; a.target = "_blank"; a.rel = "noopener";
        li.appendChild(a); ol.appendChild(li);
      });
      wrap.appendChild(ol);
      msg.appendChild(wrap);
    }
    const steps = (data.steps && data.steps.length) ? data.steps : view.stepLines;
    if (steps.length) {
      const box = el("details", "steps-box");
      box.appendChild(el("summary", null, "What the agent did"));
      const ul = el("ul");
      steps.forEach(function (s) { ul.appendChild(el("li", null, s)); });
      box.appendChild(ul);
      msg.appendChild(box);
    }
    view.turn.appendChild(msg);
    msg.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ---------------- SSE event handling ----------------
  function handleEvent(view, ev) {
    view.sawEvents = true;
    const mode = view.mode;
    switch (ev.type) {
      case "search":
        setState(view, "searching…", "busy");
        statusTextEl.textContent = "Searching the web…";
        view.searchLine.textContent = "🔍 Searching for: “" + ev.query + "”";
        view.searchLine.classList.remove("hidden");
        addStepLine(view, "searching: " + ev.query);
        break;
      case "results":
        setState(view, "found " + ev.results.length + " pages", "busy");
        addStepLine(view, "search returned " + ev.results.length + " results");
        break;
      case "local":
        setState(view, "local search…", "busy");
        addStepLine(view, "local index (" + ev.chunks + " chunks)");
        break;
      case "fetch": {
        const entry = cardFor(view, ev.url, ev.title);
        entry.meta.textContent = "reading…";
        break;
      }
      case "fetched": {
        const entry = cardFor(view, ev.url, ev.title);
        entry.card.classList.remove("reading");
        entry.badge.className = "page-badge ok";
        entry.badge.textContent = "✓";
        entry.meta.textContent =
          "read " + fmt(ev.chars) + " chars · " + ev.chunks + " chunks";
        addStepLine(view, "fetched " + (ev.title || ev.url));
        break;
      }
      case "fetch_failed": {
        const entry = cardFor(view, ev.url, ev.title);
        entry.card.classList.remove("reading");
        entry.card.classList.add("failed");
        entry.badge.className = "page-badge bad";
        entry.badge.textContent = "✗";
        entry.meta.textContent = "skipped — " + (ev.reason || "failed");
        break;
      }
      case "indexed":
        setState(view, "indexing…", "busy");
        addStepLine(view, "indexed " + ev.chunks + " chunks from " + ev.pages + " pages");
        break;
      case "retrieved": {
        setState(view, "evidence ready", "busy");
        (ev.hits || []).forEach(function (h) {
          const entry = view.cards.get(h.url);
          if (entry) entry.card.classList.add("used");
        });
        if (ev.hits && ev.hits.length) {
          view.hitsList.innerHTML = "";
          ev.hits.forEach(function (h) {
            const li = el("li");
            const a = el("a", null, h.title || h.url);
            a.href = h.url; a.target = "_blank"; a.rel = "noopener";
            li.appendChild(a);
            li.appendChild(el("span", "hit-score",
              "score " + Number(h.score).toFixed(3)));
            view.hitsList.appendChild(li);
          });
          view.retrieved.classList.remove("hidden");
        }
        addStepLine(view, "retrieved " + (ev.hits || []).length + " chunks");
        break;
      }
      case "generating":
        setState(view, "writing…", "busy");
        statusTextEl.textContent = "Writing the answer…";
        break;
      case "browse_start":
        setState(view, "browser running…", "busy");
        view.searchLine.textContent = "🖱️ " + (ev.task || "").slice(0, 160);
        view.searchLine.classList.remove("hidden");
        addStepLine(view, "browse started");
        break;
      case "browse_done":
        setState(view, "browser finished", "ok");
        addStepLine(view, "browse done");
        break;
      case "agent_start":
        setState(view, "agent running…", "busy");
        statusTextEl.textContent = "Agent is using tools…";
        view.searchLine.textContent = "🤖 Task: “" + (ev.task || "").slice(0, 160) + "”";
        view.searchLine.classList.remove("hidden");
        addStepLine(view, "agent started");
        break;
      case "agent_thinking":
        setState(view, "step " + ev.step + "/" + ev.max_steps, "busy");
        statusTextEl.textContent = "Thinking (step " + ev.step + ")…";
        break;
      case "tool":
        addToolRow(view, ev.tool || "?",
          ev.tier || null,
          ev.query || ev.url || ev.task || ev.fact || ev.title || "");
        addStepLine(view, "tool " + ev.tool);
        break;
      case "tool_result":
        finishToolRow(view, ev.tool || "", ev.preview || "", !!ev.blocked);
        addStepLine(view, "result " + (ev.tool || "") + ": " +
          (ev.preview || "").slice(0, 80));
        break;
      case "policy_block": {
        const rec = addToolRow(view, ev.tool || "?", ev.tier || null,
          "blocked by policy");
        rec.done = true;
        rec.ico.textContent = "🚫";
        rec.row.classList.add("blocked");
        rec.preview.textContent = (ev.reason || "").slice(0, 300);
        rec.preview.classList.remove("hidden");
        addStepLine(view, "policy blocked " + ev.tool);
        break;
      }
      case "approval_request": {
        const rec = addToolRow(view, ev.tool || "?", ev.tier || "act",
          "waiting for your approval…");
        rec.row.classList.add("waiting");
        rec.ico.textContent = "⏸";
        rec.waiting = true;
        openApproval(ev);
        setState(view, "⏸ approval needed", "busy");
        statusTextEl.textContent = "Waiting for your approval…";
        addStepLine(view, "approval requested: " + ev.tool);
        break;
      }
      case "approval_decided": {
        closeApproval();
        for (let i = view.rows.length - 1; i >= 0; i--) {
          if (view.rows[i].waiting) {
            view.rows[i].waiting = false;
            view.rows[i].row.classList.remove("waiting");
            if (!ev.approved) {
              view.rows[i].done = true;
              view.rows[i].row.classList.add("blocked");
              view.rows[i].ico.textContent = "🚫";
            } else {
              view.rows[i].ico.innerHTML = "";
              view.rows[i].ico.appendChild(el("span", "spinner small"));
            }
            break;
          }
        }
        toast(ev.approved ? "Approved — running the step." : "Denied — the agent will adapt.",
          ev.approved ? "ok" : "error");
        addStepLine(view, "approval " + (ev.approved ? "granted" : "denied") +
          ": " + ev.tool);
        break;
      }
      case "skill_active": {
        const rec = addToolRow(view, "🧩 skill: " + (ev.name || ""), null, "");
        rec.done = true; rec.ico.textContent = "🧩";
        addStepLine(view, "skill active: " + (ev.name || ""));
        break;
      }
      case "skill_proposed":
        toast("🧩 New skill proposal: “" + (ev.name || "") +
          "” — review it in the Skills panel.");
        loadProposals();
        addStepLine(view, "skill proposed: " + (ev.name || ""));
        break;
      case "browse_unavailable":
        showBrowseHosted();
        setState(view, "local only", "bad");
        addStepLine(view, "browse unavailable on this host");
        break;
      case "answer":
        renderAnswer(view, ev, mode);
        break;
      case "error":
        showError(ev.message);
        setState(view, "error", "bad");
        addStepLine(view, "error: " + ev.message);
        break;
      case "done":
        if (view.state.textContent !== "error") setState(view, "done", "ok");
        break;
    }
  }

  function showBrowseHosted() {
    $("browse-hosted").classList.remove("hidden");
    $("browse-hosted").scrollIntoView({ block: "nearest" });
  }

  async function runStream(view, query, wireMode) {
    const resp = await fetch("/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: query, mode: wireMode, use_web: webEl.checked,
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
        try { handleEvent(view, JSON.parse(line.slice(5).trim())); }
        catch (e) { /* ignore malformed event */ }
      }
    }
    if (!view.sawEvents) throw new Error("stream ended with no events");
  }

  async function runClassic(view, query, wireMode) {
    const r = await postJSON("/api/query", {
      query: query, mode: wireMode, use_web: webEl.checked,
      model: modelEl.value,
    });
    const data = r.data;
    if (data.error_code === "browse_unavailable_hosted") {
      showBrowseHosted();
      return;
    }
    if (!r.resp.ok && data.error && !data.answer) {
      showError(data.error);
      return;
    }
    (data.steps || []).forEach(function (s) { addStepLine(view, s); });
    (data.tools_used || []).forEach(function (t) {
      finishToolRow(view, t, "", false);
    });
    renderAnswer(view, data, view.mode);
  }

  async function run() {
    const query = queryEl.value.trim();
    errorEl.classList.add("hidden");
    if (!query) { showError("Please enter something first."); return; }
    const mode = currentMode();
    const wireMode = mode === "friend" ? "agent" : mode;
    const view = newTurn(query, mode);
    goBtn.disabled = true;
    statusEl.classList.remove("hidden");
    statusTextEl.textContent = (wireMode === "agent") ? "Starting agent…" : "Working…";
    if (wireMode === "agent") startApprovalPolling();
    try {
      await runStream(view, query, wireMode);
    } catch (err) {
      try {
        await runClassic(view, query, wireMode);
        setState(view, "done (non-streamed)", "ok");
      } catch (err2) {
        showError("Request failed: " + err2.message);
        setState(view, "error", "bad");
      }
    } finally {
      stopApprovalPolling();
      goBtn.disabled = false;
      statusEl.classList.add("hidden");
      loadHistory(); loadActivity(); loadRuns();
      loadMemory(); loadNudges(); loadProposals(); loadTasks();
    }
  }

  goBtn.addEventListener("click", run);
  queryEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
  });
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      queryEl.focus();
      queryEl.select();
    }
  });

  // example chips + friend mood chips
  document.querySelectorAll(".example").forEach(function (chip) {
    chip.addEventListener("click", function () {
      setMode(chip.getAttribute("data-mode"));
      queryEl.value = chip.getAttribute("data-text") || "";
      queryEl.focus();
    });
  });
  document.querySelectorAll("#friend-moods .chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      showPanel("mood", true);
      loadMood(chip.getAttribute("data-mood"));
    });
  });

  // ---------------- voice (Web Speech API, client-side) ----------------
  const micBtn = $("mic");
  const voiceStatus = $("voice-status");
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

  // ---------------- health ----------------
  async function checkHealth() {
    const dot = $("health-dot");
    try {
      const r = await api("/api/health");
      const d = r.data;
      browseAvailable = !!d.browse_available;
      dot.className = "health-dot ok";
      $("health-text").textContent =
        "online · llm: " + (d.llm_backend || "?") +
        " · browse: " + (browseAvailable ? "ready" : "local only");
    } catch (e) {
      browseAvailable = false;
      dot.className = "health-dot bad";
      $("health-text").textContent = "server unreachable";
    }
    updateModeUI();
  }

  // ---------------- briefing (+ overnight results) ----------------
  async function loadBriefing() {
    const body = $("briefing-body");
    try {
      const r = await api("/api/briefing");
      const b = r.data;
      if (!r.resp.ok || b.error) throw new Error(b.error || r.resp.status);
      body.innerHTML = "";
      body.appendChild(el("p", "briefing-date",
        "☀️ " + (b.date || "") + (b.city ? " · " + b.city : "")));
      if (b.weather) {
        const w = b.weather;
        const rain = (w.rain_chance == null) ? "rain n/a"
          : "rain " + Math.round(w.rain_chance) + "%";
        body.appendChild(el("p", null,
          "🌡️ " + w.temp_min + "°C – " + w.temp_max + "°C, " +
          w.conditions + ", " + rain));
      } else {
        body.appendChild(el("p", "muted", "🌡️ Weather unavailable right now."));
      }
      if (b.reminders_due && b.reminders_due.length) {
        const p = el("p", null, "⏰ Due: ");
        p.appendChild(el("span", null, b.reminders_due.map(function (x) {
          return x.text + " (" + x.due + ")";
        }).join("; ")));
        body.appendChild(p);
      }
      if (b.goals && b.goals.length) {
        const p = el("p", null, "🎯 Goals: ");
        p.appendChild(el("span", null, b.goals.map(function (g) {
          return g.title;
        }).join(" · ")));
        body.appendChild(p);
      }
      if (b.memory_facts && b.memory_facts.length) {
        const ul = el("ul", "briefing-facts");
        b.memory_facts.forEach(function (f) { ul.appendChild(el("li", null, f)); });
        body.appendChild(ul);
      }
      if (b.artifacts && b.artifacts.length) {
        body.appendChild(el("p", "muted",
          "🗂️ Latest artifacts: " + b.artifacts.map(function (a) {
            return a.title;
          }).join(" · ")));
      }
      // overnight results (v5)
      const wrap = $("overnight-wrap");
      const list = $("overnight-list");
      list.innerHTML = "";
      const results = b.overnight_results || [];
      wrap.classList.toggle("hidden", !results.length);
      results.forEach(function (t) {
        const li = el("li");
        const meta = el("div", "item-meta");
        meta.appendChild(pill(t.status));
        meta.appendChild(el("span", null,
          "  " + timeAgo(t.executed_at) + " · "));
        meta.appendChild(el("span", "item-main", t.instruction || ""));
        li.appendChild(meta);
        if (t.result) li.appendChild(el("div", "item-result", t.result));
        list.appendChild(li);
      });
    } catch (e) {
      body.innerHTML = "";
      body.appendChild(el("p", "muted", "Briefing unavailable right now."));
    }
  }

  // ---------------- live cards ----------------
  async function loadCards() {
    const wEl = $("card-weather"), fxEl = $("card-fx"), nEl = $("card-news");
    try {
      const r = await api("/api/cards");
      const c = r.data;
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
        fxEl.appendChild(el("p", null, "1 USD = ₹" + Number(c.fx.rate).toFixed(2)));
        fxEl.appendChild(el("p", "muted mini-sub", c.fx.updated || ""));
      } else fxEl.appendChild(el("p", "muted", "Unavailable"));
      nEl.innerHTML = "";
      nEl.appendChild(el("div", "mini-title", "📰 Top headlines"));
      const ul = el("ul", "mini-news");
      (c.headlines || []).forEach(function (h) {
        const li = el("li");
        const a = el("a", null, h.title);
        a.href = h.link; a.target = "_blank"; a.rel = "noopener";
        li.appendChild(a); ul.appendChild(li);
      });
      if (!ul.children.length) ul.appendChild(el("li", "muted", "Unavailable"));
      nEl.appendChild(ul);
    } catch (e) { /* cards keep skeletons */ }
  }

  // ---------------- ideas ----------------
  async function loadIdeas() {
    const list = $("ideas-list");
    try {
      const r = await api("/api/ideas");
      list.innerHTML = "";
      (r.data.ideas || []).forEach(function (idea) {
        const li = el("li");
        li.appendChild(el("span", "item-main", idea));
        const b = el("button", "mini-btn", "Run this");
        b.type = "button";
        b.addEventListener("click", function () {
          setMode("agent");
          queryEl.value = idea;
          window.scrollTo({ top: 0, behavior: "smooth" });
          queryEl.focus();
        });
        li.appendChild(b);
        list.appendChild(li);
      });
      if (!list.children.length) {
        const li = el("li", "plain muted", "No ideas right now.");
        list.appendChild(li);
      }
    } catch (e) { /* panel stays empty */ }
  }

  // ---------------- tasks ----------------
  const taskMsg = $("task-msg");
  const TASK_MSG_DEFAULT = taskMsg.textContent;

  function renderTasks(tasks) {
    const list = $("tasks-list");
    list.innerHTML = "";
    (tasks || []).forEach(function (t) {
      const li = el("li");
      const meta = el("div", "item-meta");
      meta.appendChild(pill(t.status));
      meta.appendChild(el("span", null,
        "  " + (t.run_at ? new Date(t.run_at).toLocaleString("en-IN") : "") +
        (t.executed_at ? " · ran " + timeAgo(t.executed_at) : "")));
      li.appendChild(meta);
      li.appendChild(el("div", "item-main", t.instruction || ""));
      if (t.result) {
        const det = el("details");
        det.appendChild(el("summary", null, "Last result"));
        det.appendChild(el("div", "item-result", t.result));
        li.appendChild(det);
      }
      if (t.status === "pending") {
        const actions = el("div", "card-actions");
        const runBtn = el("button", "mini-btn", "▶ Run now");
        runBtn.type = "button";
        runBtn.addEventListener("click", async function () {
          taskMsg.textContent = "Running task…";
          try {
            const r = await postJSON("/api/tasks/run", { id: t.id });
            taskMsg.textContent = r.data.error ||
              ("Done: " + (r.data.answer || "").slice(0, 160));
            toast(r.data.error || "Task finished.", r.data.error ? "error" : "ok");
            loadTasks(); loadActivity(); loadBriefing();
          } catch (e) { taskMsg.textContent = "Run failed."; }
        });
        const cancelBtn = el("button", "del-btn", "Cancel");
        cancelBtn.type = "button";
        cancelBtn.addEventListener("click", async function () {
          try {
            const r = await postJSON("/api/tasks/cancel", { id: t.id });
            if (r.data.tasks) renderTasks(r.data.tasks);
          } catch (e) { /* ignore */ }
        });
        actions.appendChild(runBtn); actions.appendChild(cancelBtn);
        li.appendChild(actions);
      }
      list.appendChild(li);
    });
    if (!list.children.length) {
      list.appendChild(el("li", "plain muted", "No scheduled tasks yet."));
    }
  }
  async function loadTasks() {
    try {
      const r = await api("/api/tasks");
      renderTasks(r.data.tasks);
    } catch (e) { /* panel stays empty */ }
  }
  $("task-add").addEventListener("click", async function () {
    const instruction = $("task-instruction").value.trim();
    const when = $("task-when").value.trim();
    if (!instruction || !when) {
      taskMsg.textContent = "Type an instruction and a time (e.g. tomorrow 11:00 am).";
      return;
    }
    try {
      const r = await postJSON("/api/tasks",
        { instruction: instruction, run_at: when });
      if (!r.resp.ok) {
        taskMsg.textContent = r.data.error || "Could not schedule.";
        return;
      }
      taskMsg.textContent = "Scheduled for " +
        new Date(r.data.created.run_at).toLocaleString("en-IN") + " IST.";
      toast("⏰ Task scheduled.", "ok");
      $("task-instruction").value = "";
      $("task-when").value = "";
      renderTasks(r.data.tasks);
    } catch (e) { taskMsg.textContent = "Could not schedule."; }
  });

  // ---------------- runs + sessions ----------------
  const runsMsg = $("runs-msg");
  let allRuns = [];

  function renderRuns(filter) {
    const list = $("runs-list");
    const q = (filter || "").toLowerCase();
    list.innerHTML = "";
    allRuns.filter(function (r) {
      if (!q) return true;
      return ((r.task || "") + " " + (r.status || "") + " " +
        (r.source || "") + " " + (r.tools_used || []).join(" ")
      ).toLowerCase().includes(q);
    }).forEach(function (r) {
      const li = el("li");
      const meta = el("div", "item-meta");
      meta.appendChild(pill(r.status));
      meta.appendChild(el("span", null,
        "  " + timeAgo(r.started_at) + " · " + (r.source || "") +
        " · " + (r.steps || 0) + " steps" +
        (r.resumed_from ? " · resumed" : "")));
      li.appendChild(meta);
      li.appendChild(el("div", "item-main", r.task || ""));
      const actions = el("div", "card-actions");
      const traceBtn = el("button", "mini-btn", "Trace");
      traceBtn.type = "button";
      traceBtn.addEventListener("click", function () { showRunTrace(r.id); });
      actions.appendChild(traceBtn);
      if (r.resumable) {
        const resumeBtn = el("button", "mini-btn", "▶ Resume");
        resumeBtn.type = "button";
        resumeBtn.addEventListener("click", async function () {
          runsMsg.textContent = "Resuming run…";
          try {
            const rr = await postJSON("/api/runs/resume", { id: r.id });
            runsMsg.textContent = rr.data.error ||
              ("Resumed answer: " + (rr.data.answer || "").slice(0, 240));
            toast(rr.data.error || "Run resumed.", rr.data.error ? "error" : "ok");
            loadRuns(); loadActivity();
          } catch (e) { runsMsg.textContent = "Resume failed."; }
        });
        actions.appendChild(resumeBtn);
      }
      li.appendChild(actions);
      list.appendChild(li);
    });
    if (!list.children.length) {
      list.appendChild(el("li", "plain muted",
        q ? "No runs match the filter." : "No agent runs recorded yet."));
    }
  }
  async function loadRuns() {
    try {
      const r = await api("/api/runs");
      allRuns = r.data.runs || [];
      renderRuns($("runs-search").value);
    } catch (e) { /* panel stays empty */ }
  }
  $("runs-search").addEventListener("input", function () {
    renderRuns(this.value);
  });

  async function showRunTrace(id) {
    const box = $("run-trace");
    box.innerHTML = "";
    box.appendChild(el("p", "muted", "Loading trace…"));
    try {
      const r = await api("/api/runs/" + encodeURIComponent(id));
      box.innerHTML = "";
      if (!r.resp.ok || !r.data.run) {
        box.appendChild(el("p", "muted", r.data.error || "Run not found."));
        return;
      }
      const run = r.data.run;
      box.appendChild(el("h3", "sub-title", "Trace — " + (run.task || "")));
      const ul = el("ul", "clean-list");
      (run.trace || []).forEach(function (t) {
        const li = el("li");
        const meta = el("div", "item-meta");
        meta.appendChild(el("span", null,
          "step " + (t.step || "?") + " · " + (t.ms || 0) + " ms"));
        if (t.blocked) meta.appendChild(el("span", null, " · 🚫 blocked"));
        if (t.approval) meta.appendChild(el("span", null,
          t.approval === "approved" ? " · ✅ approved" : " · 🚫 denied"));
        li.appendChild(meta);
        const nameLine = el("div");
        nameLine.appendChild(el("span", "item-main", t.tool || ""));
        const badge = tierBadge(t.tier || null);
        if (badge) nameLine.appendChild(badge);
        li.appendChild(nameLine);
        if (t.observation) {
          li.appendChild(el("div", "item-result", t.observation.slice(0, 300)));
        }
        ul.appendChild(li);
      });
      if (!ul.children.length) {
        ul.appendChild(el("li", "plain muted", "No trace steps recorded."));
      }
      box.appendChild(ul);
      if (run.answer) {
        box.appendChild(el("p", null, "Answer: " + run.answer.slice(0, 400)));
      }
    } catch (e) {
      box.innerHTML = "";
      box.appendChild(el("p", "muted", "Could not load the trace."));
    }
  }

  function renderSessions(sessions) {
    const list = $("sessions-list");
    list.innerHTML = "";
    (sessions || []).forEach(function (s) {
      const li = el("li");
      const meta = el("div", "item-meta");
      meta.appendChild(el("span", null,
        timeAgo(s.timestamp || s.time || s.created_at) +
        " · " + (s.source || "")));
      li.appendChild(meta);
      li.appendChild(el("div", "item-main", s.task || ""));
      if (s.tools && s.tools.length) {
        li.appendChild(el("div", "item-meta", "tools: " + s.tools.join(", ")));
      }
      if (s.summary) {
        li.appendChild(el("div", "item-result", s.summary.slice(0, 200)));
      }
      list.appendChild(li);
    });
    if (!list.children.length) {
      list.appendChild(el("li", "plain muted", "No sessions found."));
    }
  }
  async function loadSessions(q) {
    try {
      const r = await api("/api/sessions" +
        (q ? "?q=" + encodeURIComponent(q) : ""));
      renderSessions(r.data.sessions);
    } catch (e) { /* panel stays empty */ }
  }
  $("sessions-go").addEventListener("click", function () {
    loadSessions($("sessions-search").value.trim());
  });
  $("sessions-search").addEventListener("keydown", function (e) {
    if (e.key === "Enter") loadSessions(this.value.trim());
  });

  // ---------------- memory + nudges ----------------
  const memoryMsg = $("memory-msg");
  let allMemoryLines = [];

  function renderMemoryLines(filter) {
    const list = $("memory-list");
    const q = (filter || "").toLowerCase();
    list.innerHTML = "";
    allMemoryLines.filter(function (line) {
      return !q || line.toLowerCase().includes(q);
    }).forEach(function (line) {
      const li = el("li");
      const kind = line.split(":")[0];
      const span = el("span", "item-main", line);
      li.appendChild(span);
      if (kind === "fact" || kind === "preference" || kind === "instruction") {
        span.appendChild(el("span", "kind-badge", kind));
      }
      const b = el("button", "del-btn", "✕");
      b.type = "button";
      b.title = "Forget this";
      b.addEventListener("click", async function () {
        try {
          const r = await postJSON("/api/memory/delete", { line: line });
          memoryMsg.textContent = r.data.result || "";
          if (r.data.lines) {
            allMemoryLines = r.data.lines;
            renderMemoryLines($("memory-search").value);
          }
        } catch (e) { memoryMsg.textContent = "Delete failed."; }
      });
      li.appendChild(b);
      list.appendChild(li);
    });
    if (!list.children.length) {
      list.appendChild(el("li", "plain muted",
        q ? "No memory matches the filter." : "Memory is empty."));
    }
  }
  async function loadMemory() {
    try {
      const r = await api("/api/memory");
      allMemoryLines = r.data.lines || [];
      renderMemoryLines($("memory-search").value);
    } catch (e) { /* panel stays empty */ }
  }
  $("memory-search").addEventListener("input", function () {
    renderMemoryLines(this.value);
  });
  $("memory-add").addEventListener("click", async function () {
    const fact = $("memory-input").value.trim();
    if (!fact) { memoryMsg.textContent = "Type something first."; return; }
    try {
      const r = await postJSON("/api/memory",
        { fact: fact, kind: $("memory-kind").value });
      memoryMsg.textContent = r.data.result || r.data.error || "";
      if (r.resp.ok) {
        $("memory-input").value = "";
        allMemoryLines = r.data.lines || allMemoryLines;
        renderMemoryLines($("memory-search").value);
        toast("🧠 Saved to memory.", "ok");
      }
    } catch (e) { memoryMsg.textContent = "Could not save."; }
  });

  async function loadNudges() {
    const wrap = $("nudges-wrap");
    const list = $("nudges-list");
    try {
      const r = await api("/api/nudges");
      const nudges = r.data.nudges || [];
      wrap.classList.toggle("hidden", !nudges.length);
      list.innerHTML = "";
      nudges.forEach(function (n) {
        const card = el("div", "nudge-card");
        const h = el("h4", null, "Remember this?");
        h.appendChild(el("span", "kind-badge", n.kind || "fact"));
        card.appendChild(h);
        card.appendChild(el("p", "item-main", n.text || ""));
        if (n.reason) card.appendChild(el("p", "item-meta", n.reason));
        const actions = el("div", "card-actions");
        const save = el("button", "mini-btn", "💾 Save to memory");
        save.type = "button";
        save.addEventListener("click", async function () {
          try {
            const rr = await postJSON(
              "/api/nudges/" + encodeURIComponent(n.id) + "/accept", {});
            if (rr.resp.ok) {
              toast("🧠 Saved: " + (n.text || "").slice(0, 60), "ok");
              loadMemory(); loadNudges();
            } else toast(rr.data.error || "Could not save.", "error");
          } catch (e) { toast("Could not save.", "error"); }
        });
        const dismiss = el("button", "del-btn", "Dismiss");
        dismiss.type = "button";
        dismiss.addEventListener("click", async function () {
          try {
            await postJSON(
              "/api/nudges/" + encodeURIComponent(n.id) + "/dismiss", {});
            loadNudges();
          } catch (e) { /* ignore */ }
        });
        actions.appendChild(save); actions.appendChild(dismiss);
        card.appendChild(actions);
        list.appendChild(card);
      });
    } catch (e) { /* suggestions are optional */ }
  }

  // ---------------- skills + proposals ----------------
  async function loadSkills() {
    const list = $("skills-list");
    try {
      const r = await api("/api/skills");
      list.innerHTML = "";
      (r.data.skills || []).forEach(function (s) {
        const li = el("li");
        li.appendChild(el("strong", null, s.name));
        li.appendChild(el("span", null, " — " + (s.description || "")));
        list.appendChild(li);
      });
      if (!list.children.length) {
        list.appendChild(el("li", "plain muted", "No skills installed."));
      }
    } catch (e) { /* panel stays empty */ }
  }

  async function loadProposals() {
    const wrap = $("proposals-wrap");
    const list = $("proposals-list");
    if (!wrap) return;
    try {
      const r = await api("/api/skill-proposals?status=pending");
      const proposals = r.data.proposals || [];
      wrap.classList.toggle("hidden", !proposals.length);
      list.innerHTML = "";
      proposals.forEach(function (p) {
        const card = el("div", "proposal-card");
        card.appendChild(el("h4", null, p.name));
        if (p.description) card.appendChild(el("p", "item-main", p.description));
        if (p.source_task) {
          card.appendChild(el("p", "item-meta",
            "Distilled from: “" + p.source_task + "” · " + timeAgo(p.created_at)));
        }
        const det = el("details");
        det.appendChild(el("summary", null, "Instructions"));
        det.appendChild(el("pre", null, p.instructions || ""));
        card.appendChild(det);
        const actions = el("div", "card-actions");
        const accept = el("button", "mini-btn", "✅ Accept — make it a skill");
        accept.type = "button";
        accept.addEventListener("click", async function () {
          try {
            const rr = await postJSON(
              "/api/skill-proposals/" + encodeURIComponent(p.id) + "/accept", {});
            if (rr.resp.ok) {
              toast("🧩 Skill created: " + p.name, "ok");
              loadProposals(); loadSkills();
            } else {
              toast(rr.data.error || "Could not accept the proposal.", "error");
            }
          } catch (e) { toast("Could not accept the proposal.", "error"); }
        });
        const discard = el("button", "del-btn", "Discard");
        discard.type = "button";
        discard.addEventListener("click", async function () {
          try {
            await postJSON(
              "/api/skill-proposals/" + encodeURIComponent(p.id) + "/discard", {});
            loadProposals();
          } catch (e) { /* ignore */ }
        });
        actions.appendChild(accept); actions.appendChild(discard);
        card.appendChild(actions);
        list.appendChild(card);
      });
    } catch (e) { /* proposals are optional */ }
  }

  $("skill-create").addEventListener("click", async function () {
    const msg = $("skill-msg");
    const payload = {
      name: $("skill-name").value.trim(),
      description: $("skill-desc").value.trim(),
      instructions: $("skill-instructions").value.trim(),
    };
    try {
      const r = await postJSON("/api/skills", payload);
      if (!r.resp.ok) {
        msg.textContent = r.data.error || "Could not save skill.";
        return;
      }
      msg.textContent = "Saved skill: " + r.data.created.name;
      toast("🧩 Skill saved: " + r.data.created.name, "ok");
      $("skill-name").value = "";
      $("skill-desc").value = "";
      $("skill-instructions").value = "";
      loadSkills();
    } catch (e) { msg.textContent = "Could not save skill."; }
  });

  // ---------------- history ----------------
  async function loadHistory() {
    const list = $("history-list");
    try {
      const r = await api("/api/history");
      list.innerHTML = "";
      (r.data.history || []).forEach(function (entry) {
        const li = el("li", "clickable");
        li.appendChild(el("div", "item-meta",
          "[" + entry.mode + "] " + timeAgo(entry.timestamp) + " · " +
          (entry.timestamp ? new Date(entry.timestamp).toLocaleString() : "")));
        li.appendChild(el("div", "item-main", entry.query || ""));
        li.title = "Click to restore this answer";
        li.addEventListener("click", function () {
          const view = newTurn(entry.query || "", entry.mode || "ask");
          view.live.classList.add("hidden");
          renderAnswer(view,
            { answer: entry.answer, sources: entry.sources || [], steps: [] },
            entry.mode || "ask");
        });
        list.appendChild(li);
      });
      if (!list.children.length) {
        list.appendChild(el("li", "plain muted", "No runs yet."));
      }
    } catch (e) { /* panel stays empty */ }
  }

  // ---------------- activity ----------------
  async function loadActivity() {
    const list = $("activity-list");
    try {
      const r = await api("/api/activity");
      list.innerHTML = "";
      (r.data.activity || []).forEach(function (a) {
        const li = el("li");
        li.appendChild(el("div", "item-meta",
          timeAgo(a.timestamp) + " · " +
          (a.timestamp ? new Date(a.timestamp).toLocaleString() : "")));
        li.appendChild(el("div", "item-main", a.task || ""));
        if (a.tools_used && a.tools_used.length) {
          li.appendChild(el("div", "item-meta",
            "tools: " + a.tools_used.join(", ")));
        }
        if (a.answer_preview) {
          li.appendChild(el("div", "item-result", a.answer_preview));
        }
        list.appendChild(li);
      });
      if (!list.children.length) {
        list.appendChild(el("li", "plain muted", "No agent runs yet."));
      }
    } catch (e) { /* panel stays empty */ }
  }

  // ---------------- mood ----------------
  const moodResults = $("mood-results");
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

  async function loadMood(mood) {
    moodResults.innerHTML = "";
    moodResults.appendChild(el("p", null, MOOD_LINES[mood] || "Here you go:"));
    moodResults.appendChild(el("p", "muted", "Finding songs, shows and movies…"));
    try {
      const r = await api("/api/mood?mood=" + encodeURIComponent(mood));
      const d = r.data;
      moodResults.innerHTML = "";
      moodResults.appendChild(el("p", null, MOOD_LINES[mood] || "Here you go:"));
      function linkList(title, items, extra) {
        if (!items || !items.length) return;
        moodResults.appendChild(el("h3", "sub-title", title));
        const ul = el("ul", "clean-list");
        items.forEach(function (s) {
          const li = el("li");
          const a = el("a", "item-main", s.title + (s.artist ? " — " + s.artist : ""));
          a.href = s.url || "#"; a.target = "_blank"; a.rel = "noopener";
          li.appendChild(a);
          if (extra) extra(li, s);
          ul.appendChild(li);
        });
        moodResults.appendChild(ul);
      }
      linkList("🎵 Songs", d.songs, function (li, s) {
        if (s.preview) {
          const p = el("a", "item-meta", "  ▶ preview");
          p.href = s.preview; p.target = "_blank"; p.rel = "noopener";
          li.appendChild(p);
        }
      });
      linkList("📺 Shows", d.shows, function (li, s) {
        if (s.summary) {
          li.appendChild(el("div", "item-result", s.summary.slice(0, 160)));
        }
      });
      linkList("🎬 Movie lists", d.movies, null);
      if (d.note) moodResults.appendChild(el("p", "muted", d.note));
      if (d.error) moodResults.appendChild(el("p", "muted", d.error));
    } catch (e) {
      moodResults.appendChild(el("p", "muted", "Could not fetch suggestions right now."));
    }
  }
  document.querySelectorAll(".mood-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      loadMood(b.getAttribute("data-mood"));
    });
  });

  // ---------------- settings ----------------
  const settingsMsg = $("settings-msg");
  let webhookClearPending = false;

  async function loadSettings() {
    try {
      const r = await api("/api/settings");
      const s = r.data;
      const sel = $("settings-model");
      sel.innerHTML = "";
      const none = el("option", null, "Server default");
      none.value = "";
      sel.appendChild(none);
      (s.models || []).forEach(function (m) {
        const o = el("option", null, m);
        o.value = m;
        sel.appendChild(o);
      });
      sel.value = s.model || "";
      if (s.model && modelEl.querySelector('option[value="' + s.model + '"]')) {
        modelEl.value = s.model;
      }
      $("settings-steps").value = s.max_steps || 8;
      if (!localStorage.getItem("pa-theme") && s.theme) {
        applyTheme(s.theme, false);
      }
      $("settings-theme").value = currentTheme();
      const state = $("webhook-state");
      if (s.webhook_token_set) {
        state.textContent = "Token is set (" + (s.webhook_token_source || "?") +
          (s.webhook_token_masked ? ", " + s.webhook_token_masked : "") +
          "). Requests must send it as the X-Agent-Token header.";
      } else {
        state.textContent = "No token set — the webhook accepts any caller. " +
          "Set one if the app is reachable by others.";
      }
    } catch (e) { /* settings are optional */ }
  }
  $("webhook-clear").addEventListener("click", function () {
    webhookClearPending = true;
    $("settings-webhook").value = "";
    settingsMsg.textContent = "Token will be cleared when you save settings.";
  });
  $("settings-theme").addEventListener("change", function () {
    applyTheme(this.value, true);
  });
  $("settings-save").addEventListener("click", async function () {
    const payload = {
      model: $("settings-model").value || null,
      max_steps: Number($("settings-steps").value || 8),
      theme: $("settings-theme").value,
    };
    const tokenInput = $("settings-webhook").value.trim();
    if (tokenInput) payload.webhook_token = tokenInput;
    else if (webhookClearPending) payload.webhook_token = "";
    try {
      const r = await postJSON("/api/settings", payload);
      if (!r.resp.ok) {
        settingsMsg.textContent = r.data.error || "Could not save settings.";
        return;
      }
      webhookClearPending = false;
      $("settings-webhook").value = "";
      settingsMsg.textContent = "Settings saved.";
      toast("⚙️ Settings saved.", "ok");
      if (payload.model) modelEl.value = payload.model;
      loadSettings();
    } catch (e) { settingsMsg.textContent = "Could not save settings."; }
  });

  // ---------------- init ----------------
  updateModeUI();
  checkHealth();
  loadBriefing();
  loadCards();
  loadIdeas();
  loadTasks();
  loadRuns();
  loadSessions("");
  loadMemory();
  loadNudges();
  loadSkills();
  loadProposals();
  loadHistory();
  loadActivity();
  loadSettings();
})();
