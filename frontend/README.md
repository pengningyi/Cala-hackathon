# Frontend

Next.js and React application for the working RoundCloser API. Requires Node 20.9+
and a separately running backend on port 8000.

```powershell
npm.cmd ci
npm.cmd run dev
```

On macOS/Linux use `npm` instead of `npm.cmd`. Open <http://localhost:3000>.
The production build is independent of the backend:

```powershell
npm.cmd run typecheck
npm.cmd run build
npm.cmd start
```

The browser calls `/api` on its own origin. Next.js proxies those requests to
`BACKEND_API_URL` (default `http://127.0.0.1:8000`), configured in
`next.config.ts`. If running the backend elsewhere, set this server-side variable
before starting/building the frontend. Never put Cala or model keys in this app.

All input validation decisions, ranking, eligibility, observations, considerations
and drafts come from the backend. The client presents results and copies drafts;
it does not send email or enrich investor contacts.

The knowledge graph uses circles for organizations and squares for people.
Funding rounds are dated, sourced connections to the funded company, rather than
nodes. Person connections come from retrieved professional roles and sourced
paths; an affiliation does not establish a personal relationship. Node size
reflects distinct neighbors in the displayed network. Click an entity or
connection to inspect its evidence, and click it again to clear the focus.
Dragging pins a node temporarily: nearby nodes move aside and the released node
springs back toward its hub. Double-click a node or use **Expand connections**
to make it a hub and reveal more of its already retrieved neighborhood. Collapse
removes connections that are no longer needed by another expanded hub.

Zoom changes the node presentation from small points to type icons to named
cards. The controls, mouse wheel and touch gestures adjust the view; the mobile
entity picker locates an individual person or organization. Motion pauses offscreen
and when settled, respects reduced-motion preferences, and adds no Cala/model calls.

The search-progress panel uses the local looping `public/obsidian-network.gif`.
It is served directly to preserve animation, with the supplied still image used
for reduced-motion preferences. Regenerate it with
`node scripts/generate-progress-network.cjs` using the existing Playwright/Edge
and Python/Pillow tools.

Graph evidence regression tests run without a browser or API credentials:

```powershell
npm.cmd run test:graph
```

Full browser checks use the running frontend and real backend, including Cala
discovery, live progress, graph selection, sourced chat, copying and exclusions:

```powershell
npm.cmd run test:e2e
```

On Windows the tests use installed Microsoft Edge. On other systems install the
test browser first with `npx playwright install chromium`. Screenshots are written
to the ignored `.qa/` directory. Test founder profiles and round values are
hypothetical; investor evidence comes from the real Cala connection.
