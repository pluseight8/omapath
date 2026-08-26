# Omapath security and data-safety notes

## Enforced now

- Filesystem operations use Python/GIO APIs with argument lists; active code
  does not use `shell=True` or `os.system` for user filenames.
- Copy and move operations validate source/destination relationships and use
  temporary staging/backup paths before replacement.
- Archive extraction is bounded and rejects unsafe paths through
  `omapath/archive.py`.
- Rename and create operations validate a single directory-entry name, so a
  dialog cannot turn a filename into a path traversal.
- Trash is preferred for normal Delete; permanent deletion is explicit in the
  Trash context menu.
- Settings are validated, written to a temporary file, fsynced and atomically
  replaced.
- Worker exceptions become `OperationResult.errors`; they do not escape into
  GTK callbacks.

## Still required before a release claim

- interactive conflict UI for Replace/Rename/Skip/Cancel and batch decisions;
- a complete audit of remote-provider capability checks;
- permission/read-only and race-condition torture tests on real filesystems;
- plugin/service trust boundaries and a documented install policy;
- crash bundle redaction and packaging review.

These items remain PARTIAL until implemented and tested; no release claim is
based on the current headless suite alone.
