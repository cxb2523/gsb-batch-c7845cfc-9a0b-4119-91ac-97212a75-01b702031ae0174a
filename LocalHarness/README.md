# Offline harness for `Main.xaml`

This folder exists because the real workflow cannot be executed offline: it
needs UiPath Studio, the desktop Outlook application and at least one readable
unread message in the `UiPath Test` folder. The harness reproduces the parts of
`Main.xaml` that this task changed, drives them with hand-written mail metadata
and asserts the results without touching Outlook.

```
LocalHarness\
  EmailArchiveFlow.vb      mirror of the per-email archiving logic
  ScenarioRunner.vb        fixture reader, assertions, report artefacts
  fixtures\scenarios.txt   hand-written mail metadata (sender/subject/Date/attachments)
  validate-xaml.ps1        structural checks on Main.xaml itself
  run-harness.cmd          one command: compile, run scenarios, validate
```

## Running it

Requires only Windows: the .NET Framework `vbc.exe` and Windows PowerShell, both
of which ship with the OS.

```
LocalHarness\run-harness.cmd
```

Exit code `0` means the scenarios and the structural checks passed. Generated
report bodies are written to `LocalHarness\build\work\_reports\<scenario>.txt`
so the actual mail text can be reviewed.

Individual commands:

```
LocalHarness\build\ScenarioRunner.exe EmailTemplates LocalHarness\fixtures\scenarios.txt LocalHarness\build\work
powershell -NoProfile -ExecutionPolicy Bypass -File LocalHarness\validate-xaml.ps1
```

`LocalHarness\build\` is generated output and is ignored by git.

## What the harness checks

Each row is a scenario in `fixtures\scenarios.txt`.

| Scenario | Emails | What it proves |
| --- | --- | --- |
| `all-success` | 10 clean | ten distinct folders on disk; success template and its wording kept; the report's `Saved to:` path equals the folder actually written |
| `one-attachment-failure` | 4th fails at `SaveMailAttachmentsX` | the other nine still archive; the failure template is used; the body names the failed sender, subject and reason plus the partial folder; success and failure sections both appear |
| `same-sender-same-second` | 3 from one sender, identical `Date` | folders are `…10-20-30`, `…10-20-30_2`, `…10-20-30_3`; all three messages survive; each reported path is its real folder |
| `empty-subject` | 1st has an empty subject | the failure record shows the `(No Subject)` placeholder; processing continues |
| `subject-read-error` | 6th throws on reading `.Subject` | placeholder subject, reason recorded, later emails still processed |
| `bad-date` | 3rd has an unparsable `Date` header | recorded failure, `Archive folder: none created`, loop continues |
| `sender-read-error` | 2nd throws on reading `.From` | non-fatal: the email is still archived under `Unknown`, as in the original design |
| `all-fail` | all 10 fail | the run still produces a report listing all ten; `Emails archived successfully: 0` |
| `save-mail-failure` | 5th fails inside `SaveMailX` (subject contains a colon) | the folder is created first, so the failure record shows it as a partial folder holding only an empty `.msg` stub; the record keeps the readable subject `Locked: message file` rather than the sanitised file name; the remaining emails are still processed |
| `mixed` | duplicate pair + bad date + empty subject | successes and failures coexist; indices are the ones the user sees |
| `aborted` | fatal error outside the per-email scope | `EmailAbortedTemplate.txt` is used, the summary still names the archived emails and the interruption reason — the run never degrades to an error-screenshot-only mail |

Assertions common to every scenario:

* successful count and failure count match the fixture, and add up to the number of emails read;
* every failure names a sender, a subject (or the placeholder) and a reason, and the subject shown is the readable one, not the sanitised file name;
* no email appears in both the success and failure lists;
* every archived email owns a distinct directory;
* every reported path exists on disk, and no failed email's folder is printed as `Saved to:`;
* each archived email's message file sits inside its own reported folder;
* with zero failures the success template and subject are unchanged; with one or more failures the failure template is used and the body states how many were not archived.

`validate-xaml.ps1` checks `Main.xaml` itself:

* XML well-formedness, `If`/`TryCatch`/`Switch`/`While`/`Assign` child ordering;
* no duplicate `IdRef` values;
* every bracketed VB expression references a declared variable or argument;
* every `String.Format` call's argument count matches the placeholders in its template;
* the locked configuration is intact: Outlook account `Meeno2b@outlook.com`, folder `UiPath Test`, `NumberOfEmailsLimit="10"`, `UnreadOnly="True"`;
* all three template files exist.

## What the local mirror covers

`EmailArchiveFlow.vb` reproduces, step for step, the per-email body of
`Main.xaml`:

* the sender read wrapped in its own `TryCatch` and falling back to `Unknown`;
* the same sender → label `Switch` table (`Coursera`, `BIG W`, `Uber`, `Taronga Zoo`, `WinConnect Electricity`, `Me`, `Unknown`);
* subject read, empty-subject rejection, the same character replacements, and the `Path.GetInvalidFileNameChars()` clean-up. Two values are kept apart: the readable subject (shown in the failure record, so a failure after sanitising still names the subject the user recognises) and the sanitised file name (used for the `.msg` and for the success entry);
* `Date` header parsing with `CDate` and the `yyyy-MM-dd hh-mm-ss` stamp, including its local-time conversion;
* unique-folder allocation: the base name, then `_2`, `_3`, … while `Directory.Exists`;
* `Directory.CreateDirectory(...).FullName` as the single path used for both the save calls and the report line;
* the per-email `TryCatch` that records sender, subject, reason and partial folder, then continues;
* success list, failure list, template selection and the exact attribute lines that make up the report body, joined in memory (the workflow no longer round-trips the report through `EmailReport.txt`, so a locked or missing temp file cannot stop the summary being sent);
* the outer `TryCatch` that marks the run interrupted and the `Finally` block that still sends the summary.

## What it does not cover (Outlook-specific)

These parts only exist in the real run and were reviewed statically, not executed:

* `OutlookApplicationCard` and its account selection / `AccountMismatchBehavior`;
* `ForEachEmailX` — retrieving `Outlook.Folder("UiPath Test")`, the unread filter, `NumberOfEmailsLimit="10"` and `IncludeSubfolders="False"`;
* the real `System.Net.Mail.MailMessage` object and whether Outlook returns a populated `Headers("Date")` for every message;
* `SaveMailX` (writing the `.msg`) and `SaveMailAttachmentsX` — the harness writes plain text stand-ins, so file locking, name collisions between attachments, and `OverwriteExisting` behaviour are not exercised;
* `SendMailX` delivery of the final report;
* the error-screenshot path (`TakeScreenshot` / `SaveImage`) and `MessageBox` in the outer catch;
* the outer-failure branch is modelled by injecting an exception at the same point; the real trigger (for example Outlook losing its connection mid-loop) was not reproduced.

### One limitation of the isolation itself

The per-email `TryCatch` sits **inside** the `ForEachEmailX` body, so it isolates
everything that happens while handling one message: reading `Subject`, reading
`From`, parsing the `Date` header, saving the message and saving attachments.
It cannot isolate a failure raised by `ForEachEmailX` itself while it is
enumerating the folder (for example Outlook refusing to open `UiPath Test`).
That case still reaches the outer `TryCatch`, which now records the reason and
lets the `Finally` block send the interrupted-run summary instead of only the
error-screenshot mail.

## Fixture format

```
[scenario-key]
description=free text
success=<expected archives>
failure=<expected failures>
template=success|failure
mail=sender|subject|date|attachments|failAt
```

`<empty>` means a missing value: a missing sender address or an empty subject.
`attachments` is `yes`/`no`. `failAt` is `-` for normal behaviour, or one of
`sender-read`, `subject-read`, `date-parse`, `save-mail`,
`save-attachments` to inject a failure at exactly that step.

Note that `Date` headers are parsed with `CDate`, which converts the header's
numeric offset to the machine's local time. The timestamps in the folder names
therefore depend on the machine's time zone; the same-second scenario asserts
the `_2` / `_3` disambiguation shape rather than a fixed clock reading.
