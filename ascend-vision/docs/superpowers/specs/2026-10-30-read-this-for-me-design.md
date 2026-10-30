# Read this for me — design

Date: 2026-10-30. From the "contract review and red-flag detection" idea; the owner opted out of questions, so the
calls marked *(my call)* are Claude's.

## Why

Terms, leases, invoices, school forms and app agreements get signed unread. The owner asks Vision to read one and
hears a plain summary plus the red flags ranked by severity, with full notes saved to a file. It is a first filter,
not a lawyer.

## Research findings that shaped it

- No PDF or OCR library is installed, but `google.genai` is (the screen auditor uses it), and Gemini reads images
  and PDFs natively. No new dependency.
- The camera frame is in the main loop (`packet.image`, BGR); the screen comes from `screen_auditor.capture_desktop()`.
- There is no file drop into Fairy chat, so a PDF comes from the newest file in Downloads. *(my call)*

## What it does

- **Asking** (whole message):
  - screen: "read this for me", "read my screen", "what does this say", "check this for red flags";
  - camera: "read what I'm holding", "read this paper", "check this paper";
  - PDF: "read my latest download", "check my last PDF" → the newest `.pdf` in Downloads changed in the last 7 days,
    at most 20 MB.
- Vision answers "Reading it now." and reads on a background thread; one reading at a time ("Still reading the last
  one."). No camera frame: "I can't see the camera right now."; no PDF: "I don't see a PDF in Downloads from the
  last week."
- **Model:** Gemini (`llm.gemini_model`, key from `llm.gemini_api_key_env`), JSON reply: kind, summary, up to 8 red
  flags (high / medium / low), up to 5 dates or deadlines. The prompt treats the document as untrusted data and
  looks for auto-renewal, fees and penalties, liability, termination, cancellation notice, data sharing, deadlines.
  Images are sent as JPEG at most 1600 px. No key: "Reading needs a Gemini key."
- **Answer** (spoken through the usual reply voice): the summary's first sentence, then the two most severe flags
  ("Two things to watch: high, it renews every year unless you cancel 60 days before; medium, a 5 percent late
  fee."), "N more in the notes." if there are more, or "Nothing stood out as a red flag." For contracts, leases,
  terms and agreements it ends "I'm not a lawyer, so for anything big, ask one."
- **Notes:** `data/readings/<YYYY-MM-DD-HHMM>-<kind>.md` with source, summary, every flag, dates and "Not legal
  advice." The image or PDF itself is not kept. *(my call)*
- **Privacy:** the screenshot, photo or PDF goes to Google Gemini, only when the owner asks. README says so.
- **Config:** `read_for_me.enabled` (true), `read_for_me.downloads` ('' = the user's Downloads folder),
  `read_for_me.folder` ('' = `data/readings`). Not added to `config.yaml`.

## Non-goals

- Checking the licenses of packages agents add (it belongs in the Hub's review step, not a spoken reader).
- Other file types than PDF, or picking a file by name.
- Legal advice, or comparing against a lawyer's checklist.
