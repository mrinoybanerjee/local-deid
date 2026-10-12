# Local DeID: plan and spec (draft for review)

Status: v6, 2026-10-10. v1 is built (see `README.md`). v5 set the documents to
evaluations, assessments, IFSPs, and daily notes. Transcripts move out of v1.
It also confirms 8 GB RAM and employer approval (see the change log).

v4 merges three independent design reviews of v3 (Fable 5.1, Opus 5.5, and
GPT-6.1 Sol, all at high reasoning), each written from a staff engineer and
senior product designer point of view. Section 12 lists what was adopted and
what was not. The reviews and the research behind this spec are in the
project's private archive repository, not in this repository.

## 1. Verdict: is it possible?

Yes, with one honest limit.

- **"Change nothing else" can be guaranteed by code.** The app never rewrites
  text. It replaces exact character spans and checks the result.
  - Child speech errors ("wabbit") are clinical data, and they stay exactly as
    written.
  - The real risk is a span that is *too wide*, for example "Miss Ana's class"
    swallowing "class". Section 4 handles this with word-boundary snapping
    and a protected-token check.
- **"Catch every identifier" cannot be proven 100% by any system.**
  - On their own kind of notes, the best systems reach about 0.97–0.99
    recall. That holds for English i2b2 and for Spanish MEDDOCAN.
  - On new kinds of text they drop sharply.
  - No benchmark exists for SLP notes or bilingual therapy transcripts. We
    build our own and report real numbers.
- **This use case has two big advantages.**
  - She knows her caseload's names, and the roster is the strongest single
    lever.
  - Small in-domain fine-tunes give large gains in the literature.

## 2. Context and constraints

- **User:** a bilingual (English/Spanish) pediatric SLP employed by a
  company in North Carolina. She works in daycares, schools, and homes,
  often back to back, and writes notes 4–5 hours a day, often in batches.
- **Inputs (v1):** the documents she writes, in English, Spanish, or mixed:
  - **Evaluations and assessments.** These are long, typically 2,000–6,000
    words. They have header fields, score tables, background and birth
    history, medical history, family and language history, and
    recommendations.
  - **IFSPs** (NC Infant-Toddler Program). These include family names, the
    service coordinator, the CDSA office, family concerns in the family's own
    words, routines, other agencies, outcomes, and service dates.
  - **Daily notes.** These are short, with goal data, cueing levels, and child
    productions.
  - v1 takes pasted text, including tables pasted from Word or the EMR as
    tab-separated text. Session transcripts are not a v1 input.
  - She writes in an EMR. v1 is **paste in, paste out only**: no file import
    and no file export. **v1.1 (2026-10-12, the clinician's request):** a
    document dropped on the window, or opened with "Open a document…", is
    read as text and checked like a paste (section 5.4). Output stays
    text: no file export.
- **Hardware:** MacBook Air, M3 or M4, **8 GB RAM (confirmed)**. Chrome and
  ChatGPT will be open at the same time.
- **Install:** Mrinoy builds and installs the app on her Mac, and keeps one
  stable local signing identity across updates.
- **AI step:** she pastes the output into ChatGPT or Claude on the Mac.
  Pasting raw notes from her phone is not safe, and first run says so.
- **Legal reading (not legal advice):**
  - Her notes are very likely PHI under HIPAA through her employer.
  - FERPA applies to school-contract work, and IDEA Part C to early
    intervention.
  - ASHA says to put no identifiable information into generative AI, and to
    follow employer policy.
  - **Employer approval: done.** Mrinoy reported on 2026-10-10 that she has
    written approval. We have not seen the approval document. It should cover
    both (a) AI use on de-identified notes and (b) keeping a small list of
    children's names on her laptop. If it does not cover (b), the app runs
    without a saved caseload.

## 3. Success criteria

| Gate | Target | How we measure |
|---|---|---|
| Text integrity | 100%: characters outside spans unchanged; no replaced span contains a protected token unless the clinician chose it | Automatic check on every run; output blocked on failure |
| **Headline: documents fully clean, before review** | Set per stratum (English / Spanish / mixed × daily note / evaluation / IFSP) before testing. Daily notes ≥ 97%. Evaluation and IFSP targets set after the phase 1 baseline, because long documents compound misses | Hand-written gate set |
| Recall per identifier type | 95% lower bound reported per type and per language; each type sized so the bound is meaningful; types that cannot occur are dropped | Gate set plus synthetic test set |
| Gate set | ≥ 500 identifiers per language plus a mixed stratum and a general-text stratum, from an independent generator, never used for tuning | Built in phase 1 |
| Clinical content kept | Zero hides of allow-listed SLP terms, tests, scores, codes, IPA, or target words | Allow-list suite, English and Spanish |
| Restore | 100% round trip on clean drafts; wrong-session drafts always blocked; residue always blocked | Property tests plus a fuzzer of LLM-style edits |
| Review load | Median check list ≤ 5 items per daily note, ≤ 15 per evaluation or IFSP | Counted locally on her Mac (counts only) |
| Speed | 500-word daily note < 5 s warm; 5,000-word evaluation < 30 s warm; clean-note hotkey round trip < 10 s | Harness on an 8 GB Air |
| Memory | Whole app peak < 2.5 GB | Harness on an 8 GB Air with Chrome open |
| Network | Zero outbound connections from the whole process tree | `nettop` and a LuLu/Little Snitch test |
| Real documents | ≤ 1 identifier missed across 30 real documents (at least 5 evaluations, 5 IFSPs, and 10 daily notes, in both languages), found in a day-two re-check; this is a blocking phase 3 exit | Acceptance run on her Mac; only counts leave it |

## 4. Engineering architecture

### 4.1 Process model

- **UI process:** pywebview (WKWebView) with bundled HTML/JS only.
  - Content Security Policy `default-src 'self'`.
  - Navigation is blocked.
  - Pasted text is rendered as text, never as HTML.
  - The JS bridge exposes a small, validated set of calls.
  - The bridge runs calls on separate threads, so all session changes go
    through one serialized queue.
- **Engine worker:** a separate Python process that holds the model. It can
  be restarted and cancelled, and it drops results for stale input versions.
  If it crashes, the UI restarts it and keeps the session.
- **Network:** blocked inside the processes (sockets raise), offline settings
  are forced, and no remote content is loaded. We verify at the OS level for
  the whole process tree, WebKit content process included.
- **Command-line tool:** the same engine, used by the evaluation harness.
- **Fallback:** a SwiftUI shell, only if pywebview fails the network,
  accessibility, or recovery tests.

### 4.2 One coordinate system

1. The raw pasted string is immutable. It is normalized to NFC once, at
   paste. That is the only text change, and it is shown if it changes
   anything.
2. All spans are code-point offsets into that string.
   - Any detector that sees a transformed copy (accent folding, windows,
     tokenizers) must map its results back through a tested monotonic offset
     map.
   - The UI converts to UTF-16 only at the display edge.
3. Every span snaps outward to whole words. Snapping never extends into a
   protected token.
4. Hypothesis property tests cover NFC/NFD, CRLF, non-breaking spaces,
   zero-width characters, smart quotes, emoji, and window edges.

### 4.3 Pipeline

```
paste ─ protect ─┬─ fields (form labels EN + ES) ┤
                 ├─ rules (EN + ES, always both)  ┐
                 ├─ roster (caseload)             ├─ merge ─ propagate ─ decide ─ render ─ checks ─ clipboard
                 ├─ name gazetteer + context      │
                 └─ Privacy Filter (one model)    ┘
```

| Stage | Job |
|---|---|
| `protect` | Marks clinical tokens: the SLP allow-list (EN/ES), known CPT and ICD-10 codes, test names, scores and fractions ("8/10 trials", "8 de 10 intentos"), IPA, `/slash/` phonemes, quoted target words, SALT codes, and this child's remembered target words. A protect span is a **hard mask** for every detector, with one exception: a roster hit inside a protect span (a sister named Sol; target word "sol") becomes a check item. |
| `fields` | **Form-field parser** (the highest-yield detector for evaluations and IFSPs). It reads "Label: value" lines and table cells with labels in English and Spanish: Name/Nombre, DOB/Fecha de nacimiento, Parent/Guardian/Padre/Madre/Tutor, Address/Dirección, Phone/Teléfono, Medicaid #, Insurance, Physician/PCP/Médico, Referral source, School/Daycare/Escuela/Guardería, Teacher/Maestra, Service Coordinator/Coordinador(a) de servicios, CDSA, Date of evaluation/Fecha de evaluación, Examiner, Interpreter/Intérprete, and the NC ITP child ID. The value becomes a span of the label's type. The label list starts broad (public SLP and EMR templates) and the clinician can add a label from the Check view. SALT headers (`+ Name:`, `+ DOB:`) count as fields. |
| `rules` | Both language packs on all text. **Dates** in all forms, including Spanish lowercase months and weekdays. **Times. Phones** in both languages. **Contact and records:** emails, URLs, addresses (US and Latin American), Medicaid/insurance/record IDs, ZIP codes. **Context triggers:** "Ms./Miss/Mr./Dr. X", "la maestra X", "doña X", "su hermana X", "escuela/guardería/daycare X", "born at / nació en X", "NICU at X", "seen by Dr. X", "referred by X". |
| `roster` | Caseload names, matched without regard to accent or case. One-letter misspellings for names of 6+ letters. A nickname table (Chuy = Jesús, Pepe = José, Lupita = Guadalupe). Surname particles ("de la Cruz"). Names that are also words, such as Ana, Leo, Sol, Rosa, and Luz, become check items per mention. They are never auto-kept. |
| `gazetteer` | Bilingual first-name and surname lists (SSA, Census 2010 CC0, Spanish lists), case-insensitive, with context. A hit next to a kin word, honorific, or name-like position becomes a span. A bare hit becomes a check item. This replaces the v3 capitalization net and truecasing. |
| `privacy_filter` | OpenAI Privacy Filter base (Apache-2.0, multilingual). It is the **only ML model in v1.** The runtime (ONNX q4, about 0.9 GB, or MLX 8-bit, about 1.4 GB) is chosen by the phase 1 harness. It reads 4k-token windows with overlap. |
| `merge` | Union the character coverage first, so no tail of an identifier is lost. Then split the coverage into typed entities. Type priority: protect > roster > rules > model. "Longest" breaks ties only within one source. Every span keeps all its sources. |
| `propagate` | A caught string becomes a span at every whole-word mention. Exception: names that are also words are decided per mention. |
| `decide` | Every check item **defaults to hidden** (fail-closed). The clinician can un-hide items to improve the draft; she never has to approve an item before copying. Un-hiding a roster name needs a confirmation. |
| `render` | Tags only. Each distinct real value gets its own numbered tag, consistent within the session: `[CHILD_1]`, `[PARENT_1]`, `[PHONE_2]`. Text that already looks like a tag is escaped. |
| `checks` | Integrity, protected-token, and leak checks. A failure names the exact word and offers Hide or Keep in place. |

**GLiNER** is a phase 1 experiment only. It ships only if the gate set shows a
school, teacher, or employer gap that the rules and triggers cannot close.

### 4.4 What gets copied

```
ref: K7F2 — keep this line and all [TAGS] exactly as written.
[CHILD_1]: girl, 4;2. Language: Spanish/English.
<tagged text>
```

- The **ref line** binds the draft to its session.
- The **context line** gives the AI gender and age, which are not Safe Harbor
  identifiers under age 90. Without it, Spanish drafts get gender agreement
  wrong.
- With "Copy with my note template", her saved prompt (English or Spanish)
  goes around this block.

### 4.5 Restore

1. Find the ref line.
   - If it is missing, match the draft to a session by fingerprint (numbers,
     target words, rare phrases) and ask her to confirm, with the child's
     name shown large.
   - If two sessions score close, ask.
   - A draft with the wrong ref is blocked.
2. Parse tags with a tolerant grammar: case, spaces, underscores, missing
   brackets, possessives, and a Spanish label table (`[NIÑO_1]`,
   `[MADRE_1]`). Rewrite them to canonical form, then restore exactly.
3. **Block** "Copy restored draft" on any residue:
   - an unknown tag
   - a tag from another session
   - a tag-like fragment
4. **Warn, but do not block:**
   - a tag that disappeared (summaries can drop names)
   - numbers or scores in the draft that are not in the source. These are
     compared after normalizing "80%", "8/10", "8 out of 10", and "ocho de
     diez".
5. Show the source and the restored draft side by side. Restored values are
   green and AI additions are highlighted. The result is labeled "Restored
   draft", not "final note". She still reviews it clinically (ASHA Rule 17).
6. Round-trip property tests check `restore(render(x)) == x`. A fuzzer covers
   LLM-style tag edits and wrong-session pastes.

### 4.6 Storage and security

- **Caseload (minimal):**
  - first name and nicknames
  - surnames
  - names of family members and teachers, with their relationship
  - school or daycare
  - remembered target words
  - No addresses, phone numbers, Medicaid IDs, or dates of birth. Rules catch
    those by format.
- **Sessions:**
  - The original text, the mapping, and the copied output live in one
    encrypted file per session.
  - Each session has its own data key, and deleting the key deletes the
    session.
  - A session is kept until she marks it Done, or for 30 days at most, with a
    warning before deletion. It is never deleted while waiting for a draft.
- **Crypto:**
  - AES-GCM from a vetted library, with unique nonces and authenticated
    metadata.
  - The master key is a non-synchronizing Keychain item tied to the stable
    signing identity.
  - Writes are crash-safe (write, then atomic rename).
- **Location:** `~/Library/Application Support/LocalDeID/`, excluded from
  iCloud and Time Machine.
- **Clipboard:**
  - Writes are marked concealed, so clipboard managers skip them.
  - Our copies clear after 2 minutes.
  - First run warns about Universal Clipboard (iPhone) and clipboard-history
    apps.
- **Logs:** counts and error codes only. No text, and no crash reports that
  contain text.
- **Other:**
  - "Delete everything" is in Settings.
  - Model files are pinned by hash.
  - A self-test checks the hashes, the offline status, and a sample note.

### 4.7 Memory and speed

- One ML model, loaded at launch and kept warm. It unloads under memory
  pressure, and windows are processed one after another.
- Day 1 of phase 1 is the harness: whole-app peak memory and cold and warm
  latency, on a real 8 GB Air with Chrome open, for daily notes and for
  5,000-word evaluations.
- If peak memory goes over budget, we use the smaller runtime or shorter
  windows. We never drop recall silently. "Reduced check" mode is shown in
  the counts header.

### 4.8 Testing and evaluation

- Unit tests for every rule and trigger, in English and Spanish.
- Property tests for coordinates, integrity, and restore.
- A golden corpus: any change in output fails the build until it is approved.
- The harness reports the gates in section 3 per stratum.
- **Data:**
  - Synthetic data is split by family, template, and document type, so
    near-copies do not leak across splits.
  - The gate set comes from an independent generator (section 7), across
    many templates and writing styles. It covers:
    - form fields and score tables
    - birth and medical history (hospitals, physicians)
    - IFSP family narratives (routines, parents' jobs, agencies)
    - bystanders, "doña Lupe", initials
    - text without accents
    - Spanish dates
    - CPT codes and "8/10" scores
  - Tuning uses only the dev data. If the final test fails, we build a fresh
    sealed test set.
- The same input with the same settings always gives the same output. The
  pipeline version is stored with each session.

## 5. UI and UX design

### 5.1 Product shape: clipboard-first

The app lives in the menu bar, with one compact window. The fastest safe path
must also be the easiest path; otherwise she will paste raw notes into ChatGPT.

- **⌃⌥D: de-identify the clipboard.**
  - If the note has no check items, the tagged text goes straight back to the
    clipboard. A small notice says "Hid 9 items · ref K7F2".
  - Otherwise the Check window opens with Copy already enabled, because
    unsure items are hidden by default.
- **⌃⌥R: restore the clipboard draft.** This opens the Restore view.
- **The window** has a paste box (home), the Check view, the Restore view,
  and a short History list.

### 5.2 Views

1. **Paste box (home).** Paste (⌘V) runs at once.
   - The app proposes the child from roster hits ("Mateo R.?"). One click or
     Enter confirms.
   - The child's remembered target words apply automatically.
2. **Check.**
   - **Left pane:** the text. Hidden items are red chips with their tag and
     an icon. Unsure items, which are also hidden, are amber chips with "?".
     She selects text with click-drag to hide a span by hand, so multiword
     names work.
   - **Right pane:** the check list, ranked by risk.
     - Rows are grouped by reason, for example "6 test names — keep all".
     - Each row shows the number of mentions and up to 3 different contexts.
     - Names that are also words are decided per mention.
     - "Always keep" is a separate, explicit action, and it can be undone.
   - **Quasi-identifier prompts** appear only when one fires, as a concrete
     row: "Mom works nights at Food Lion — hide employer?"
   - **Header:** child name (large), language badge, "12 hidden · 3 unsure
     (hidden)", and the reduced-check status if active.
   - **Keys:** ↑↓ move, U un-hide, H hide, ⌘Z undo, ⌘C copy. Shortcuts are
     off while typing.
3. **Copy.** One button, plus "Copy with my note template". A one-line
   reminder of her AI settings (for example "Temporary Chat").
4. **Restore.** Opens from ⌃⌥R or from a History row.
   - It shows the source and the restored draft side by side, with green
     restored values and highlighted additions.
   - Blocks and warnings appear as rows, each with a plain reason and an
     action.
   - Button: "Copy restored draft".
5. **History.** Today's sessions in three states: Needs check, Waiting for
   draft, Done. Each row shows the child, the time, and the ref.
6. **Caseload.**
   - It starts as a box: "Type your kids' names, one per line." Surnames and
     school are optional.
   - It grows from the Check view: "Add [Sofía's sister] to caseload".
7. **Settings.**
   - Note templates (English and Spanish).
   - Word lists (her additions).
   - AI destination tips.
   - Self-test.
   - Delete everything.
8. **First run.** Three steps:
   1. What the app does and does not do. Never paste raw notes from your
      phone.
   2. Type your kids' names, or skip.
   3. Try it on a sample note.

### 5.3 States and errors

- **Every block names the word and gives a next step.** There are no dead
  ends:
  - "Hide 'Rossetti' and copy"
  - "Restart checks"
  - "Copy anyway" is never offered for integrity or leak failures.
- **Engine restarting:** "Restarting checks… your note is safe." The session
  is kept.
- **No unsure items:** "No unsure words found." It never says "nothing to
  review".
- **Accessibility:**
  - VoiceOver labels on chips and rows.
  - Visible focus.
  - Zoom up to 200%.
  - A narrow-window layout.
  - Color is never the only signal.

### 5.4 Documents (v1.1)

- **Input:** drop one file anywhere on the window, or click "Open a
  document…". Word (.docx, .doc), PDF, RTF, OpenDocument text (.odt), and
  text files (.txt, .md), up to 25 MB.
- **Same path as a paste:** the app reads the document's text, and the
  Check view, Copy, and Restore work exactly as for a pasted note. The
  text in Check is exactly the text that was checked.
- **.docx** is read by the app itself: body text in order, and each table
  row as one line of tab-separated cells, the same shape as a table pasted
  from Word, so the form-field parser reads it the same way. Content
  controls, insertions, hyperlinks, text boxes (one representation each),
  and symbol characters are read; tracked deletions, moved-from text, field
  codes, and pictures are not. Automatic list numbers and bullets are
  written as Word shows them ("1." then a tab), as in a paste from Word.
  Plain equations are read as their text. A
  .docx with an embedded document or object (for example an Excel table),
  a structured equation, a symbol the app cannot name, or any visible text
  the reader did not take (a completeness check counts every character) is
  refused, never read in part. **.doc, .rtf, .odt** go through macOS `textutil`; **.pdf** through
  PDFKit, with filled form fields as "Field name: value" lines (the label
  shown, for a dropdown or a radio group) and typed text boxes (a form with
  a multi-select list is refused: its choices cannot be read); text files as UTF-8, UTF-16 with a BOM, or Windows-1252.
- **Not read:** headers, footers, footnotes, and comments (as when copying
  the body). What is not read cannot leak.
- **Refused with a next step:** other file types, files over 25 MB,
  password-protected PDFs, and any PDF with a scanned or picture-only page
  (a page that shows something but has no text), because its words could
  not be checked ("Print it to PDF from the EMR, or copy the text and
  paste it"). No OCR. Words inside pictures on a page that also has text
  are not read (no OCR); EMR exports have no such pictures.
- **Delete everything wins:** a document still being read when Delete
  everything runs is not opened or saved.
- **The file's name never reaches the engine, the vault, or a log:** the
  page sends only the extension and the bytes, because a filename can hold
  a child's name. Nothing is written to disk while reading.
- **Safety of drops:** the page stops every drop; only a file it can read
  is opened. A dropped link or web page never replaces the app's page, and
  the web view has no file-URL access.
- **Finding the window:** opening the app again (Spotlight, Finder) shows
  the window, because the menu bar item can be hidden behind the notch.
  The launcher starts the app in the background and exits (DECISIONS 28).

## 6. Tag rules (v1: tags only)

| Type | Tag | Notes |
|---|---|---|
| Child (this session) | `[CHILD_1]` | Other children in a group: `[CHILD_2]`. Peers and classmates: `[PEER_1]` |
| Family | `[PARENT_1]`, `[SIBLING_1]`, `[FAMILY_1]` | Relationship words ("su mamá") stay |
| Teachers, other providers, case managers | `[TEACHER_1]`, `[PROVIDER_1]` | Hidden by default, even though Safe Harbor does not require it |
| The clinician herself | Kept | A setting can hide her name too |
| Employer (the clinician's company) | `[EMPLOYER]` | |
| Parent's workplace | `[ORG_1]` | |
| School, daycare, CDSA office | `[SCHOOL_1]`, `[DAYCARE_1]` | |
| Pets | `[PET_1]` | |
| Physicians, hospitals, clinics (birth and medical history) | `[PROVIDER_1]`, `[HOSPITAL_1]` | "NICU at WakeMed" becomes "NICU at [HOSPITAL_1]" |
| CDSA office, service coordinator, other agencies (DSS, WIC, Smart Start) | `[AGENCY_1]`, `[PROVIDER_1]` | Agency involvement can identify a family, so hide the name and keep the type ("a social services agency" can be added in review) |
| Interpreters | `[PROVIDER_1]` | |
| Case and plan IDs (NC ITP child ID, IFSP number, claim or authorization numbers) | `[ID_1]` | |
| Dates (date of birth, evaluation date, IFSP dates, service dates) | `[DATE_1]` | Year kept; relative time ("hace dos semanas") kept. Chronological age (2;4), gestational age, and birth weight stay, so the AI can still write the evaluation |
| Session times | `[TIME_1]` | |
| Ages | Kept (4;2) | Over 89 becomes `90+` |
| Places below state level | `[PLACE_1]` | |
| Phone, email, URL, IDs | `[PHONE_1]`, `[EMAIL_1]`, `[MEDICAID_ID_1]` | Numbered per distinct value |

**Fake-names mode** is deferred to v1.1. All three reviews recommended this.
Generic surrogates cannot be restored. Fake names add restore failures, and a
leftover fake name is a false fact in a Medicaid record. If we add it, every
surrogate will be unique and restorable, and the same restore tests will run.

## 7. Evaluation plan

The app must work on any SLP text, whoever wrote it. So it is tested on
documents built from many styles and templates, not tuned to one writer, and
nothing waits on material from the clinician.

1. **Phase 0.**
   - We collect public SLP document structures: the NC ITP IFSP form,
     published evaluation report formats, and SOAP and daily-note templates.
   - We check that AI drafts from tagged text are good enough before building
     further.
2. **Dev data (synthetic).**
   - One model family (Claude) generates evaluations, assessments, IFSPs, and
     daily notes in English, Spanish, and mixed text, in many writing styles
     and EMR layouts.
   - Every identifier is planted, so the labels are exact.
   - The data is split by family and template, and is used only for dev and
     tuning.
3. **Gate set (independent).**
   - A different model family (GPT-6.1 Sol, through Codex) writes the gate
     set from the list in 4.8. This way the test does not share the dev
     generator's habits.
   - Mrinoy spot-checks a sample of it.
   - It also includes a **general-text stratum** (letters to parents, emails,
     plain prose) to check that the app handles any text, not only SLP
     forms.
4. **Measures:** the section 3 gates per stratum, with confidence
   intervals. Rare types (email, Medicaid ID) are sized on purpose.
5. **Fine-tune.** If the gates are missed, we fine-tune Privacy Filter on
   the synthetic train split (English, Spanish, and mixed).
6. **Acceptance.** The clinician runs 30 real notes in both languages. On day two
   she re-checks them, and we count misses. After launch, the app keeps a
   local count of the items she hides by hand.

## 8. Phases

| Phase | What ships | Exit check |
|---|---|---|
| 0 | Employer approval (done). 8 GB RAM (confirmed). Public SLP document structures collected. Tagged-draft quality check | Done in phase 1, week 1 |
| 1 | Day 1: memory and speed harness (8 GB, 5,000-word evaluation). Then the engine (protect, form fields, both rule packs, roster, gazetteer, Privacy Filter, merge), gate and synthetic sets, command-line tool, GLiNER experiment | Section 3 numbers reported per stratum |
| 2 | Menu-bar app: hotkeys, paste box, Check, Copy, Restore, History, Caseload, vault, self-test | Runs offline on her Mac; all checks enforced; restore tests pass |
| 3 | Fine-tune if needed; acceptance run | Section 3 gates met, including ≤ 1 miss in 30 real notes |
| v1.1+ | Fake-names mode; session transcripts (normalization, misarticulated names, spoken digits) | Only if she needs them after v1 use |

Every phase gets a code review with `/cr` (GPT-6.1 Sol, high reasoning) before
it is called done.

## 9. Open questions

None. Resolved on 2026-10-10:

- Employer approval: done (reported by Mrinoy).
- RAM: 8 GB.
- Document types: evaluations, assessments, IFSPs, and daily notes; no
  transcripts.
- Tags only in v1, with fake names later: accepted.
- She writes in an EMR, so the app is paste in, paste out. No file import or
  export. (v1.1 adds document input; section 5.4.)
- No samples or templates from the clinician. The app must work on any SLP text, so
  we build and test it on varied public and generated documents.

## 10. Alternatives

- **An SLP documentation tool with a BAA, adopted by her employer.** Ambiki,
  Twofold, and SOAPNoteAI make this claim (vendor claims, not verified).
- **ChatGPT for Clinicians** offers individual BAAs, but OpenAI's own pages
  limit it to MD/DO, NP, PA, and pharmacists.
- **Claude Enterprise** with the HIPAA-ready setting is an employer-level
  option.

## 11. Change log

- **v2:** Fable 5.1 review of v1.
- **v3:** pediatric SLP use case, Spanish, full architecture and UI/UX.
- **v4:** three-model design review merged (section 12).
- **v5:**
  - Documents are evaluations, assessments, IFSPs, and daily notes; no
    transcripts in v1.
  - Added a form-field parser (EN/ES labels).
  - Added tags for hospitals, physicians, agencies, interpreters, and case
    IDs.
  - Added per-document-type strata, gates, and speed targets.
  - 8 GB RAM and employer approval confirmed.
- **v6:**
  - Tags only, confirmed.
  - Paste in, paste out only.
  - No dependence on the clinician's samples or style: the dev data and an
    independent gate set (from a different model family) cover many styles
    and templates, plus general text.
  - No open questions.
- **v6.1 (2026-10-12):** document input by drop or "Open a document…"
  (section 5.4), at the clinician's request; output stays text. Opening the
  app again shows its window. Decision record: `docs/DECISIONS.md` 27.

## 12. Review synthesis (v3 → v4)

### Adopted (raised by two or three reviewers)

- **Clipboard-first menu-bar app** with hotkeys; History replaces the session
  board. (Opus, Fable; GPT asked for one session workspace.)
- **Tags only in v1; fake names deferred.** (All three.)
- **Restore bound to its session:** a ref line, a fingerprint fallback, and a
  block on a wrong ref. (All three.)
- **Tolerant tag parsing, residue blocking, round-trip tests, and a
  side-by-side diff.** (All three.)
- **Unique numbered tags per distinct value.** (GPT, Opus.)
- **One coordinate system:** union-then-split merge, word snapping, a
  protected-token check, and Unicode property tests. (All three.)
- **Protect masks every detector**, and roster hits inside it become check
  items. Names that are words are decided per mention. (All three.)
- **Measured memory, one ML runtime, GLiNER as an experiment only.** (All
  three.)
- **A hand-written gate set**, a per-stratum all-clean headline, splits by
  family and template, and the clinician's run as a blocking exit. (All three.)
- **Security:** WebKit network and CSP, a stable signing identity, the whole
  session encrypted, clipboard protection, and a minimal caseload. (All
  three.)
- **Cuts:** `.docx`/caption ingest, CSV import, the five-state board, and
  the per-session child picker and target-words field. (All three.)
- **Additions:**
  - tags for peers, pets, times, the employer, and other providers
  - SALT header parsing
  - concrete quasi-identifier prompts in place of a nag
  - errors that always give a next step
  - accessibility details
  - "Delete everything"
  - pinned model hashes

### Adopted from one reviewer

- **Unsure items hidden by default, so Copy is never locked** (Opus). This
  removes the incentive to press "keep" blindly.
- **Context line with gender and age** for Spanish gender agreement (Opus).
- **Number normalization** before the restore number check (Opus).
- **A bilingual name gazetteer with context triggers** in place of
  truecasing (Fable).
- **A restartable engine worker** with stale-result rejection and serialized
  session changes (GPT).
- **Check-list length gates** (Fable).

### Not adopted

- Loading and freeing each model per use (Fable). With one model of about
  1 GB, keeping it warm is faster; it still unloads under memory pressure.
- A required read-through of the remaining text before Copy (GPT). This
  conflicts with fail-closed defaults and adds the friction that makes her
  skip the app. Concrete quasi-identifier prompts cover the risk instead.
