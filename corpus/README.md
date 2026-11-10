# Corpus

Insurer: ICICI Lombard General Insurance Company Limited. Product: Private Car Package Policy.

- `manifest.yaml` lists every document with source URL, role, dates and hash.
- `raw/` holds the downloaded files (PDFs saved from web pages must have a text layer).
- Preserve for every indexed source: title, source type, URL or filename, page or section,
  PDF creation date, retrieval date.

## Precedence rule (prototype only, not legal advice)

The detailed policy wording takes precedence over webpage summaries for interpreting policy terms,
subject to the applicable policy schedule and endorsements. Operational webpages and turnaround
material guide process but must not silently override the wording. When sources disagree on a
timeline or on the repair-quality period, keep both citations and escalate.

## Known conflicts (keep them as test cases)

- 15-day report timeline: Chapter 6 (from the assessment or survey) vs the Citizen Charter (after the insurer's request).
- 7-day decision: Chapter 6 (from the survey report) vs the Charter (communication, start point unstated).
- Repair-quality period: Chapter 6 (6 months or 6,000 km) vs the Submit & Track page (24 months or 10,000 km).
- Usage limit: the summary table on page 49 paraphrases Chapter 4 more loosely.
