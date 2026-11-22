(function () {
  const btn = document.getElementById("go");
  const queryEl = document.getElementById("query");
  const webEl = document.getElementById("use-web");
  const statusEl = document.getElementById("status");
  const errorEl = document.getElementById("error");
  const resultEl = document.getElementById("result");
  const answerEl = document.getElementById("answer");
  const sourcesEl = document.getElementById("sources");
  const stepsEl = document.getElementById("steps");
  const noteEl = document.getElementById("note");
  const titleEl = document.getElementById("result-title");

  function currentMode() {
    const el = document.querySelector('input[name="mode"]:checked');
    return el ? el.value : "ask";
  }

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.remove("hidden");
  }

  async function run() {
    const query = queryEl.value.trim();
    errorEl.classList.add("hidden");
    resultEl.classList.add("hidden");
    if (!query) {
      showError("Please enter a question or a message first.");
      return;
    }
    const mode = currentMode();
    btn.disabled = true;
    statusEl.classList.remove("hidden");
    try {
      const resp = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query, mode: mode, use_web: webEl.checked }),
      });
      const data = await resp.json();
      if (!resp.ok && data.error && !data.answer) {
        showError(data.error);
        return;
      }
      titleEl.textContent = mode === "reply" ? "Draft reply" : "Answer";
      answerEl.textContent = data.answer || "";
      if (data.note) {
        noteEl.textContent = data.note;
        noteEl.classList.remove("hidden");
      } else {
        noteEl.classList.add("hidden");
      }
      sourcesEl.innerHTML = "";
      (data.sources || []).forEach(function (s) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = s.url;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = s.title || s.url;
        li.appendChild(a);
        sourcesEl.appendChild(li);
      });
      stepsEl.innerHTML = "";
      (data.steps || []).forEach(function (s) {
        const li = document.createElement("li");
        li.textContent = s;
        stepsEl.appendChild(li);
      });
      resultEl.classList.remove("hidden");
    } catch (err) {
      showError("Request failed: " + err.message);
    } finally {
      btn.disabled = false;
      statusEl.classList.add("hidden");
    }
  }

  btn.addEventListener("click", run);
  queryEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
  });
})();
