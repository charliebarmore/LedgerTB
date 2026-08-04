# Firm mode — book files on a shared drive

A **book** is one encrypted ProBooks database file. By default your book
lives in the app's data folder — but a firm can keep book files anywhere,
including a shared network drive, the way desktop trial-balance software
has always worked: the app installs on each person's machine, the data
travels by path, and whoever needs a book opens it.

## Using it

- The **lock screen** has a *Book file* section: recent books, or open /
  create any path (e.g. `\\server\Books\SmithCo.probooks`). Each book has
  a passphrase — the passphrase is the login. **Using one office
  passphrase for all your firm's books is fine**; different passphrases
  per book are an option (say, to wall off one sensitive client), not a
  requirement. And **"Remember on this Mac/PC"** stores a book's unlock
  key in your computer's credential vault so the app opens it without
  asking — per machine, undoable on Data Safety.
- **Data Safety** shows which book is open and offers *Switch book…*
- One book can hold many clients, or you can create one book per client
  for ProSystem-style granularity — two people can then work different
  clients at the same time.

## The in-use lock

SQLite's own file locking is unreliable on network shares, so ProBooks
coordinates the honest way: opening a book writes a `.lock` file beside
it naming who has it open. If someone else holds the lock you choose:

- **Open read-only** — look at everything, change nothing (enforced at
  the database, not by the UI), or
- **Take over** — for stale locks after a crash. A stale lock from your
  own machine is reclaimed automatically; someone else's never is.

One writer per book at a time. Same-book simultaneous *editing* is
deliberately out of scope — that's what keeps a shared book safe.

## Notes

- Backups (Data Safety) always back up the currently open book, into the
  local backups folder of the machine that ran them.
- Assistant access (MCP) reads whichever book was most recently opened,
  read-only as always. Books with different passphrases need assistant
  access re-enabled after switching.
- The shared drive sees only SQLCipher-encrypted bytes; without a book's
  passphrase the file is unreadable.
