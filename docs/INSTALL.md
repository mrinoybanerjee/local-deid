# Install and use Local DeID

`install.sh` sets up everything on the Mac. It keeps the code in
`~/Library/LocalDeID` and builds `~/Applications/Local DeID.app`, a small
launcher that runs that folder's Python environment. The launcher holds no
code and no data. The app makes no network connections; only the installer
uses the network.

## Install (once)

1. On the clinician's Mac, open Chrome with a few tabs and ChatGPT, so the
   memory check is realistic.
2. Open Terminal, paste this line, and press Return:

   ```
   curl -fsSL https://raw.githubusercontent.com/mrinoybanerjee/local-deid/main/install.sh | bash
   ```

3. Wait about 15–20 minutes (an estimate: most of it is the 1.5 GB model
   download and the speed and memory check, which opens the window and uses
   the clipboard). Do not use the Mac meanwhile.
4. Read the last lines of the check: **Result: PASS** or **Result: FAIL**.
   Send them to Mrinoy if it says FAIL.

"DeID" appears in the menu bar, and the app starts at login. macOS may show
a "Background Items Added" notice; that is expected. If macOS asks whether
Python may use the Keychain (for example after an update), click
**Always Allow**. The key that protects saved sessions stays in the login
Keychain of this Mac only.

The hotkeys need no Accessibility permission.

## Update

Quit Local DeID from the menu bar, then paste the same line again. It
downloads the latest code, installs the locked packages, checks the model
files, runs the self-test, rebuilds the app, and opens it. The caseload,
sessions, and settings stay. To repeat the speed and memory check, end the
line with `| bash -s -- --measure` instead of `| bash`.

## Daily use

1. Copy a note in the EMR and press **⌃⌥D** (Control-Option-D).
   - If nothing needs a check, the de-identified text is now on the clipboard.
     The menu bar shows "Hid N items · ref XXXX".
   - Otherwise the Check window opens. Red words are hidden. Amber words with
     "?" are unsure and are hidden too. Select words and press **H** to hide
     them. Press **U** on a row to show a word again. Then click **Copy**.
2. Paste into ChatGPT or Claude (a Temporary Chat), and ask for the draft.
   Keep the `ref:` line and every `[TAG]` as written.
3. Copy the AI's draft and press **⌃⌥R** (Control-Option-R). The Restore window
   shows the draft with the real names back, in green. Click **Copy restored
   draft** and paste it into the EMR.
4. The clipboard clears itself after 2 minutes, and when the app quits.

Put the children's names in **Caseload**, one child per line, for example
`Mateo Ruiz López; nickname Teo; mom Lucía; sister Sol; school Little Sprouts`.
For a two-word first name, add `first`: `Juan Pablo Ruiz; first Juan Pablo`.
Do not enter addresses, phone numbers, IDs, or dates of birth.

## Where the data is

- Sessions, caseload, and settings: `~/Library/Application Support/LocalDeID/vault/`,
  encrypted (AES-GCM), excluded from Time Machine. Sessions are deleted after 30
  days, except a note that still waits for its draft.
- The key: login Keychain, item `com.mrinoy.localdeid`, this Mac only.
- The log of the last run: `~/Library/Logs/LocalDeID/last-run.log`. It holds
  error codes only, never note text.
- **Settings > Delete everything** (type DELETE) removes the sessions, the
  caseload, the settings, and the key.

## If something is wrong

- **Settings > Run self-test** checks the model files, the offline guard, and a
  sample note.
- "Your saved settings cannot be read": the Keychain key is gone or changed.
  Use Settings > Delete everything, then enter the caseload again.
- The app does not start: open Terminal and run
  `~/Library/LocalDeID/.venv/bin/python -m localdeid.app --selftest-exit`.
