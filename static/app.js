/* Action Code Dashboard - UI helpers */
(function () {
  "use strict";

  // Live clock in the top bar
  var clock = document.getElementById("clock");
  if (clock) {
    function tick() {
      var now = new Date();
      var p = function (n) { return String(n).padStart(2, "0"); };
      clock.textContent =
        now.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) +
        "  " + p(now.getHours()) + ":" + p(now.getMinutes()) + ":" + p(now.getSeconds());
    }
    tick();
    setInterval(tick, 1000);
  }

  // A required portal choice uses a native dialog with a keyboard focus trap.
  var portalDialog = document.getElementById("portal-choice-dialog");
  if (portalDialog && typeof portalDialog.showModal === "function") {
    portalDialog.close();
    portalDialog.showModal();
    portalDialog.addEventListener("cancel", function (event) { event.preventDefault(); });
    var selectedPortal = portalDialog.querySelector('input[name="portal"]:checked');
    if (selectedPortal) selectedPortal.focus();
  }

  // Code picker chips on the run page: native <label> toggles the checkbox;
  // the :has() selector in CSS paints the selected state.

  // Run page: "Source inputs" radio (folder vs. upload files for this run).
  // Reveals the six file fields only when "Upload" is selected, and only
  // marks them required in that mode so folder-mode submits stay untouched.
  var sourceRadios = document.querySelectorAll('input[name="source_mode"]');
  var uploadBody = document.getElementById("upload-body");
  if (sourceRadios.length && uploadBody) {
    var uploadFileInputs = uploadBody.querySelectorAll('input[type="file"]');
    var syncSourceMode = function () {
      var checked = document.querySelector('input[name="source_mode"]:checked');
      var isUpload = !!checked && checked.value === "upload";
      uploadBody.hidden = !isUpload;
      uploadFileInputs.forEach(function (f) { f.required = isUpload; });
    };
    sourceRadios.forEach(function (r) { r.addEventListener("change", syncSourceMode); });
    syncSourceMode();
  }

  // Auto-dismiss flash messages after 6s
  document.querySelectorAll(".flash").forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity .4s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 420);
    }, 6000);
  });

  // Run status polling (used on run detail page) - updates the live step
  // text while running, reloads when the run finishes.
  var statusEl = document.getElementById("run-status");
  // Only poll (and reload-on-finish) while the run is actually still going.
  // Without this check, loading an already-finished run page would fetch
  // status once, see it's not "running", and reload immediately - which
  // repeats forever and can cancel an in-progress file download.
  if (statusEl && statusEl.dataset.status === "running") {
    var runId = statusEl.dataset.runId;
    var stepEl = document.getElementById("current-step");
    var done = false;
    function poll() {
      if (done) return;
      fetch(statusEl.dataset.statusUrl || ("/run/" + runId + "/status"))
        .then(function (r) { if (!r.ok || r.redirected) throw new Error("Status unavailable"); return r.json(); })
        .then(function (data) {
          if (stepEl && data.current_step) stepEl.textContent = data.current_step;
          if (data.status === "running") {
            setTimeout(poll, 4000);
            return;
          }
          done = true;
          window.location.reload();
        })
        .catch(function () { setTimeout(poll, 6000); });
    }
    poll();
  }

  // ME2M history and dashboard refresh when an active report finishes.
  document.querySelectorAll("[data-me2m-status-url]").forEach(function (row) {
    function pollReport() {
      fetch(row.dataset.me2mStatusUrl)
        .then(function (r) { if (!r.ok || r.redirected) throw new Error("Status unavailable"); return r.json(); })
        .then(function (data) {
          if (data.status === "running") setTimeout(pollReport, 4000);
          else window.location.reload();
        })
        .catch(function () { setTimeout(pollReport, 6000); });
    }
    setTimeout(pollReport, 4000);
  });

  // Dedicated live status panel (dashboard / run page): polls the running
  // run, updates the step chip + step progress list, and ticks the elapsed
  // timer. Reloads the page when the run finishes.
  var live = document.getElementById("live-status");
  if (live) {
    var liveRunId = live.dataset.runId;
    var liveStep = document.getElementById("live-step");
    var liveElapsed = document.getElementById("live-elapsed");
    var started = new Date(live.dataset.started.replace(" ", "T"));
    var stepItems = document.querySelectorAll(".live-steps .live-step");

    // Mark steps before the current one as done, the current one as active.
    function refreshSteps(text) {
      var m = text && text.match(/^(Step [\d\-]+b?)/);
      var currentKey = m ? m[1] : null;
      var found = false;
      stepItems.forEach(function (el) {
        el.classList.remove("done", "current");
        if (!found && currentKey) {
          if (el.dataset.stepKey === currentKey) {
            el.classList.add("current");
            found = true;
          } else {
            el.classList.add("done");
          }
        }
      });
    }

    function tickElapsed() {
      if (!liveElapsed) return;
      var s = Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000));
      liveElapsed.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    }
    tickElapsed();
    setInterval(tickElapsed, 1000);
    refreshSteps(liveStep.textContent);

    function pollLive() {
      fetch("/run/" + liveRunId + "/status")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === "running") {
            if (data.current_step) {
              liveStep.textContent = data.current_step;
              refreshSteps(data.current_step);
            }
            setTimeout(pollLive, 4000);
          } else {
            window.location.reload();
          }
        })
        .catch(function () { setTimeout(pollLive, 8000); });
    }
    pollLive();
  }

  // Confirm deletes
  document.querySelectorAll("[data-confirm]").forEach(function (el) {
    el.addEventListener("click", function (e) {
      if (!window.confirm(el.dataset.confirm)) e.preventDefault();
    });
  });

  // ---------------------------------------------------------------------
  // Generic modal system (Settings > Users: "Add user" / "edit codes").
  // Any [data-modal-open="<id>"] element opens #<id>.modal-overlay; a
  // [data-modal-close] inside it, a click on the dim backdrop, or Escape
  // closes it. Server-rendered modals can start pre-opened (e.g. to show
  // a validation error again) by already having the "open" class.
  // ---------------------------------------------------------------------
  function openModal(overlay) {
    if (!overlay) return;
    overlay.classList.add("open");
    var firstField = overlay.querySelector("input, select, textarea, button");
    if (firstField) firstField.focus();
  }

  function closeModal(overlay) {
    if (overlay) overlay.classList.remove("open");
  }

  document.querySelectorAll("[data-modal-open]").forEach(function (trigger) {
    trigger.addEventListener("click", function () {
      openModal(document.getElementById(trigger.dataset.modalOpen));
    });
  });

  document.querySelectorAll(".modal-overlay").forEach(function (overlay) {
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeModal(overlay);
    });
    overlay.querySelectorAll("[data-modal-close]").forEach(function (btn) {
      btn.addEventListener("click", function () { closeModal(overlay); });
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll(".modal-overlay.open").forEach(closeModal);
  });

  // Password visibility toggle. Wraps every password field with a small
  // eye button so users can verify what they typed (e.g. SMTP password in
  // Settings) without changing how the form submits.
  var EYE =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-11-7-11-7a19.9 19.9 0 0 1 4.22-5.44M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 7 11 7a19.86 19.86 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24"/>' +
    '<line x1="1" y1="1" x2="23" y2="23"/></svg>';

  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    var wrap = document.createElement("div");
    wrap.className = "password-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "pw-toggle";
    toggle.setAttribute("aria-label", "Show password");
    toggle.innerHTML = EYE;
    wrap.appendChild(toggle);

    toggle.addEventListener("click", function () {
      var showing = input.type === "text";
      input.type = showing ? "password" : "text";
      toggle.innerHTML = showing ? EYE : EYE_OFF;
      toggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    });
  });
})();
