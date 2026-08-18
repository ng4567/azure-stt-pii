"""Generate the two architecture diagram pages.

Both pages share one template, so the Fabric analytics tail, the theme, and the nav
are written once and cannot drift apart. Only the row-1 call pipeline and the prose
differ per architecture.

Run from the repository root after editing anything below:

    python3 frontend/public/architecture/build_diagrams.py

The generated HTML is checked in; the backend serves it from
`/api/architecture-diagrams/<id>` and the pages also open directly as static files.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent

STYLE = """
  :root{
    --bg:#0b1220; --surface:#0e1524; --surface-2:#111a2b; --surface-3:#152034;
    --line:#1e2a41; --line-strong:#2c3d5c;
    --text:#e9eefb; --muted:#a2b0cb; --faint:#7c8aa5;
    --accent:#3ea0ff; --cyan:#50e6ff; --good:#45d69a;
    --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg);color:var(--text);padding:1.25rem 1rem;min-height:100vh;line-height:1.55;
    -webkit-font-smoothing:antialiased;
  }
  .container{max-width:1200px;margin:0 auto;background:var(--surface);
    border:1px solid var(--line);border-radius:14px;padding:1.5rem}
  .nav{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:1.1rem}
  .nav a{display:flex;align-items:center;gap:.45rem;padding:.35rem .7rem;border:1px solid var(--line);
    border-radius:999px;font-size:.78rem;font-weight:600;color:var(--muted);text-decoration:none}
  .nav a:hover{border-color:var(--accent);color:var(--text)}
  .nav a[aria-current="page"]{background:rgba(62,160,255,.14);border-color:var(--accent);color:var(--text)}
  .nav i{display:grid;place-items:center;width:17px;height:17px;border-radius:5px;
    background:var(--accent);color:#04101f;font-style:normal;font-size:.68rem;font-weight:700}
  .nav a:not([aria-current="page"]) i{background:var(--line-strong);color:var(--muted)}
  header{border-bottom:1px solid var(--line);padding-bottom:.9rem;margin-bottom:1.1rem}
  h1{font-size:1.35rem;letter-spacing:-.02em;font-weight:700}
  h1 span{color:var(--accent)}
  .subtitle{color:var(--muted);font-size:.92rem;margin-top:.4rem;max-width:84ch}
  .badges{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.8rem}
  .badge{background:var(--surface-3);color:var(--muted);border:1px solid var(--line);
    border-radius:999px;padding:.2rem .65rem;font-size:.73rem;font-weight:600}
  .badge.key{background:rgba(62,160,255,.15);color:#9ed0ff;border-color:transparent}
  .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.55rem;
    margin-bottom:1.1rem}
  .metric{border:1px solid var(--line);border-radius:10px;padding:.6rem .75rem;background:var(--surface-2)}
  .metric b{display:block;font-size:1.2rem;font-weight:700;color:var(--text);
    font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .metric span{display:block;color:var(--faint);font-size:.72rem;margin-top:.1rem}
  .diagram{background:var(--surface-2);border:1px solid var(--line);border-radius:10px;
    padding:.85rem;overflow-x:auto}
  svg{display:block;width:100%;height:auto;min-width:940px}
  svg .b{fill:#fff;font-size:12.5px;font-weight:700}
  svg .s{fill:#fff;font-size:10px;opacity:.9}
  svg .tb{fill:var(--text);font-size:12px;font-weight:700}
  svg .ts{fill:var(--muted);font-size:9.5px}
  svg .m{fill:var(--faint);font-size:10px}
  svg .t{fill:var(--muted);font-size:10px;font-style:italic}
  svg .band{fill:var(--faint);font-size:10.5px;font-weight:700;letter-spacing:.08em}
  .note{border-left:3px solid var(--accent);background:rgba(62,160,255,.08);
    border-radius:0 8px 8px 0;padding:.8rem .95rem;margin-top:1rem;font-size:.87rem;color:var(--muted)}
  .note b{color:var(--text)}
  code{background:var(--surface-3);border-radius:4px;padding:.05rem .32rem;
    font-family:var(--mono);font-size:.86em;color:#9ed0ff}
  footer{margin-top:1.1rem;padding-top:.8rem;border-top:1px solid var(--line);
    color:var(--faint);font-size:.76rem}
  /* Embedded in the dashboard: drop the page chrome and the duplicate nav.
     The background stays opaque and matches the host panel - a transparent body
     lets the browser paint the iframe canvas white underneath it. */
  body.framed{background:#0e1524;padding:1.1rem 1.25rem}
  body.framed .container{max-width:none;border:0;border-radius:0;padding:0;background:transparent}
  body.framed .nav{display:none}
  @media (max-width:768px){
    body{padding:.75rem .5rem}.container{padding:1rem;border-radius:12px}h1{font-size:1.15rem}
  }
"""

NAV = """
<nav class="nav" aria-label="Architectures">
  <a data-arch="architecture-1-azure-language"{a1}><i>1</i>Today &mdash; Azure Speech + Azure AI Language</a>
  <a data-arch="architecture-2-mai-realtime-deepseek"{a2}><i>2</i>Modernized &mdash; MAI-Transcribe + Foundry</a>
</nav>
"""

# The shared Fabric tail. Six stages, drawn as surface cards with a common accent
# edge so the reader can see at a glance that this half does not change.
TAIL_STAGES = [
    ("PII-safe record", ["summary + normalized", "attributes only"]),
    ("Oracle Database 19c", ["call_analytics table,", "the system of record"]),
    ("Fabric Mirroring", ["LogMiner redo via the", "on-premises gateway"]),
    ("OneLake", ["Delta tables, readable", "by every Fabric engine"]),
    ("Semantic model", ["Direct Lake, 15 governed", "KPI measures"]),
    ("Fabric Data Agent", ["natural language,", "and M365 Copilot"]),
]


def fabric_tail(top: int) -> str:
    """Row 2: identical on both pages."""
    width, gap, height = 166, 30, 92
    parts = [
        f'<text class="band" x="4" y="{top - 26}">SHARED ANALYTICS ESTATE &mdash; IDENTICAL IN BOTH ARCHITECTURES</text>',
        f'<rect x="0" y="{top - 16}" width="1160" height="{height + 30}" rx="12" '
        f'fill="none" stroke="#2c3d5c" stroke-dasharray="5 4"/>',
    ]
    for index, (title, lines) in enumerate(TAIL_STAGES):
        x = 4 + index * (width + gap)
        parts.append(
            f'<rect x="{x}" y="{top}" width="{width}" height="{height}" rx="10" '
            f'fill="#152034" stroke="#2c3d5c"/>'
        )
        parts.append(f'<rect x="{x}" y="{top}" width="4" height="{height}" rx="2" fill="#3ea0ff"/>')
        parts.append(f'<text class="tb" x="{x + 15}" y="{top + 26}">{title}</text>')
        for line_index, line in enumerate(lines):
            parts.append(
                f'<text class="ts" x="{x + 15}" y="{top + 46 + line_index * 15}">{line}</text>'
            )
        if index < len(TAIL_STAGES) - 1:
            start, end = x + width, x + width + gap - 3
            parts.append(
                f'<path d="M{start},{top + height / 2:.0f} L{end},{top + height / 2:.0f}" '
                f'stroke="#5a6d8f" stroke-width="2" marker-end="url(#arrow)"/>'
            )
    return "\n  ".join(parts)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} | Call processing migration</title>
<style>{style}</style>
</head>
<body>
<div class="container">
{nav}
<header>
  <h1><span>{eyebrow}</span> &mdash; {heading}</h1>
  <p class="subtitle">{subtitle}</p>
  <div class="badges">{badges}</div>
</header>

<div class="metrics">{metrics}</div>

<div class="diagram">
<svg viewBox="0 0 1160 {height}" role="img" aria-label="{aria}">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
            orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#5a6d8f"/>
    </marker>
  </defs>

  {pipeline}

  {tail}
</svg>
</div>

<div class="note">{note}</div>

<footer>
  Call-processing figures are read from the checked-in stereo benchmark result
  (504.168s call, 989-word reference), at Azure list price before any negotiated discount.
  The analytics half is a deployment blueprint, not a measured benchmark.
</footer>

</div>
<script>
  // The pages are served both as static files and from /api/architecture-diagrams/<id>.
  if (window.top !== window.self) document.body.classList.add("framed");
  var suffix = location.pathname.endsWith(".html") ? ".html" : "";
  for (var link of document.querySelectorAll("[data-arch]")) {{
    link.setAttribute("href", link.dataset.arch + suffix);
  }}
</script>
</body>
</html>
"""


def metrics(items):
    return "".join(f'<div class="metric"><b>{value}</b><span>{label}</span></div>' for value, label in items)


def badges(key, rest):
    out = f'<span class="badge key">{key}</span>'
    return out + "".join(f'<span class="badge">{item}</span>' for item in rest)


TAIL_TOP = 296
HEIGHT = 430

# --------------------------------------------------------------------- arch 1
ARCH1_PIPELINE = """
  <rect x="4" y="24" width="205" height="104" rx="10" fill="#2f6fbf"/>
  <text class="b" x="20" y="52">Call audio</text>
  <text class="s" x="20" y="72">stereo, ch0 REP / ch1 CUSTOMER</text>
  <text class="s" x="20" y="90">normalized to 16-bit PCM</text>

  <rect x="253" y="24" width="205" height="104" rx="10" fill="#c25428"/>
  <text class="b" x="269" y="52">Azure Speech SDK</text>
  <text class="s" x="269" y="72">one real-time session per</text>
  <text class="s" x="269" y="90">channel, turns finalized</text>
  <text class="s" x="269" y="108">during the call</text>

  <rect x="510" y="8" width="205" height="66" rx="10" fill="#6d5fd0"/>
  <text class="b" x="526" y="34">Conversation PII</text>
  <text class="s" x="526" y="54">detects typed entity spans</text>

  <rect x="510" y="86" width="205" height="66" rx="10" fill="#5a4dbb"/>
  <text class="b" x="526" y="112">Summarization</text>
  <text class="s" x="526" y="132">issue + resolution summary</text>

  <rect x="770" y="24" width="220" height="104" rx="10" fill="#1f8f66"/>
  <text class="b" x="786" y="52">Redacted outputs</text>
  <text class="s" x="786" y="72">redacted transcript,</text>
  <text class="s" x="786" y="90">38 typed entities,</text>
  <text class="s" x="786" y="108">sanitized summary</text>

  <path d="M209,76 L250,76" stroke="#5a6d8f" stroke-width="2" marker-end="url(#arrow)"/>
  <path d="M458,76 L484,76 L484,41 L507,41" stroke="#5a6d8f" stroke-width="2" fill="none" marker-end="url(#arrow)"/>
  <path d="M458,76 L484,76 L484,119 L507,119" stroke="#5a6d8f" stroke-width="2" fill="none" marker-end="url(#arrow)"/>
  <path d="M715,41 L742,41 L742,76 L767,76" stroke="#5a6d8f" stroke-width="2" fill="none" marker-end="url(#arrow)"/>
  <path d="M715,119 L742,119 L742,76 L767,76" stroke="#5a6d8f" stroke-width="2" fill="none" marker-end="url(#arrow)"/>

  <rect x="4" y="162" width="820" height="14" rx="7" fill="#22406b"/>
  <text class="t" x="18" y="173">transcription runs with the call</text>
  <path d="M4,190 L1150,190" stroke="#2c3d5c" stroke-width="1.5"/>
  <path d="M4,184 L4,196 M824,184 L824,196 M950,184 L950,196 M1140,184 L1140,196"
        stroke="#5a6d8f" stroke-width="1.5"/>
  <text class="m" x="4" y="212">call start</text>
  <text class="m" x="824" y="212" text-anchor="middle">504.2s call ends</text>
  <text class="m" x="960" y="212" text-anchor="middle">504.7s transcript</text>
  <text class="m" x="1150" y="212" text-anchor="end">509.3s outputs</text>

  <path d="M890,232 L890,288" stroke="#5a6d8f" stroke-width="2" stroke-dasharray="4 3"
        marker-end="url(#arrow)"/>
  <text class="t" x="902" y="262">only the PII-safe record crosses over</text>
"""

ARCH2_PIPELINE = """
  <rect x="4" y="24" width="196" height="104" rx="10" fill="#2f6fbf"/>
  <text class="b" x="20" y="52">Call audio</text>
  <text class="s" x="20" y="72">stereo, ch0 REP / ch1 CUSTOMER</text>
  <text class="s" x="20" y="90">normalized to 16-bit PCM</text>

  <rect x="240" y="24" width="196" height="104" rx="10" fill="#152034" stroke="#3d5480"/>
  <text class="tb" x="256" y="52">Local VAD</text>
  <text class="ts" x="256" y="72">runs in the backend, not billed</text>
  <text class="ts" x="256" y="88">commits at natural pauses,</text>
  <text class="ts" x="256" y="104">not on a fixed clock</text>

  <rect x="476" y="24" width="196" height="104" rx="10" fill="#c25428"/>
  <text class="b" x="492" y="52">MAI-Transcribe-1.5</text>
  <text class="s" x="492" y="72">Voice Live WebSocket,</text>
  <text class="s" x="492" y="90">one session per channel,</text>
  <text class="s" x="492" y="108">transcribed as spoken</text>

  <rect x="712" y="24" width="196" height="104" rx="10" fill="#5a4dbb"/>
  <text class="b" x="728" y="52">DeepSeek &middot; Foundry</text>
  <text class="s" x="728" y="72">one call, strict JSON</text>
  <text class="s" x="728" y="90">schema, temperature 0</text>

  <rect x="948" y="24" width="205" height="104" rx="10" fill="#1f8f66"/>
  <text class="b" x="964" y="52">PII-safe summary</text>
  <text class="s" x="964" y="72">returns {summary} only</text>
  <text class="s" x="964" y="90">redacted: null</text>
  <text class="s" x="964" y="108">entities: []</text>

  <path d="M200,76 L237,76" stroke="#5a6d8f" stroke-width="2" marker-end="url(#arrow)"/>
  <path d="M436,76 L473,76" stroke="#5a6d8f" stroke-width="2" marker-end="url(#arrow)"/>
  <path d="M672,76 L709,76" stroke="#5a6d8f" stroke-width="2" marker-end="url(#arrow)"/>
  <path d="M908,76 L945,76" stroke="#5a6d8f" stroke-width="2" marker-end="url(#arrow)"/>

  <rect x="4" y="162" width="820" height="14" rx="7" fill="#22406b"/>
  <text class="t" x="18" y="173">transcription runs with the call</text>
  <path d="M4,190 L1150,190" stroke="#2c3d5c" stroke-width="1.5"/>
  <path d="M4,184 L4,196 M824,184 L824,196 M940,184 L940,196 M1140,184 L1140,196"
        stroke="#5a6d8f" stroke-width="1.5"/>
  <text class="m" x="4" y="212">call start</text>
  <text class="m" x="824" y="212" text-anchor="middle">504.2s call ends</text>
  <text class="m" x="952" y="212" text-anchor="middle">504.5s transcript</text>
  <text class="m" x="1150" y="212" text-anchor="end">508.2s summary</text>

  <path d="M890,232 L890,288" stroke="#5a6d8f" stroke-width="2" stroke-dasharray="4 3"
        marker-end="url(#arrow)"/>
  <text class="t" x="902" y="262">only the PII-safe record crosses over</text>
"""

pages = {
    "architecture-1-azure-language.html": {
        "title": "Today - Azure Speech + Azure AI Language",
        "eyebrow": "Current state",
        "heading": "Azure Speech + Azure AI Language",
        "nav": NAV.format(a1=' aria-current="page"', a2=""),
        "subtitle": (
            "The stack most contact centres run today. Azure Speech transcribes the call as it "
            "happens, then Conversation PII and conversation summarization run concurrently on "
            "the transcript. It is the only path that returns a redacted transcript and a typed "
            "entity list &mdash; and its summarization feature is scheduled to retire on "
            "March&nbsp;31,&nbsp;2029."
        ),
        "badges": badges(
            "Full transcript redaction",
            [
                "Real-time streaming STT",
                "Azure AI Language &mdash; no LLM, no prompt",
                "Concurrent endpoint branches",
                "Entra ID auth",
            ],
        ),
        "metrics": metrics([
            ("5.36%", "word error rate"),
            ("85", "speaker turns"),
            ("504.7s", "transcript ready (504.2s call)"),
            ("4.6s", "work after the transcript"),
            ("$0.298093", "list cost per call"),
        ]),
        "pipeline": ARCH1_PIPELINE,
        "aria": (
            "Architecture 1 flow: call audio, Azure Speech real-time transcription, concurrent "
            "Azure AI Language calls, redacted outputs, then the shared Fabric analytics estate."
        ),
        "note": (
            "<b>The managed baseline.</b> Redaction is decided by a dedicated Azure AI Language "
            "model rather than a general-purpose model under a prompt, and both Language calls "
            "are issued in parallel, so their endpoint times overlap instead of accumulating. "
            "That governance comes at a price: it is the more expensive path per call and the "
            "less accurate transcript. Because it emits a full <code>redacted</code> block and a "
            "typed entity list, it is the only path that can be scored for PII precision and "
            "recall."
        ),
    },
    "architecture-2-mai-realtime-deepseek.html": {
        "title": "Modernized - MAI-Transcribe + Foundry",
        "eyebrow": "Modernized",
        "heading": "MAI-Transcribe-1.5 + Foundry + Fabric",
        "nav": NAV.format(a1="", a2=' aria-current="page"'),
        "subtitle": (
            "The same call, transcribed live by MAI-Transcribe-1.5 over Voice Live and reduced to "
            "one PII-safe summary by a single strict-schema model call in Microsoft Foundry. "
            "Roughly a third fewer transcription errors at about a third of the cost, with no "
            "dependency on a retiring service."
        ),
        "badges": badges(
            "PII-safe summary only",
            [
                "Real-time streaming STT",
                "One model call, strict JSON schema",
                "VAD-aligned utterance commits",
                "Entra ID auth",
            ],
        ),
        "metrics": metrics([
            ("3.34%", "word error rate"),
            ("112", "speaker turns"),
            ("504.5s", "transcript ready (504.2s call)"),
            ("3.7s", "work after the transcript"),
            ("$0.101356", "list cost per call"),
        ]),
        "pipeline": ARCH2_PIPELINE,
        "aria": (
            "Architecture 2 flow: call audio, local voice activity detection, MAI-Transcribe-1.5 "
            "over Voice Live, one DeepSeek call in Foundry, a PII-safe summary, then the shared "
            "Fabric analytics estate."
        ),
        "note": (
            "<b>Real-time, and nothing unredacted is retained.</b> Because audio is transcribed as "
            "it is spoken, the transcript lands about three-tenths of a second after the caller "
            "hangs up &mdash; the same working assumption as the current stack. The model returns "
            "<code>{summary}</code> and nothing else, so there is no redacted transcript to store "
            "and no entity list to leak; local post-processing replaces any regex-detected literal "
            "that reaches the summary. Only that PII-safe record crosses into analytics."
        ),
    },
}

for filename, spec in pages.items():
    html = PAGE.format(
        style=STYLE,
        height=HEIGHT,
        tail=fabric_tail(TAIL_TOP),
        **spec,
    )
    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    (OUT / filename).write_text(html, encoding="utf-8")
    print("wrote", filename, len(html), "bytes")
