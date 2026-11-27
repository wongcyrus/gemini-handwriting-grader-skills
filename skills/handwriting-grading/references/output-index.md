# Private offline teacher output index

Maintain `exam.output_dir/index.html` yourself after the output directory exists.
It is an assistant-maintained navigation guide, not a CLI artifact, publication
mechanism or approval. It opens locally without a server or internet. Treat both
the index and all linked materials as private.

## Refresh contract

Inspect actual files, manifests and current hash-bound approvals on resume and
after every output creation, edit, failure, archive or cleanup. Refresh before
reporting the stage result, link the page, and tell the teacher how to open it.
Recheck after review saves, recovery, child runs, exports and cost summaries, not
only successful stages. An unsuccessful stage can leave partial outputs or no
output; say which occurred. Do not turn index maintenance into any gated operation.

Show recommended current deliverables first, then group other existing outputs
by stage, version and child run. Useful collections include review workbooks,
standard reports, statistics, advanced reports, distribution previews/exports,
archives and cost summaries. Give each link a short plain-language description
and any review/sharing warning. Label **partial**, **failed**, **stale**,
**unverified**, **superseded**, or **unavailable** explicitly. Presence/completion
does not establish correctness or approval; an old output is not synchronized
with new reviews. Never link absent files or publish nonexistent future outputs.

Link a folder for per-student collections. Do not enumerate student names, IDs,
answers, feedback, credentials, bearer-token review URLs or confidential content.
AI-generated analysis/images require verification before sharing. Do not embed
those contents into the guide.

## HTML and content preservation

- Use a self-contained page with meaningful headings, keyboard-accessible links,
  readable text, and no scripts, external styles/fonts or remote resources.
- Resolve links relative to the index, URL-encode each path component, and HTML
  escape labels and attribute values. Do not emit absolute filesystem paths or
  `file://` links. Link only intended outputs inside the private output tree.
- Own only the single section delimited by
  `<!-- handwriting-grading:index:start -->` and
  `<!-- handwriting-grading:index:end -->`. Preserve all user content outside
  it verbatim. Create a new page when none exists.
- If an existing page has no single unambiguous managed section (missing,
  duplicated, reversed or nested markers), preserve it unchanged. Explain the
  conflict and obtain permission before changing it or using a fresh alternate
  filename; do not silently replace it.
- If an older assistant-managed Markdown guide exists, preserve user content
  outside its unambiguous managed section; mark only that section superseded
  with a link to the HTML guide. Never alter unmanaged Markdown.
- Never modify underlying outputs, manifests or approvals to update navigation.

After separately authorized cleanup, verify preserved sources, region/grading
images, review/history and deliverables; recheck archive and output links. Mark
removed artifacts unavailable instead of retaining broken links. If the guide
cannot be safely written, report the maintenance error and actual output links
directly. Never hide a stage failure behind a successful index update.
