# User / business flow

The whole app's user journey, from the login gate through to the
reconciliation loop it eventually feeds into. This is the outer loop;
`docs/ROADMAP.md`'s V2 section has a narrower diagram focused just on the
Reconciliation page's own internal logic.

```mermaid
flowchart TD
    Login["Log in<br/>(shared password)"] --> Nav{"Where next?"}

    Nav -->|"Check performance"| Dash["Dashboard<br/>blended KPIs, charts,<br/>Since Last Statement panel"]

    Nav -->|"A trade happened"| HaveSlip{"Have a slip<br/>screenshot?"}
    HaveSlip -->|Yes| Upload["Upload Slip tab<br/>(Claude Vision pre-fills the form)"]
    HaveSlip -->|No| ManualTrade["Manual Entry tab"]
    Upload --> ConfirmTrade["Review / edit,<br/>then Save"]
    ManualTrade --> ConfirmTrade
    ConfirmTrade --> Saved1[("Trade saved")]

    Nav -->|"A dividend/interest posted"| RecordDiv["Record Dividend page<br/>Gross + Withholding grid"]
    RecordDiv --> Saved2[("Dividend saved")]

    Saved1 --> Dash
    Saved2 --> Dash

    Dash -->|"New official statement<br/>eventually arrives"| Recon["Reconciliation page<br/>(Tools section)"]
    Recon -->|Matched| Confirmed["Marked reconciled --<br/>never re-checked"]
    Recon -->|"No match"| Review["Needs review --<br/>delete & re-enter"]
    Recon -->|"xlsx has it, app doesn't"| Gap["Official activity<br/>not yet logged --<br/>go log it"]
    Review -.-> ManualTrade
    Review -.-> RecordDiv
    Gap -.-> ManualTrade
    Gap -.-> RecordDiv
```

## Notes

- **Login** is a single shared password, not per-user accounts -- this is a
  single-portfolio, single-user app by design (see `docs/ROADMAP.md`'s V1
  context).
- **Dashboard** is the default landing page and the only one most sessions
  actually need -- Record Trade/Record Dividend are only visited when new
  activity happens, and Reconciliation only after a new official statement
  is processed (infrequent, monthly at most).
- **The two loop-backs out of Reconciliation** (Needs review / Gap ->
  Record Trade / Record Dividend) are the same correction path either way:
  this app has no in-place edit anywhere, so both "wrong data" and "missing
  data" resolve through the same two entry pages.
