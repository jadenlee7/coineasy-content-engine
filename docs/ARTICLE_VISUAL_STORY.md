# Article visual story package

Each generated article carries one hero banner and two inline editorial
visuals. The package is designed for daily publishing without adding another
image-provider secret.

## Output

- Hero: `1200x630`, used as the article and social preview banner.
- Inline visual 1: `1200x675`, placed after the first section.
- Inline visual 2: `1200x675`, placed after the third section when available.
- Every image is available as a PNG download and a native-layer SVG for Figma.

The article model returns two bounded visual briefs. Each brief contains a
controlled motif, headline, caption, and two to four source-grounded points.
Only these motifs are accepted:

- `network`
- `layers`
- `flow`
- `signal`
- `event`
- `asset`

If the visual brief is missing or invalid, the server derives a deterministic
brief from the immutable article sections and takeaways. A visual-only model
miss therefore does not turn a valid article into an HTTP 502.

## Durability

Visual briefs are stored inside the immutable article content version.
`/api/article-visual/:contentId/:visualId` rebuilds `hero`, `visual-1`, or
`visual-2` from that stored version. This makes old and new articles
re-downloadable from the team library without retaining temporary files.

The endpoint:

- requires the HttpOnly Studio session;
- accepts only an article content UUID and an allowlisted visual ID;
- reads source evidence from the server-side catalog;
- returns an SVG with `Cache-Control: private, no-store`;
- never accepts a browser-supplied visual prompt.

## Source fidelity

Visual copy follows the same factual boundary as the article:

- no new facts, entities, numbers, URLs, hashtags, or claims;
- no speculative price charts;
- no image-provider prompt or external generation call;
- stored articles remain `needs_review` until a team member reviews them.
