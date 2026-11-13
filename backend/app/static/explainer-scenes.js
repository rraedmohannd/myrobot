/* Scenes of the program explainers (docs/PROTOCOL.md `explainer.segment`), shared by the robot
   screen (/robot: live presentation and attract mode) and the admin preview (/admin).

   Everything is local: inline SVG line drawings, CSS animations and a vendored QR generator
   (static/vendor/qrcode). No CDN, no images from the internet. The drawings are original,
   simple illustrations (no logos, no people, no copied artwork).

     const ctl = ExplainerScenes.render(container, scene, {lang: "ar"});
     ctl.update(progress, seconds);   // progress 0..1 of the segment's speech (null = unknown)

   The scene design size is 620 x 420 px; pages scale the container with CSS transforms. */
(function (global) {
  "use strict";

  const CSS = `
  .xs { --xs-ink:#8fe9ff; --xs-warm:#ffb648; --xs-text:#f3f6f8; --xs-muted:#9db0bd; --xs-panel:#142430; --xs-line:#24394a;
        width:620px; height:420px; position:relative; overflow:hidden; color:var(--xs-text); display:flex; flex-direction:column;
        align-items:center; justify-content:center; gap:10px; text-align:center; font-weight:600; }
  .xs * { box-sizing:border-box; }
  .xs svg.xs-art { width:300px; height:225px; flex:none; }
  .xs .ink, .xs .ink2 { fill:none; stroke:var(--xs-ink); stroke-width:4; stroke-linecap:round; stroke-linejoin:round;
        stroke-dasharray:1; stroke-dashoffset:1; animation:xs-draw 1.1s ease forwards; animation-delay:calc(var(--i, 0) * .2s); }
  .xs .ink2 { stroke:var(--xs-warm); }
  @keyframes xs-draw { to { stroke-dashoffset:0; } }
  .xs-fade { opacity:0; animation:xs-fade .7s ease forwards; }
  @keyframes xs-fade { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
  .xs-name { font-size:40px; line-height:1.25; font-weight:700; max-width:596px; }
  .xs-name.long { font-size:32px; }
  .xs-tag { font-size:23px; color:var(--xs-ink); font-weight:400; max-width:560px; }
  .xs-head { font-size:27px; color:var(--xs-ink); font-weight:700; max-width:580px; }
  .xs-pen { font-size:15px; color:var(--xs-muted); font-weight:400; }
  .xs-corner { position:absolute; left:6px; bottom:0; width:170px !important; height:128px !important; opacity:.22; }
  .xs[dir="ltr"] .xs-corner { left:auto; right:6px; }
  /* keypoints: written word by word */
  .xs-points { list-style:none; margin:0; padding:0 26px; width:100%; display:flex; flex-direction:column; gap:14px; text-align:start; }
  .xs-points li { font-size:31px; line-height:1.35; display:flex; gap:12px; align-items:baseline; min-height:42px; }
  .xs-points li::before { content:""; flex:none; width:14px; height:14px; border-radius:4px; background:var(--xs-warm); transform:translateY(-2px) rotate(45deg); opacity:.25; transition:opacity .3s; }
  .xs-points li.on::before { opacity:1; }
  .xs-w { opacity:0; transition:opacity .25s; }
  .xs-w.on { opacity:1; }
  .xs-caret { display:inline-block; width:3px; height:1em; background:var(--xs-ink); vertical-align:-0.15em; margin:0 3px; animation:xs-blink .8s steps(2) infinite; }
  @keyframes xs-blink { 50% { opacity:0; } }
  /* stat */
  .xs-num { font-size:150px; line-height:1; font-weight:800; color:var(--xs-ink); font-variant-numeric:tabular-nums; direction:ltr; }
  .xs-unit { font-size:38px; font-weight:700; }
  /* chips */
  .xs-chips { display:flex; flex-wrap:wrap; gap:12px; justify-content:center; padding:0 18px; }
  .xs-chip { font-size:23px; font-weight:600; padding:8px 18px; border-radius:999px; background:var(--xs-panel); border:2px solid var(--xs-ink);
             opacity:0; transform:scale(.6); transition:opacity .35s, transform .35s cubic-bezier(.3,1.6,.5,1); }
  .xs-chip.on { opacity:1; transform:none; }
  /* timeline */
  .xs-steps { display:flex; gap:8px; width:100%; padding:0 14px; align-items:flex-start; justify-content:center; }
  .xs-step { flex:1; min-width:0; display:flex; flex-direction:column; align-items:center; gap:8px; opacity:.18; transition:opacity .4s; position:relative; }
  .xs-step.on { opacity:1; }
  .xs-step i { width:42px; height:42px; border-radius:50%; border:3px solid var(--xs-ink); display:flex; align-items:center; justify-content:center; font-style:normal; font-size:20px; background:#0d1820; z-index:1; }
  .xs-step.on i { background:var(--xs-ink); color:#06222b; }
  .xs-step::before { content:""; position:absolute; top:20px; inset-inline-start:-50%; width:100%; height:3px; background:var(--xs-line); }
  .xs-step:first-child::before { display:none; }
  .xs-step b { font-size:19px; line-height:1.3; }
  .xs-step span { font-size:15px; font-weight:400; color:var(--xs-text); background:var(--xs-panel); border:1px solid var(--xs-line); border-radius:8px; padding:3px 7px; line-height:1.35; }
  /* diagram */
  .xs-flow { display:flex; align-items:center; justify-content:center; gap:6px; width:100%; padding:0 12px; }
  .xs-node { font-size:23px; padding:16px 14px; min-width:110px; border-radius:14px; background:var(--xs-panel); border:2px solid var(--xs-ink); opacity:0; transform:translateY(14px); transition:opacity .4s, transform .4s; }
  .xs-node.on { opacity:1; transform:none; }
  .xs-arrow { width:44px; height:24px; flex:none; opacity:0; transition:opacity .4s; }
  .xs-arrow.on { opacity:1; }
  .xs[dir="rtl"] .xs-arrow { transform:scaleX(-1); }
  /* media + qr */
  .xs-media { width:520px; height:292px; border-radius:14px; overflow:hidden; background:#000; }
  .xs-media img, .xs-media video { width:100%; height:100%; object-fit:cover; display:block; }
  .xs-qr { background:#fff; padding:14px; border-radius:16px; line-height:0; }
  .xs-qr canvas { width:230px; height:230px; image-rendering:pixelated; }
  .xs-url { font-size:14px; color:var(--xs-muted); font-weight:400; direction:ltr; max-width:560px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  @media (prefers-reduced-motion: reduce) { .xs .ink, .xs .ink2 { animation-duration:.01s; animation-delay:0s; } }
  `;

  // ---------- original line drawings, one per program (viewBox 320 x 240) ----------
  const ART = {
    // robot head and a small neural network
    "intelligent-systems-engineering": `
      <rect class="ink" x="38" y="72" width="112" height="100" rx="18"/>
      <path class="ink" d="M94 72 V46"/><circle class="ink2" cx="94" cy="38" r="8"/>
      <circle class="ink" cx="72" cy="112" r="11"/><circle class="ink" cx="116" cy="112" r="11"/>
      <path class="ink" d="M68 146 Q94 162 120 146"/>
      <rect class="ink" x="24" y="104" width="14" height="34" rx="5"/><rect class="ink" x="150" y="104" width="14" height="34" rx="5"/>
      <path class="ink2" d="M196 70 L246 96 M196 122 L246 96 M196 122 L246 148 M196 174 L246 148 M246 96 L294 122 M246 148 L294 122"/>
      <circle class="ink" cx="196" cy="70" r="10"/><circle class="ink" cx="196" cy="122" r="10"/><circle class="ink" cx="196" cy="174" r="10"/>
      <circle class="ink" cx="246" cy="96" r="10"/><circle class="ink" cx="246" cy="148" r="10"/><circle class="ink2" cx="294" cy="122" r="11"/>`,
    // sun, solar panel and wind turbine
    "renewable-energy-engineering": `
      <circle class="ink2" cx="58" cy="54" r="22"/>
      <path class="ink2" d="M58 18 V8 M58 100 V90 M22 54 H12 M104 54 H94 M32 28 L25 21 M84 28 L91 21 M32 80 L25 87 M84 80 L91 87"/>
      <path class="ink" d="M36 200 L66 132 H190 L160 200 Z"/>
      <path class="ink" d="M51 166 H175 M107 132 L77 200 M149 132 L119 200"/>
      <path class="ink" d="M98 200 V218 M70 218 H128"/>
      <path class="ink" d="M252 218 V104"/><circle class="ink2" cx="252" cy="100" r="7"/>
      <path class="ink" d="M252 93 Q264 66 252 40 Q243 66 252 93 Z"/>
      <path class="ink" d="M258 104 Q287 108 304 132 Q276 124 258 104 Z"/>
      <path class="ink" d="M246 104 Q217 108 200 132 Q228 124 246 104 Z"/>
      <path class="ink" d="M206 218 H300"/>`,
    // building facade
    "architectural-engineering": `
      <path class="ink" d="M20 214 H300"/>
      <rect class="ink" x="96" y="46" width="132" height="168"/>
      <path class="ink2" d="M88 46 H236"/>
      <rect class="ink" x="114" y="64" width="24" height="26"/><rect class="ink" x="150" y="64" width="24" height="26"/><rect class="ink" x="186" y="64" width="24" height="26"/>
      <rect class="ink" x="114" y="106" width="24" height="26"/><rect class="ink" x="150" y="106" width="24" height="26"/><rect class="ink" x="186" y="106" width="24" height="26"/>
      <rect class="ink2" x="146" y="160" width="32" height="54"/>
      <rect class="ink" x="114" y="160" width="20" height="26"/><rect class="ink" x="190" y="160" width="20" height="26"/>
      <path class="ink" d="M40 214 V128 H96"/><rect class="ink" x="54" y="144" width="26" height="18"/><rect class="ink" x="54" y="176" width="26" height="18"/>
      <path class="ink" d="M266 214 V176"/><circle class="ink2" cx="266" cy="156" r="22"/>`,
    // room plan with furniture, seen from above
    "interior-design": `
      <path class="ink" d="M92 206 H288 V34 H32 V206 H48"/>
      <path class="ink2" d="M48 206 A44 44 0 0 1 92 162 V206"/>
      <path class="ink2" d="M120 34 H200 M120 42 H200"/>
      <rect class="ink" x="50" y="56" width="96" height="36" rx="8"/><path class="ink" d="M50 68 H146"/>
      <circle class="ink" cx="98" cy="124" r="17"/>
      <rect class="ink" x="206" y="56" width="62" height="92" rx="6"/><rect class="ink2" x="216" y="64" width="42" height="20" rx="5"/>
      <path class="ink" d="M206 104 H268"/>
      <rect class="ink" x="160" y="168" width="60" height="26" rx="4"/>
      <circle class="ink2" cx="262" cy="182" r="12"/><path class="ink2" d="M262 170 V194 M250 182 H274"/>`,
    // pen tool and a bezier curve with its handles
    "graphic-design": `
      <path class="ink" d="M36 184 C 80 40, 190 226, 284 78"/>
      <path class="ink2" d="M36 184 L80 62 M284 78 L208 196"/>
      <rect class="ink" x="28" y="176" width="16" height="16"/><rect class="ink" x="276" y="70" width="16" height="16"/>
      <circle class="ink2" cx="80" cy="62" r="8"/><circle class="ink2" cx="208" cy="196" r="8"/>
      <g transform="translate(120 4) rotate(18 44 60)">
        <path class="ink" d="M44 118 L18 66 L30 30 H58 L70 66 Z"/>
        <path class="ink" d="M44 118 V72"/><circle class="ink2" cx="44" cy="64" r="7"/>
        <rect class="ink" x="26" y="10" width="36" height="20" rx="3"/>
      </g>`,
    // heart and ECG line
    "biomedical-engineering": `
      <path class="ink" d="M160 204 C 62 142 78 44 160 92 C 242 44 258 142 160 204 Z"/>
      <path class="ink2" d="M14 132 H96 L112 104 L130 168 L150 70 L170 156 L184 132 H306"/>
      <path class="ink" d="M262 40 V72 M246 56 H278"/>
      <circle class="ink" cx="262" cy="56" r="26"/>`,
  };
  // generic drawing for a program without its own art: a graduation cap
  const ART_DEFAULT = `
      <path class="ink" d="M160 60 L290 110 L160 160 L30 110 Z"/>
      <path class="ink" d="M90 134 V180 Q160 216 230 180 V134"/>
      <path class="ink2" d="M290 110 V170"/><circle class="ink2" cx="290" cy="178" r="8"/>`;

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  function drawing(slug, extraClass) {
    return `<svg class="xs-art ${extraClass || ""}" viewBox="0 0 320 240" aria-hidden="true">${ART[slug] || ART_DEFAULT}</svg>`;
  }
  // Every stroke draws itself, one after the other ("the robot is drawing").
  function animateStrokes(root) {
    root.querySelectorAll(".ink, .ink2").forEach((el, i) => { el.setAttribute("pathLength", "1"); el.style.setProperty("--i", i); });
  }
  function ensureCss() {
    if (document.getElementById("xs-css")) return;
    const style = document.createElement("style");
    style.id = "xs-css"; style.textContent = CSS;
    document.head.appendChild(style);
  }
  const step = (items, progress, lead) => Math.min(items.length, Math.floor(Math.max(0, progress) * items.length * (lead || 1.15) + 0.999));

  // ---------- one builder per scene type: returns {html, update(progress, seconds, root)} ----------
  const BUILD = {
    title(scene, slug, name) {
      return { html: `${drawing(slug)}<div class="xs-name xs-fade${String(name).length > 26 ? " long" : ""}" style="animation-delay:.5s">${esc(name)}</div>` +
                     (scene.tagline ? `<div class="xs-tag xs-fade" style="animation-delay:1.3s">${esc(scene.tagline)}</div>` : "") };
    },
    keypoints(scene, slug) {
      const points = (scene.points || []).map(p => String(p).split(/\s+/).filter(Boolean));
      const total = points.reduce((n, w) => n + w.length, 0);
      let k = 0;
      const lis = points.map(words => `<li>${`<span>` + words.map(w => `<span class="xs-w" data-k="${k++}">${esc(w)} </span>`).join("") + `</span>`}</li>`).join("");
      return {
        html: (scene.title ? `<div class="xs-head">${esc(scene.title)}</div>` : "") + `<ul class="xs-points">${lis}</ul>` + drawing(slug, "xs-corner"),
        update(progress, seconds, root) {
          const shown = Math.min(total, Math.floor(Math.max(0, progress) * total * 1.2 + 1));
          root.querySelectorAll(".xs-w").forEach(w => w.classList.toggle("on", +w.dataset.k < shown));
          root.querySelectorAll(".xs-points li").forEach(li => li.classList.toggle("on", !!li.querySelector(".xs-w.on")));
          let caret = root.querySelector(".xs-caret");
          const last = root.querySelector(`.xs-w[data-k="${shown - 1}"]`);
          if (shown >= total) { if (caret) caret.remove(); return; }
          if (!caret) { caret = document.createElement("span"); caret.className = "xs-caret"; }
          if (last && caret.previousSibling !== last) last.after(caret);
        },
      };
    },
    stat(scene) {
      const raw = String(scene.value || ""), target = parseFloat(raw.replace(/,/g, ""));
      const countable = /^\d{1,6}$/.test(raw);
      return {
        html: `<div class="xs-num">${countable ? "0" : esc(raw)}</div><div class="xs-unit xs-fade" style="animation-delay:.3s">${esc(scene.unit || "")}</div>` +
              (scene.caption ? `<div class="xs-tag xs-fade" style="animation-delay:1.6s">${esc(scene.caption)}</div>` : ""),
        update(progress, seconds, root) {
          if (!countable) return;
          const t = Math.min(1, Math.max(0, seconds) / 1.8), eased = 1 - Math.pow(1 - t, 3);
          root.querySelector(".xs-num").textContent = String(Math.round(target * eased));   // ends exactly on the sourced number
        },
      };
    },
    courses(scene, slug) {
      const chips = scene.courses || [];
      return {
        html: (scene.title ? `<div class="xs-head">${esc(scene.title)}</div>` : "") +
              `<div class="xs-chips">${chips.map(c => `<span class="xs-chip" dir="auto">${esc(c)}</span>`).join("")}</div>` + drawing(slug, "xs-corner"),
        update(progress, seconds, root) {
          const n = step(chips, progress);
          root.querySelectorAll(".xs-chip").forEach((c, i) => c.classList.toggle("on", i < n));
        },
      };
    },
    timeline(scene) {
      const steps = scene.steps || [];
      return {
        html: (scene.title ? `<div class="xs-head">${esc(scene.title)}</div>` : "") +
              `<div class="xs-steps">${steps.map((s, i) => `<div class="xs-step"><i>${i + 1}</i><b dir="auto">${esc(s.label)}</b>${(s.items || []).map(t => `<span dir="auto">${esc(t)}</span>`).join("")}</div>`).join("")}</div>`,
        update(progress, seconds, root) {
          const n = step(steps, progress);
          root.querySelectorAll(".xs-step").forEach((c, i) => c.classList.toggle("on", i < n));
        },
      };
    },
    diagram(scene) {
      const nodes = scene.nodes || [];
      const arrow = `<svg class="xs-arrow" viewBox="0 0 44 24"><path d="M2 12 H38 M28 3 L40 12 L28 21" fill="none" stroke="#ffb648" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
      return {
        html: (scene.title ? `<div class="xs-head">${esc(scene.title)}</div>` : "") +
              `<div class="xs-flow">${nodes.map((n, i) => (i ? arrow : "") + `<div class="xs-node" dir="auto">${esc(n)}</div>`).join("")}</div>`,
        update(progress, seconds, root) {
          const n = step(nodes, progress, 1.3);
          root.querySelectorAll(".xs-node").forEach((c, i) => c.classList.toggle("on", i < n));
          root.querySelectorAll(".xs-arrow").forEach((c, i) => c.classList.toggle("on", i + 1 < n));
        },
      };
    },
    media(scene, slug, name) {
      const m = scene.media;
      if (!m || !m.url) return BUILD.title({tagline: scene.caption}, slug, name);   // no approved photo or video: the drawing
      const tag = m.kind === "video" ? `<video src="${esc(m.url)}" muted loop autoplay playsinline aria-label="${esc(m.alt || "")}"></video>`
                                     : `<img src="${esc(m.url)}" alt="${esc(m.alt || "")}">`;
      return { html: `<div class="xs-media xs-fade">${tag}</div>` + (scene.caption ? `<div class="xs-tag">${esc(scene.caption)}</div>` : "") };
    },
    qr(scene, slug, name, lang) {
      const url = scene.url && /^https?:\/\//.test(scene.url) && global.qrcode ? scene.url : null;
      if (!url) {
        // No official web address in the source records: nothing to scan, so no code is shown.
        const where = lang === "en" ? "Official study plan: ask the Admission and Registration Department" : "الخطة الدراسية الرسمية: اسأل دائرة القبول والتسجيل";
        return { html: `${drawing(slug)}<div class="xs-name xs-fade" style="font-size:30px">${esc(name)}</div><div class="xs-tag xs-fade" style="animation-delay:.8s">${esc(where)}</div>` };
      }
      let shown = url; try { shown = decodeURI(url); } catch (e) { /* keep the encoded form */ }
      return {
        html: `<div class="xs-qr xs-fade"><canvas></canvas></div><div class="xs-head">${esc(scene.caption || (lang === "en" ? "Scan the code" : "صوّر الكود"))}</div><div class="xs-url">${esc(shown)}</div>`,
        mount(root) {
          const qr = global.qrcode(0, "M"); qr.addData(url, "Byte"); qr.make();
          const n = qr.getModuleCount(), quiet = 2, scale = 6, canvas = root.querySelector("canvas"), ctx = canvas.getContext("2d");
          canvas.width = canvas.height = (n + quiet * 2) * scale;
          ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = "#000";
          for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * scale, (r + quiet) * scale, scale, scale);
        },
      };
    },
  };

  /** Draw `scene` into `container` (it is emptied first). Returns a controller with update(). */
  function render(container, scene, options) {
    ensureCss();
    const o = options || {}, s = scene || {type: "title"};
    const program = s.program || {}, lang = o.lang || (/[؀-ۿ]/.test(program.name || "") ? "ar" : "en");
    const built = (BUILD[s.type] || BUILD.title)(s, program.slug, program.name || "", lang);
    const root = document.createElement("div");
    root.className = "xs"; root.dir = lang === "en" ? "ltr" : "rtl"; root.dataset.type = s.type || "title";
    root.innerHTML = built.html;
    animateStrokes(root);
    container.replaceChildren(root);
    if (built.mount) { try { built.mount(root); } catch (e) { console.warn("scene mount failed", e); } }
    const words = Math.max(8, o.words || 28);
    const ctl = {
      root, type: root.dataset.type,
      /** progress: 0..1 inside the segment's speech, or null when unknown (then time is used). */
      update(progress, seconds) {
        const sec = seconds == null ? 0 : seconds;
        const p = progress == null ? Math.min(1, sec / (words * 0.42)) : progress;
        if (built.update) built.update(p, sec, root);
      },
    };
    ctl.update(0, 0);
    return ctl;
  }

  global.ExplainerScenes = { render, drawing, TYPES: Object.keys(BUILD), PROGRAMS: Object.keys(ART) };
})(window);
