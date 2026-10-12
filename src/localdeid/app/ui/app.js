// Local DeID window (SPEC 5). All note text is set with textContent, never as HTML.
// Engine offsets are code points; the DOM uses UTF-16, so offsets are converted here only.
"use strict";

const S = { view: null, selRow: 0, restore: null, ready: false, restoreSeq: 0, opens: 0, shown: 0, wiped: 0, deletes: 0,
  loads: {}, deleted: new Set() };

// Check view replies. Each opening of a note (paste, History, hotkey) takes a number
// when it starts; its reply shows only if no later-started opening is on screen.
// Edit replies (keep, hide, undo, copy) apply only to the same note, never older.
function beginOpen() { return ++S.opens; }
function showOpened(gen, view) {
  if (!view || gen < S.shown || S.deleted.has(view.id)) return false;
  S.shown = gen;
  if (S.view && view.id === S.view.id && view.rev < S.view.rev) return true;  // never an older revision
  S.selRow = 0;  // a newly opened note starts at its first row
  renderCheck(view);
  return true;
}
function showEdit(view) {
  if (view && S.view && view.id === S.view.id && view.rev >= S.view.rev && !S.deleted.has(view.id)) renderCheck(view);
}

// After a session is deleted, nothing of it stays on screen.
function forgetSession(id) {
  S.deleted.add(id);
  if (S.view && S.view.id === id) {
    S.view = null;
    for (const x of ["text", "rows", "failures"]) $(x).replaceChildren();
    for (const x of ["child", "lang", "counts", "tip"]) $(x).textContent = "";
    $("failures").hidden = true;
  }
  if (S.restore && S.restore.candidates && S.restore.candidates.some((c) => c.id === id)) {
    if (S.restore.best && S.restore.best[0] === id) S.restore.best = null;
    renderRestore(S.restore);  // the question is shown again without the deleted session
  }
  if (S.restore && S.restore.id === id) {
    S.restore = null;  // late replies for this session are dropped in renderRestore
    for (const x of ["src", "restored", "issues"]) $(x).replaceChildren();
    for (const x of ["sbs", "restoreactions", "confirm"]) $(x).hidden = true;
  }
}
const $ = (id) => document.getElementById(id);
const cp = (s) => Array.from(s).length; // code points of a JS string

function api() {
  if (!window.pywebview || !window.pywebview.api) throw new Error("bridge not ready");
  return window.pywebview.api;
}

function notice(text, isError) {
  const n = $("notice");
  n.textContent = text;
  n.className = isError ? "error" : "";
  n.hidden = !text;
  if (text && !isError) setTimeout(() => { if (n.textContent === text) n.hidden = true; }, 6000);
}

async function callQuiet(name, ...args) {
  let out;
  try { out = await api()[name](...args); } catch (e) { out = { ok: false, message: "The app is busy. Try again." }; }
  return out || { ok: false, message: "Something went wrong." };
}

async function call(name, ...args) {
  const out = await callQuiet(name, ...args);
  if (!out.ok) notice(out.message || "Something went wrong.", true);
  return out;
}

// A yes/no question in the page (SPEC 4.3: un-hiding a caseload name needs a confirmation).
function ask(text) {
  return new Promise((resolve) => {
    const box = $("askbox"), prev = document.activeElement;
    $("ask-text").textContent = text;
    box.hidden = false;
    $("ask-no").focus();
    const done = (answer) => {
      box.hidden = true;
      $("ask-yes").onclick = $("ask-no").onclick = box.onkeydown = null;
      if (prev && prev.focus) prev.focus();
      resolve(answer);
    };
    $("ask-yes").onclick = () => done(true);
    $("ask-no").onclick = () => done(false);
    box.onkeydown = (e) => { if (e.key === "Escape") { e.preventDefault(); done(false); } };
  });
}

function show(view) {
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== view;
  for (const b of document.querySelectorAll("nav button"))
    b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false");
  if (view === "history") loadHistory();
  if (view === "caseload") loadCaseload();
  if (view === "settings") loadSettings();
}

// ------------------------------------------------------------------ check view

function renderCheck(v) {
  S.view = v;
  $("child").textContent = v.child || "No child found";
  $("lang").textContent = v.language;
  $("counts").textContent = `${v.counts.hidden} hidden · ${v.counts.unsure} unsure (hidden)` +
    (v.counts.checks ? ` · ${v.counts.checks} to check` : " · No unsure words found.") +
    (v.nfc_changed ? " · Accents were normalized when pasted." : "");
  const f = $("failures");
  f.replaceChildren();
  if (v.blocked) {
    const p = document.createElement("p");
    p.textContent = "Copy is blocked. Hide or keep each word below, then copy.";
    f.appendChild(p);
    for (const x of v.failures) {
      const d = document.createElement("div");
      d.textContent = `${x.code}: “${x.word}” ${x.detail || ""}`;
      f.appendChild(d);
    }
  }
  f.hidden = !v.blocked;
  const t = $("text");
  t.replaceChildren();
  let pos = 0;
  for (const seg of v.segments) {
    const el = document.createElement("span");
    el.dataset.cp = String(pos);
    if (seg.tag) {
      const kind = seg.hidden ? (seg.sure ? "hidden" : "unsure") : "kept";
      el.className = `chip ${kind}`;
      el.textContent = seg.t;
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = `${seg.tag}${seg.sure ? "" : "?"}`;
      tag.setAttribute("aria-hidden", "true");
      el.appendChild(tag);
      el.setAttribute("aria-label", `${seg.t}: ${kind === "kept" ? "kept" : "hidden as " + seg.tag}${seg.sure ? "" : ", unsure"}`);
      el.dataset.key = seg.key;
    } else {
      el.textContent = seg.t;
    }
    t.appendChild(el);
    pos += cp(seg.t);
  }
  const ol = $("rows");
  ol.replaceChildren();
  v.rows.forEach((r, i) => {
    const li = document.createElement("li");
    li.className = (r.check ? "check " : "") + (i === S.selRow ? "sel" : "");
    li.tabIndex = -1;
    li.setAttribute("aria-label", `${r.value}, ${r.hidden ? "hidden" : "kept"}, ${r.mentions} mentions, ${r.reason}`);
    const v1 = document.createElement("div");
    const val = document.createElement("span");
    val.className = "value";
    val.textContent = r.value;
    v1.appendChild(val);
    v1.append(` → ${r.hidden ? "[" + r.tag + "]" : "kept"}${r.sure ? "" : " (unsure)"}`);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${r.mentions} mention${r.mentions === 1 ? "" : "s"} · ${r.reason}`;
    li.append(v1, meta);
    for (const c of r.contexts) {
      const d = document.createElement("div");
      d.className = "ctx";
      d.textContent = `…${c}…`;
      li.appendChild(d);
    }
    const b = document.createElement("button");
    b.textContent = r.hidden ? "Keep (un-hide)" : "Hide";
    b.addEventListener("click", () => decide(r.key, !r.hidden));
    li.appendChild(b);
    ol.appendChild(li);
  });
  $("copy").disabled = v.blocked;
  $("copytpl").disabled = v.blocked;
  $("undo").disabled = !v.can_undo;
  show("check");
}

async function decide(key, hidden) {
  if (!S.view) return;
  const sid = S.view.id;  // the note the question is about, even if another note opens meanwhile
  let out = await callQuiet("decide", sid, key, hidden, false);
  if (!S.view || S.view.id !== sid) return;  // another note opened meanwhile
  if (!out.ok && out.error === "confirm-roster") {
    if (!(await ask(out.message))) return;
    if (!S.view || S.view.id !== sid) {
      notice("Another note opened, so nothing changed. Try again in this note.", true);
      return;
    }
    out = await callQuiet("decide", sid, key, hidden, true);
  }
  if (out.ok) showEdit(out.view);
  else notice(out.message || "Something went wrong.", true);
}

function selectionOffsets() {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const r = sel.getRangeAt(0);
  const toCp = (node, off) => {
    let el = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    while (el && el.dataset && el.dataset.cp === undefined) el = el.parentElement;
    if (!el || el.dataset.cp === undefined) return null;
    const base = Number(el.dataset.cp);
    const textNode = el.firstChild && el.firstChild.nodeType === Node.TEXT_NODE ? el.firstChild : null;
    const len = textNode ? cp(textNode.textContent) : 0;
    if (node === textNode) return base + cp(node.textContent.slice(0, off));
    if (node === el) return base + (off === 0 ? 0 : len);
    return base + len;  // inside a chip's tag label: the label is not note text, so the chip's end
  };
  const a = toCp(r.startContainer, r.startOffset);
  const b = toCp(r.endContainer, r.endOffset);
  if (a === null || b === null || b <= a) return null;
  return [a, b];
}

async function hideSelection() {
  const o = selectionOffsets();
  if (!S.view || !o) { notice("Select words in the note text first, then press H.", true); return; }
  const out = await call("hide_span", S.view.id, o[0], o[1]);
  if (out.ok) showEdit(out.view);
}

async function copy(withTemplate) {
  if (!S.view) return;
  const out = await call("copy", S.view.id, withTemplate);
  if (out.ok) {
    showEdit(out.view);
    notice(out.notice);
    $("tip").textContent = out.tip || "";
  }
}

// ------------------------------------------------------------------ restore view

function renderRestore(res) {
  if (res && res.id && S.deleted.has(res.id)) return;  // that session was deleted
  if (res && res.candidates) res.candidates = res.candidates.filter((c) => !S.deleted.has(c.id));
  S.restore = res;
  $("issues").replaceChildren();
  $("confirm").hidden = true;
  $("sbs").hidden = true;
  $("restoreactions").hidden = true;
  if (!res) return;
  if (res.status === "no-session") {
    addIssue(true, `No session has ref ${res.refs.join(", ")}. Restore drafts made from notes you checked here.`);
  } else if (res.status === "confirm") {
    const isBest = (c) => !!res.best && c.id === res.best[0] && c.ref === res.best[1];
    const best = res.candidates.find(isBest) || null;
    $("confirm").hidden = false;
    $("confirmchild").textContent = best ? best.child || best.ref : "Which session is this draft for?";
    const box = $("cands");
    box.replaceChildren();
    for (const c of res.candidates) {
      // One button per copy: a note copied twice with different tags has two mappings.
      const copies = c.copies && c.copies.length ? c.copies : [{ ref: c.ref }];
      copies.forEach((k) => {
        const b = document.createElement("button");
        b.textContent = `${k.child || c.child || "?"} · ref ${k.ref}`;  // each copy names its own child
        if (res.best && c.id === res.best[0] && k.ref === res.best[1]) b.className = "primary";
        b.addEventListener("click", async () => {
          const seq = ++S.restoreSeq;
          const out = await call("restore_confirm", c.id, k.ref, res.token);
          if (out.ok && seq === S.restoreSeq) renderRestore(out.restore);
        });
        box.appendChild(b);
      });
    }
    if (!res.candidates.length) addIssue(true, "No saved session matches this draft.");
  } else if (res.status === "restored") {
    for (const i of res.issues) addIssue(i.blocking, issueText(i));
    $("src").textContent = res.source;
    const pre = $("restored");
    pre.replaceChildren();
    if (!res.blocked) {
      const chars = Array.from(res.text);
      let pos = 0;
      for (const [a, b] of res.spans) {
        pre.append(chars.slice(pos, a).join(""));
        const m = document.createElement("mark");
        m.className = "restored";
        m.textContent = chars.slice(a, b).join("");
        pre.appendChild(m);
        pos = b;
      }
      pre.append(chars.slice(pos).join(""));
      $("restoreactions").hidden = false;
    }
    $("sbs").hidden = false;
  }
  show("restore");
}

function issueText(i) {
  const t = {
    "unknown-tag": `The draft has a tag this session never made: ${i.text}. Fix it in the draft and restore again.`,
    "unknown-escape": `The draft has an unknown escape mark: ${i.text}.`,
    "fragment": `The draft has a broken tag: “${i.text}”. Fix it in the draft and restore again.`,
    "wrong-ref": i.text + " Restore it from its own session.",
    "no-session": i.text,
    "missing-tag": `${i.text}: the AI dropped it. Check that the draft is still right.`,
    "new-number": `The draft has a number that is not in your note: ${i.text}. Check it.`,
  }[i.code];
  return t || i.text;
}

function addIssue(block, text) {
  const d = document.createElement("div");
  d.className = block ? "block" : "warn";
  d.textContent = (block ? "Blocked: " : "Check: ") + text;
  $("issues").appendChild(d);
}

// ------------------------------------------------------------------ other views

async function loadHistory() {
  const wiped = S.wiped, seq = (S.loads.history = (S.loads.history || 0) + 1);
  const out = await call("history");
  if (wiped !== S.wiped || seq !== S.loads.history) return;  // a newer list, or Delete everything
  const tb = $("hist");
  tb.replaceChildren();
  if (!out.ok) return;
  const names = { check: "Needs check", waiting: "Waiting for draft", done: "Done" };
  for (const r of out.rows.filter((x) => !S.deleted.has(x.id))) {
    const tr = document.createElement("tr");
    const td = (t) => { const c = document.createElement("td"); c.textContent = t; tr.appendChild(c); return c; };
    td(r.child || "—");
    td(new Date(r.updated * 1000).toLocaleString());
    td(r.ref);
    td(names[r.state] + (r.days_left < 3 ? ` · deleted in ${Math.max(0, r.days_left)} days` : ""));
    const act = td("");
    const open = document.createElement("button");
    open.textContent = "Open";
    open.addEventListener("click", async () => {
      const gen = beginOpen();
      const o = await call("open", r.id);
      if (o.ok) showOpened(gen, o.view);
    });
    const done = document.createElement("button");
    done.textContent = "Mark done";
    done.addEventListener("click", async () => { await call("set_state", r.id, "done"); loadHistory(); });
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      const out = await call("delete", r.id);
      if (out.ok) forgetSession(r.id);
      loadHistory();
    });
    act.append(open, done, del);
    tb.appendChild(tr);
  }
}

// A read never overwrites what the clinician typed while it was on its way, and a newer read wins.
async function loadInto(op, ids, apply, saveId) {
  const wiped = S.wiped, seq = (S.loads[op] = (S.loads[op] || 0) + 1);  // per kind of read
  $(saveId).disabled = true;  // a save before the saved values arrive would overwrite them with blanks
  for (const id of ids) $(id).readOnly = true;  // and no typing into fields that are about to fill
  const before = ids.map((id) => $(id).value);
  const out = await call(op);
  if (seq === S.loads[op]) for (const id of ids) $(id).readOnly = false;  // the latest read unlocks
  if (!out.ok || wiped !== S.wiped || seq !== S.loads[op]) return;
  // fill only the fields the clinician did not touch meanwhile; her edits stay
  apply(out, new Set(ids.filter((id, i) => $(id).value === before[i])));
  $(saveId).disabled = false;
}

async function loadCaseload() {
  await loadInto("get_caseload", ["kids"], (out, free) => { if (free.has("kids")) $("kids").value = out.text; },
    "savekids");
}

async function loadSettings() {
  await loadInto("get_settings", ["keep", "employer", "tplen", "tples", "aitip"], applySettings, "savesettings");
}

function applySettings(out, free) {
  const s = out.settings;
  const vals = { keep: s.keep_names.join("\n"), employer: s.employer_names.join("\n"), tplen: s.template_en,
    tples: s.template_es, aitip: s.ai_tip };
  for (const [id, v] of Object.entries(vals)) if (free.has(id)) $(id).value = v;
}

// ------------------------------------------------------------------ events

function bind() {
  for (const b of document.querySelectorAll("nav button")) b.addEventListener("click", () => show(b.dataset.view));
  $("paste").addEventListener("paste", () => setTimeout(runPaste, 0));
  $("run").addEventListener("click", runPaste);
  $("copy").addEventListener("click", () => copy(false));
  $("copytpl").addEventListener("click", () => copy(true));
  $("undo").addEventListener("click", async () => {
    if (!S.view) return;
    const out = await call("undo", S.view.id);
    if (out.ok) showEdit(out.view);
  });
  $("dorestore").addEventListener("click", async () => {
    const seq = ++S.restoreSeq;
    const out = await call("restore", $("draft").value);
    if (out.ok && seq === S.restoreSeq) renderRestore(out.restore);  // a newer restore wins
  });
  $("copyrestored").addEventListener("click", async () => {
    if (!S.restore) return;
    const out = await call("copy_restored", S.restore.id, S.restore.rtoken || "");
    if (out.ok) notice(out.notice);
  });
  $("markdone").addEventListener("click", async () => {
    if (!S.restore) return;
    const out = await call("set_state", S.restore.id, "done");
    if (out.ok) notice("Marked done.");
  });
  $("savekids").addEventListener("click", async () => {
    const out = await call("set_caseload", $("kids").value);
    if (out.ok) notice(`Saved ${out.children} ${out.children === 1 ? "child" : "children"}.`);
  });
  $("savesettings").addEventListener("click", async () => {
    const out = await call("set_settings", {
      keep_names: $("keep").value, employer_names: $("employer").value, template_en: $("tplen").value,
      template_es: $("tples").value, ai_tip: $("aitip").value });
    if (out.ok) notice("Settings saved.");
  });
  $("selftest").addEventListener("click", async () => {
    const ul = $("selftestout");
    ul.replaceChildren();
    const li0 = document.createElement("li");
    li0.textContent = "Running…";
    ul.appendChild(li0);
    const out = await call("selftest");
    ul.replaceChildren();
    if (!out.ok) return;
    for (const c of out.selftest.checks) {
      const li = document.createElement("li");
      li.textContent = `${c.ok ? "Pass" : "FAIL"} · ${c.name}: ${c.detail}`;
      ul.appendChild(li);
    }
  });
  $("deleteall").addEventListener("click", async () => {
    S.deletes++;  // a document being read is dropped from now on, before the reply
    const out = await call("delete_everything", $("deleteconfirm").value);
    if (out.ok) {
      // nothing deleted may stay on screen, and no reply on its way may bring it back
      S.view = null; S.restore = null;
      S.shown = ++S.opens; S.restoreSeq++; S.wiped++;
      for (const id of ["text", "rows", "src", "restored", "cands", "issues", "hist", "selftestout", "failures"])
        $(id).replaceChildren();
      for (const id of ["paste", "draft", "kids", "keep", "employer", "tplen", "tples", "aitip", "deleteconfirm"])
        $(id).value = "";
      for (const id of ["sbs", "restoreactions", "confirm", "failures"]) $(id).hidden = true;
      for (const id of ["child", "lang", "counts", "confirmchild", "tip"]) $(id).textContent = "";
      show("home");
      notice("Everything was deleted.");
    }
  });
  $("trysample").addEventListener("click", () => {
    $("paste").value = "Name: Mateo Ruiz   DOB: 03/14/2021\nMother: Lucía Ruiz (919) 555-0142\n" +
      "S: Mom reports Mateo is saying more words at home.\nO: Mateo produced /k/ in 8/10 trials with mod cues " +
      "at Little Sprouts Daycare.\nA: Progress toward IFSP outcome 2.\nP: Continue 1x/week. CPT 92507.";
    $("firstrun").hidden = true;
    show("home");
    call("set_settings", { first_run_done: true });
  });
  $("frdone").addEventListener("click", async () => {
    $("firstrun").hidden = true;
    await call("set_settings", { first_run_done: true });
  });
  document.addEventListener("keydown", onKey);
  // typing a new draft cancels any restore still on its way (it would overwrite the draft)
  $("draft").addEventListener("input", () => { S.restoreSeq++; });
  // Any other route to a native copy (menu, context menu) out of the Check view copies nothing.
  document.addEventListener("copy", (e) => {
    const sel = window.getSelection();
    const touches = (id) => {
      for (let i = 0; sel && i < sel.rangeCount; i++) if (sel.getRangeAt(i).intersectsNode($(id))) return true;
      return false;
    };
    // any part of a selection in the note or the restored panes: real values never leave natively
    if (touches("check")) {
      e.preventDefault();
      notice("Use Copy: it copies the note with the tags.", true);
    } else if (touches("sbs")) {
      e.preventDefault();
      notice("Use Copy restored draft: it clears itself from the clipboard.", true);
    }
  });
  // A dropped document opens in Check. Every drop is stopped first, so a dropped link
  // or page never loads in the window.
  document.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (hasFiles(e)) {
      e.dataTransfer.dropEffect = "copy";
      document.body.classList.add("dropping");
      clearTimeout(S.dropTimer);
      S.dropTimer = setTimeout(() => document.body.classList.remove("dropping"), 200);
    }
  });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    document.body.classList.remove("dropping");
    const files = e.dataTransfer ? e.dataTransfer.files : null;
    if (!files || files.length === 0) return;
    if (files.length > 1) { notice("Drop one document at a time.", true); return; }
    openDocument(files[0]);
  });
  $("opendoc").addEventListener("click", chooseDocument);
}

// Documents (SPEC 5.4). Only the extension and the bytes go to the app, never the
// file's name: a name can hold a child's name.
const DOC_EXTS = [".docx", ".doc", ".pdf", ".rtf", ".odt", ".txt", ".md"];
const DOC_MAX = 25 * 1024 * 1024;

function hasFiles(e) {
  return !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files"));
}

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function openDocument(file) {
  const name = file.name || "";
  const dot = name.lastIndexOf(".");
  const ext = dot < 0 ? "" : name.slice(dot).toLowerCase();
  if (!DOC_EXTS.includes(ext)) {
    notice("This kind of file cannot be read. Use a Word (.docx or .doc), PDF, RTF, or text file.", true);
    return;
  }
  if (file.size > DOC_MAX) {  // checked before reading: a huge file is never loaded
    notice("This file is larger than 25 MB. Save a shorter copy, or copy the text and paste it.", true);
    return;
  }
  const gen = beginOpen();
  // A Delete everything that finishes while the file is read cancels this import,
  // here and in the app (which compares its own count, read now).
  const deletes = S.deletes;
  const st = await callQuiet("state");
  const wipes = st.ok && Number.isInteger(st.wipes) ? st.wipes : null;
  notice("Reading the document…");
  let data;
  try { data = toBase64(await file.arrayBuffer()); } catch (err) {
    notice("This file cannot be read. Open it, save it again as Word or PDF, and try again.", true);
    return;
  }
  if (deletes !== S.deletes) { notice(""); return; }
  const out = await call("open_document", ext, data, wipes);
  if (out.ok) { notice(""); showOpened(gen, out.view); }
}

async function chooseDocument() {
  const gen = beginOpen();
  const out = await call("choose_document");
  if (out.ok && out.view) { notice(""); showOpened(gen, out.view); }
}

async function runPaste() {
  const text = $("paste").value;
  if (!text.trim()) return;
  const gen = beginOpen();
  const out = await call("paste", text);
  if (!out.ok) return;
  if (showOpened(gen, out.view) && $("paste").value === text) $("paste").value = "";  // never wipe newer text

}

function onKey(e) {
  if (!$("askbox").hidden) return;  // the question takes the keys
  const typing = ["TEXTAREA", "INPUT"].includes(document.activeElement && document.activeElement.tagName);
  if (typing || $("check").hidden || !S.view) return;
  const rows = S.view.rows;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    S.selRow = Math.max(0, Math.min(rows.length - 1, S.selRow + (e.key === "ArrowDown" ? 1 : -1)));
    renderCheck(S.view);
    const li = $("rows").children[S.selRow];
    if (li) li.focus();
    e.preventDefault();
  } else if ((e.key === "u" || e.key === "U") && !e.metaKey && rows[S.selRow]) {
    decide(rows[S.selRow].key, false);
  } else if ((e.key === "h" || e.key === "H") && !e.metaKey) {
    if (window.getSelection() && !window.getSelection().isCollapsed) hideSelection();
    else if (rows[S.selRow]) decide(rows[S.selRow].key, true);
  } else if (e.key === "z" && e.metaKey) {
    $("undo").click();
    e.preventDefault();
  } else if (e.key === "c" && e.metaKey) {
    e.preventDefault();  // ⌘C in Check is Copy (SPEC 5.2), never the real values of a selection
    copy(false);
  }
}

// Called from Python (hotkeys) with evaluate_js.
window.LocalDeID = {
  beginOpen: () => beginOpen(),  // a hotkey takes its number when it starts
  showCheck: (v, gen) => showOpened(typeof gen === "number" ? gen : beginOpen(), v),
  beginRestore: () => ++S.restoreSeq,  // a hotkey restore takes its number when it starts
  showRestore: (res, draft, seq) => {
    if (typeof seq === "number" ? seq !== S.restoreSeq : false) return;  // a newer restore started
    if (res && res.id && S.deleted.has(res.id)) return;  // its session was deleted: keep the draft box
    if (typeof seq !== "number") S.restoreSeq++;
    if (typeof draft === "string") $("draft").value = draft;
    renderRestore(res);
  },
  status: (text) => { $("engine").textContent = text; },
  notice: (text, error) => notice(text, error),
};

window.addEventListener("pywebviewready", async () => {
  S.ready = true;
  bind();
  const st = await call("state");
  if (st.ok && st.first_run) $("firstrun").hidden = false;
});
