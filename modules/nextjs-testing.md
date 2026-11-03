---
name: nextjs-testing
title: Jest on pure client logic and an offline Playwright build
summary: Unit-test hooks and pure functions with a 70 percent aggregate gate, then run Playwright and production builds against local API, auth, and Notion CMS fixtures. Use this when server-side publication must be exercised in offline CI without real provider credentials.
stack: [nextjs, jest, playwright]
tags: [testing, jest, playwright, ci, notion, cms]
requires: []
related_skills: []
status: stable
updated: 2026-11-03
---

# Jest on pure client logic and an offline Playwright build

Two suites, one pull-request workflow. Jest runs on hooks, client contexts, pure modules, and parser/publication fixtures. Playwright drives a production server whose API, auth, and Notion CMS hosts are local mocks. Lint and a separate production build sit beside that chain; the build job starts its own CMS mock. These gates exercise application behavior without real backend or CMS credentials. Package/browser downloads and artifact uploads remain CI network operations.

The Jest config is created with `next/jest` (`createJestConfig({ dir: "./" })`) so Next transforms CSS and server-only modules. Do not hand-roll a transform that fights that wrapper.

## What Jest runs

Default discovery is `__tests__/**` plus `*.test` and `*.spec`. Ignore `node_modules`, `.next`, the Playwright tree, and a helpers directory that is not a suite.

| Script | What it collects |
| --- | --- |
| `test` / CI | Everything discovery finds |
| `test:unit` | Hooks, contexts, and pure-logic tests |
| `test:component` | Component tests under the component folder |
| `test:coverage` | Same as `test`, with the coverage gate |

CI runs `pnpm exec jest --coverage --ci` and adds the `default` and `github-actions` reporters. It does not pass the narrower path filters. A failing component test fails the job even though component files are outside the coverage set.

Setup, after the environment:

- `@testing-library/jest-dom` matchers.
- `jest-axe`'s `toHaveNoViolations` is registered. No test calls it, so accessibility is not a unit gate.
- `next/navigation` is mocked: `useRouter`, `usePathname` (`/`), `useSearchParams` (empty), `redirect`.
- `next/image` renders a plain `img`. The suite does not exercise the image optimizer.
- When `window` exists, `matchMedia` returns `matches: false`. `ResizeObserver` and `IntersectionObserver` are no-op classes.
- `console.error` drops the legacy `ReactDOM.render` warning and forwards everything else.

`testEnvironment` is `jsdom`. A pure module that must not see a DOM sets `@jest-environment node` on that file. The `matchMedia` stub is already behind `typeof window`, so that file still loads the setup.

The CMS parser suite uses the Node environment, mocks `server-only`, and stubs `global.fetch` with block-list responses. It restores fetch and the original env after each case. This tests the real parser, including required metadata, editorial dates, substantive body, draft bypass, unsupported public blocks, and the endpoint override. Collection tests inject the article loader to check skip/log, partial success, and all-failed empty results. Those are unit boundaries; they do not test a deployed cache. Publication semantics are in `notion-cms-publication`.

## Coverage gate

`coverageThreshold.global` is 70 for branches, functions, lines, and statements. That number is the aggregate of `collectCoverageFrom`, not a per-file floor. Reporters are `text`, `lcov`, and `html`.

Collect hooks, client contexts, config, shared types, and pure modules (mappers, transforms, entitlement math, stream parsers, consent, formatting). Collect only the top level of a utils folder so server clients that live in a nested utils path stay out.

Negate, inside those globs, every server-only file: session helpers, machine-to-machine signing, and loaders that fetch. A directory glob would otherwise count them. Feature pages and design-system primitives stay out of the denominator. Playwright exercises the pages. Component tests still run and can fail CI; they are outside the percentage. Including pages in the denominator would make the 70 percent gate a measure of untested UI.

The reference SEO parser/publication tests run through normal discovery, but their source directory is outside `collectCoverageFrom`. Their existence does not make the 70 percent gate a publication-coverage floor.

## What Playwright runs

Config file is separate from any default `playwright.config`. `testDir` is the integration tree. `fullyParallel` is true. `forbidOnly` is on when `CI` is set. Retries are 0 in CI and 1 locally, so `trace: "on-first-retry"` and `video: "on-first-retry"` record nothing on the CI path. Screenshots are `only-on-failure`. Expect timeout 10s, action timeout 15s, navigation timeout 30s.

Projects:

1. `setup` matches the fake-auth file and writes storage states. No network sign-in.
2. Desktop Chromium (`Desktop Chrome`) and a phone Chromium (`Pixel 5`) run only the specs directory. Both depend on `setup`. Both default to the limited-role storage state. A spec that needs the paid role, or no session, calls `test.use({ storageState })`.

One worker in CI (P27):

```ts
workers: process.env.CI ? 1 : undefined,
reuseExistingServer: !process.env.CI,
```

Locally `workers` is left unset, so Playwright uses its default (half the cores). `fullyParallel: true` with more than one worker still shares one mock process. Specs then delete rows another spec is reading, and a cached miss sticks for the cache TTL (P27). A local parallel run needs a fixture store per worker. CI `workers: 1` keeps the desktop project and the phone project serial even though both depend on the same setup.

## Mock stack

Four processes: three mocks and Next. Each mock has a 30s readiness timeout; Next has 300s, including its production build.

| Process | Ready URL | Role |
| --- | --- | --- |
| Mock API | `http://127.0.0.1:<port>/health` | Answers server actions and Server Components. In-memory store, one copy per role. |
| Mock auth | its `/health` | Accepts the synthetic session. No real auth project. |
| Mock Notion | its `/health` | Supplies valid synthetic blocks to server-side CMS reads, including build-time reads. |
| Next | the app origin | `rm -rf .next/cache && pnpm run build && pnpm run start` |

Ports default to 3100 (Next), 8099 (API), 8055 (auth), and 8098 (CMS). Override with `INTEGRATION_NEXT_PORT`, `MOCK_BACKEND_PORT`, `MOCK_SUPABASE_PORT`, and `MOCK_NOTION_PORT`.

Keep the three mock entries before the Next entry in `webServer`. The pinned reference Playwright runner awaits each server plugin's setup/readiness in array order, so the CMS is ready before Next's build starts. The [Playwright web-server documentation](https://playwright.dev/docs/test-webserver) defines the array, readiness URL, env inheritance, and server reuse options; recheck startup ordering when upgrading the runner. If the runner starts entries concurrently, add an explicit CMS readiness wait inside the build command. A readiness gate only before browser tests is too late for prerendering (P50).

Server actions and Server Components use `BASE_URL` (the mock API). Browser calls that hit this app's own route handlers are intercepted with `page.route()`: a send, a long-lived stream, an item create, and an upload PUT. The mock API is never the target of those browser routes.

The fake session is a `storageState` file. The cookie name is `sb-<host>-auth-token`, where `<host>` is the mock auth hostname, matching the SSR client's derivation. The value is `base64-` plus base64url JSON. Values longer than 3180 characters are split into `.0`, `.1`, … chunks, which is the size the SSR client uses. A non-httpOnly hint cookie is written beside it so a landing redirect that reads the hint still runs. The mock API reads the role from the bearer token the server action forwards. It does not check the signature.

`POST /__reset` on the mock API drops the in-memory stores. Specs that create, update, or delete call it in `beforeEach`. Specs that also cache in `sessionStorage` clear that store with an init script. Reset is per process, not per worker. That is why P27 is one worker, not a lock inside the spec.

## CMS fixture contract (P50)

The mock reads page-id mappings from the code catalogs without fetching CMS content. Its minimal protocol is:

| Request | Response |
| --- | --- |
| `GET /health` | JSON readiness response, no credential required |
| `GET /v1/blocks/<id>/children?page_size=100` | With the synthetic bearer credential and a known article id, a block-list envelope |
| Known legal-page id | Empty block list, preserving the harness's no-published-legal-version fixture |
| Missing/wrong bearer credential | 401 |
| Unknown id or unsupported API method/path | 404 |

The envelope has `object`, `results`, `has_more: false`, and `next_cursor: null`. Each article contains rich-text paragraphs for the exact Ready header and required editorial metadata, then a heading and substantive synthetic body. Use a deterministic valid date and generic author; the fixture must pass the production validator. Do not add a test-only parser bypass or restore an empty production key.

`NOTION_API_BASE_URL` is read by a server-only env getter, trims a trailing slash, and defaults to the vendor API outside the harness. The same HTTP client/parser is used for local and real endpoints; the override supplies no browser access or alternate content path. Keep it out of `NEXT_PUBLIC_*`. The standalone mock checks the expected synthetic bearer value, so an arbitrary nonempty key is insufficient.

Reference Playwright publication specs use the request fixture to assert a real article response contains its synthetic body, description, and expected section breadcrumb. They fetch `/sitemap.xml`, check every catalog article path and the exact count of article plus static locations. This couples fixture validity to the parser, cached collection, rendering, and discovery projection. It does not exercise a live CMS or remote cache service.

Limits: the reference mock emits one top-level page, no cursor continuation, no nested child blocks, and no injected transport/rate-limit/server failures. Its id extraction depends on catalog source formatting; a catalog refactor can silently produce fewer fixtures. Preserve independent expected-route/count assertions. Add cursor, nested-block, image-expiry, and failure fixtures in focused parser tests when relying on those paths; the happy-path HTTP mock alone does not cover them.

## Web-server env

The harness sets these keys on the Next process. CI has no local env file. Names only:

| Name | What the placeholder must do |
| --- | --- |
| `PORT`, `BASE_URL` | Next port, and the mock API origin |
| `SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL` | The mock auth origin |
| `SUPABASE_ANON_KEY`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Any non-empty string. The mock does not verify it |
| `NEXT_PUBLIC_SITE_URL` | The Next origin |
| `NEXT_PUBLIC_BETA_TESTING` | The string `false`, so the invite gate stays off and matches a runner with no local env file |
| `NOTION_API_KEY` | Nonempty synthetic credential matching the local CMS mock; production publication still checks for a key |
| `NOTION_API_BASE_URL` | Local CMS origin plus `/v1`, used at build time and runtime |
| `LOOPS_API_KEY`, `NEXT_PUBLIC_POSTHOG_KEY`, `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN`, `SENTRY_AUTH_TOKEN` | Empty to disable CRM, analytics, and error-reporting integrations even when local env files contain credentials |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID` | Shapes that satisfy import-time clients. Never contacted |
| `M2M_JWT_PRIVATE_KEY` | A throwaway PKCS#8 key generated for the harness. The mock does not verify the signature. Not the production key |
| `NODE_OPTIONS` | `--max-old-space-size=4096` |

`NODE_OPTIONS` is set on this web server because the build is a child of Playwright (P26). The workflow env sets the same value so lint, Jest, the build job, and Playwright inherit it. The hosted runner heap is about 2 GB. `next build` dies there on a Next 16 typecheck without the 4096 MB cap.

The server command is `next build` then `next start`. `next dev` compiles on first hit and the suite times out (P26). `.next/cache` is removed first. The Data Cache from a previous run would otherwise replay for the production revalidate window and hide fresh mock data. `reuseExistingServer` is false when `CI` is set, so CI always boots a new stack.

## Setup order

1. Define shared ports/origins and synthetic auth states; point API/auth/CMS env names at their local servers. Explicitly disable external telemetry and CRM.
2. Provide substantive Ready CMS fixtures and a matching synthetic bearer credential. Test the mock's health and authenticated block route separately.
3. Start and healthcheck all mocks before the production build. Clear the integration cache, build, start, and wait for Next's origin.
4. Run serial CI projects; reset mutable API state per spec. Keep read-only CMS fixture expectations independent of the fixture generator.
5. Configure the separate build job's CMS start/healthcheck/cleanup too. Playwright's processes do not exist in that job.

## Pull-request workflow

Trigger: pull request to `main` or `dev`. Node 20. `pnpm/action-setup` runs before `actions/setup-node` with `cache: pnpm`, and the pnpm version comes from the `packageManager` field. Install with `pnpm install --frozen-lockfile`.

```yaml
NODE_VERSION: "20"
NODE_OPTIONS: --max-old-space-size=4096
```

| Job | Needs | Command |
| --- | --- | --- |
| Unit | — | `pnpm exec jest --coverage --ci --reporters=default --reporters=github-actions` |
| Integration | Unit | `pnpm exec playwright install --with-deps chromium`, then `pnpm run test:integration` |
| Lint | — | `pnpm run lint` |
| Build | — | Start/healthcheck local CMS, then `pnpm run build`, then cleanup |

Chromium only. Firefox and WebKit are not installed. The integration script is `playwright test -c <integration config>`. A desktop-only script exists for local use and is not the CI command. Storybook is not a job.

The build job evaluates payment and auth clients at import. Set the same Stripe and Supabase names as the table above, plus the site origin, to non-secret placeholders. Its auth origin is a dummy host, not a running auth mock; this build is not an authenticated API flow. The job starts only the CMS mock, sets `NOTION_API_KEY` to its synthetic credential and `NOTION_API_BASE_URL` to its local `/v1` endpoint, saves the child PID, registers an EXIT trap, and waits with `curl --fail --retry 10 --retry-connrefused --retry-delay 1` on `/health` before building. The trap cleans up on success or build failure. It does not inherit Playwright's mock stack.

Coverage upload uses `fail_ci_if_error: false` and reads `coverage/lcov.info`. The HTML report is uploaded with `if: always()` (30 days). Failure screenshots are uploaded with `if: failure()` and `if-no-files-found: ignore`. The build job uploads `.next` for one day. Those uploads are not gates.

`pnpm run test:ci` (coverage, then Playwright) is a local convenience. CI splits them so the integration job can `needs` the unit job.

## Failure prevention

- **P26.** Workflow env and the Playwright web-server env both set `NODE_OPTIONS=--max-old-space-size=4096`. Integration starts `pnpm run build && pnpm run start` after deleting `.next/cache`. The command does not contain `next dev`.
- **P27.** CI `workers` is 1 while `fullyParallel` is true. One mock process holds the rows. Specs that mutate call `POST /__reset` in `beforeEach`. Local parallel runs isolate that store per worker.
- **P50.** Browser `page.route()` cannot intercept server-side CMS fetches during build or Server Component execution. Both build paths require the local CMS, a matching synthetic key, and a readiness gate before building. Missing production credentials must still fail. A configured loader that skips every failed article can return empty, so build success alone is insufficient: assert expected content and sitemap membership.
- The invite flag in the web-server env is the string `false`. `true` turns the gate on and the signed-in specs stop at the invite screen. `invite-code-gate` is the rule for that flag.
- The axe matcher is registered and unused. An accessible-name bug is not caught by Jest. `content-security-policy` and the page tests are where that shows up, and only if a spec asserts it.
- `coverageThreshold.global` can hide an untested pure function when the rest of the set is high. A new pure module still belongs in `collectCoverageFrom`. A new server loader belongs in the negation list.
- The throwaway signing key and the placeholder payment keys stay in the harness. They are not production secrets and they are not pasted into a second module.

## Tests of the harness

- With `CI=1`, the Playwright config reports `workers === 1`, and the web server command contains `pnpm run build && pnpm run start` and does not contain `next dev`.
- All three mock readiness entries precede the Next build entry; CMS env points at local `/v1`. Check ordering again against the installed runner after an upgrade.
- CMS `/health` is ready without auth, wrong credentials return 401, known article ids return valid blocks, legal fixtures return empty, and unknown ids return 404.
- The parser's fetch target uses the overridden base URL with one slash; changing env is observed at call time and restored after each unit case.
- A valid CMS article renders its synthetic body/description/breadcrumb; sitemap locations equal the known catalog plus static routes. An empty collection cannot pass those assertions.
- The standalone build starts and healthchecks its own CMS child and kills it on exit. With no production key the guard throws; the harness does not suppress it.
- Jest's ignore list includes the Playwright directory, and a file under that directory is not collected.
- `collectCoverageFrom` includes the hooks directory and negates the session and machine-to-machine modules.
- A spec that mutates the mock, skips reset, and runs after another mutator in the same worker sees the previous spec's row.
- The unit job command includes `--coverage`. A collected set under 70 percent exits non-zero.

## Checklist

- [ ] CI Jest is `pnpm exec jest --coverage --ci` with no path filter, and `coverageThreshold.global` is 70 on the pure-logic set.
- [ ] Server-only files are negated in `collectCoverageFrom`. Feature pages are left out of that set and covered by Playwright.
- [ ] Playwright application requests stay local: mock API/auth/CMS and `page.route()` for browser calls. `NEXT_PUBLIC_BETA_TESTING` is `false`. Telemetry/CRM envs are empty.
- [ ] Both production build paths start/healthcheck CMS first and use server-only `NOTION_API_BASE_URL` plus the matching synthetic `NOTION_API_KEY` (P50).
- [ ] Production validation remains enabled; rendered content and sitemap assertions fail on empty or invalid publication. Pagination/nesting/error coverage is checked separately.
- [ ] `NODE_OPTIONS=--max-old-space-size=4096` is on the workflow and on the Playwright web server. The server command deletes `.next/cache` and runs `pnpm run build && pnpm run start` (P26).
- [ ] With `CI=1`, `workers` is 1, `reuseExistingServer` is false, `forbidOnly` is true, and retries are 0 (P27).
- [ ] Mutating specs reset the mock in `beforeEach`. The paid role and the signed-out state are `test.use`, not the project default.
- [ ] Integration `needs` the unit job. Lint and the import-time build do not. Playwright installs Chromium only.
- [ ] Coverage upload does not fail the job. The signing key in the web-server env is throwaway and is not the production private key.
