/* =========================================================================
   ARMA v4 — UI davranışları
   v2.4-dən fərqlər:
     * alert() yoxdur — toast bildirişi var (iş axınını kəsmir)
     * axtarış və seçim fetch ilə gedir, səhifə yenilənmir
     * toplu proqres location.reload() yerinə /api/job/<id> ilə oxunur
     * mövzu (işıqlı/qaranlıq/sistem) əl ilə seçilir, localStorage-da qalır
   ====================================================================== */
(function () {
  "use strict";

  var ARMA = window.ARMA || {};
  window.ARMA = ARMA;

  ARMA.WA = (window.ARMA_CONFIG && window.ARMA_CONFIG.wa) || "";

  /* ------------------------------------------------------------- format */
  ARMA.money = function (v) {
    var n = parseFloat(v);
    if (isNaN(n)) return "";
    return (Math.round(n * 100) / 100).toFixed(2).replace(/\.00$/, "") + " ₼";
  };

  /** Hər linkin ALTINDA maya qiyməti — WhatsApp-a hazır blok. */
  ARMA.block = function (links, maya) {
    var m = ARMA.money(maya);
    return (links || []).filter(Boolean).map(function (u) {
      return u + "\n" + m;
    }).join("\n\n");
  };

  /* -------------------------------------------------------------- toast */
  function host() {
    var el = document.getElementById("toastHost");
    if (!el) {
      el = document.createElement("div");
      el.id = "toastHost";
      el.className = "toast-host";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    return el;
  }

  var ICONS = { ok: "✓", warn: "⚠", danger: "✕", info: "ℹ" };

  ARMA.toast = function (message, kind, title) {
    kind = kind || "info";
    var el = document.createElement("div");
    el.className = "toast " + kind;

    var icon = document.createElement("span");
    icon.className = "t-icon";
    icon.textContent = ICONS[kind] || ICONS.info;

    var body = document.createElement("div");
    body.className = "t-body";
    if (title) {
      var t = document.createElement("div");
      t.className = "t-title";
      t.textContent = title;
      body.appendChild(t);
    }
    var msg = document.createElement("div");
    msg.textContent = message;          // textContent = HTML enjeksiyonu yoxdur
    body.appendChild(msg);

    var close = document.createElement("button");
    close.className = "t-close";
    close.type = "button";
    close.setAttribute("aria-label", "Bağla");
    close.textContent = "×";

    el.appendChild(icon);
    el.appendChild(body);
    el.appendChild(close);
    host().appendChild(el);

    var timer = setTimeout(remove, kind === "danger" ? 8000 : 4500);
    function remove() {
      clearTimeout(timer);
      el.classList.add("leaving");
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
    }
    close.addEventListener("click", remove);
    return el;
  };

  /* ------------------------------------------------------------ kopyala */
  function flashButton(btn, text) {
    if (!btn) return;
    var old = btn.dataset.label || btn.textContent;
    btn.dataset.label = old;
    btn.textContent = text;
    setTimeout(function () { btn.textContent = btn.dataset.label; }, 1700);
  }

  function fallbackCopy(text, done) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      done();
    } catch (e) {
      ARMA.toast("Kopyalamaq alınmadı — mətni əl ilə seçin.", "danger");
    }
    document.body.removeChild(ta);
  }

  ARMA.copy = function (text, btn) {
    if (!text || !text.trim()) {
      ARMA.toast("Kopyalanacaq link yoxdur.", "warn");
      return;
    }
    var done = function () {
      flashButton(btn, "✓ Kopyalandı");
      ARMA.toast(text.split("\n\n").length + " blok panoya köçürüldü.", "ok");
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {
        fallbackCopy(text, done);
      });
    } else {
      fallbackCopy(text, done);
    }
  };

  /* ----------------------------------------------------------- whatsapp */
  /* Yeni tab AÇILMIR — whatsapp:// protokolu birbaşa WhatsApp tətbiqini açır.
     WhatsApp təhlükəsizlik səbəbindən mesajı ÖZÜ göndərmir; düymə mesajı hazır
     yazılmış halda açır, istifadəçi "Göndər"-ə basır. Bu, WhatsApp-ın öz
     məhdudiyyətidir və heç bir üsulla keçilmir. */
  ARMA.wa = function (text, btn) {
    if (!text || !text.trim()) {
      ARMA.toast("Göndəriləcək link yoxdur.", "warn");
      return;
    }
    if (text.length > 12000 && !window.confirm(
      "Mətn çox uzundur — WhatsApp tətbiqi kəsə bilər. " +
      "Belə halda kopyalamaq daha etibarlıdır. Yenə də göndərilsin?")) return;
    window.location.href = "whatsapp://send?phone=" + ARMA.WA +
      "&text=" + encodeURIComponent(text);
    flashButton(btn, "➤ WhatsApp-a ötürüldü");
  };

  /* --------------------------------------------------------------- fetch */
  function handle(r) {
    return r.json().catch(function () {
      throw new Error("Server cavabı oxunmadı (" + r.status + ")");
    }).then(function (j) {
      if (!r.ok || j.ok === false) throw new Error(j.error || ("Xəta " + r.status));
      return j;
    });
  }

  ARMA.post = function (url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {})
    }).then(handle);
  };

  ARMA.get = function (url) {
    return fetch(url, { headers: { "Accept": "application/json" } }).then(handle);
  };

  /** Düyməni "işləyir" vəziyyətinə sal; qaytardığı funksiya bərpa edir. */
  ARMA.busy = function (btn, label) {
    if (!btn) return function () {};
    var old = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>' + (label || "İşlənir…");
    return function () { btn.disabled = false; btn.innerHTML = old; };
  };

  /* --------------------------------------------------------------- mövzu */
  var THEME_KEY = "arma-theme";

  ARMA.setTheme = function (mode) {
    var root = document.documentElement;
    if (mode === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* gizli rejim */ }
    Array.prototype.forEach.call(
      document.querySelectorAll(".theme-switch button"), function (b) {
        b.setAttribute("aria-pressed", String(b.dataset.theme === mode));
      });
  };

  ARMA.initTheme = function () {
    var mode = "system";
    try { mode = localStorage.getItem(THEME_KEY) || "system"; } catch (e) { /* yox */ }
    ARMA.setTheme(mode);
  };

  /* ------------------------------------------------- toplu iş proqresi */
  /** /api/job/<id> hər N saniyədən bir oxunur; səhifə YENİLƏNMİR.
      Bitəndə bir dəfə yenilənir ki, tam nəticə cədvəli görünsün. */
  ARMA.watchJob = function (jobId, opts) {
    opts = opts || {};
    var every = opts.every || 2500;
    var bar = document.getElementById("jobBar");
    var pct = document.getElementById("jobPct");
    var cur = document.getElementById("jobCurrent");
    var done = document.getElementById("jobDone");
    var img = document.getElementById("jobImg");
    var rate = document.getElementById("rateWarn");

    function tick() {
      ARMA.get("/api/job/" + jobId).then(function (j) {
        if (bar) bar.style.width = j.percent + "%";
        if (pct) pct.textContent = j.percent + "%";
        if (done) done.textContent = j.done + "/" + j.total;
        if (cur) cur.textContent = j.current || "—";
        if (img && j.img_total) img.textContent = j.img_done + "/" + j.img_total;
        if (rate) {
          rate.classList.toggle("show", !!j.rate_limited);
          if (j.rate_limited) {
            rate.textContent = "⏳ Sürət limiti — sistem " + j.cooldown_seconds +
              " saniyə gözləyib özü davam edəcək.";
          }
        }
        if (j.finished) {
          ARMA.toast(
            j.state === "cancelled" ? "Tapşırıq dayandırıldı." :
              j.state === "error" ? "Tapşırıq xəta ilə bitdi." :
                "Yoxlama bitdi — " + j.done + "/" + j.total + " kod.",
            j.state === "done" ? "ok" : "warn");
          setTimeout(function () { window.location.reload(); }, 900);
          return;
        }
        setTimeout(tick, every);
      }).catch(function (e) {
        ARMA.toast("Proqres oxunmadı: " + e.message, "warn");
        setTimeout(tick, every * 2);
      });
    }
    tick();
  };

  /* ------------------------------------------------------------ başlanğıc */
  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    ARMA.initTheme();

    Array.prototype.forEach.call(
      document.querySelectorAll(".theme-switch button"), function (b) {
        b.addEventListener("click", function () { ARMA.setTheme(b.dataset.theme); });
      });

    // mobil naviqasiya
    var toggle = document.getElementById("navToggle");
    var sidebar = document.querySelector(".sidebar");
    if (toggle && sidebar) {
      toggle.addEventListener("click", function () {
        var open = sidebar.classList.toggle("open");
        toggle.setAttribute("aria-expanded", String(open));
      });
    }

    // data-copy / data-wa atributu olan hər düymə avtomatik işləyir
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-copy]"), function (b) {
        b.addEventListener("click", function () {
          ARMA.copy(ARMA.block(b.dataset.copy.split("|"), b.dataset.maya), b);
        });
      });
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-wa]"), function (b) {
        b.addEventListener("click", function () {
          ARMA.wa(ARMA.block(b.dataset.wa.split("|"), b.dataset.maya), b);
        });
      });
  });
})();
