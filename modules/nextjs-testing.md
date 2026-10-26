---
name: nextjs-testing
title: Jest on pure client logic and an offline Playwright build
summary: Unit-test hooks and pure functions in jsdom with a 70 percent aggregate gate, and run Playwright against a production build plus in-memory mocks. Use this when a Next.js pull request needs a gate that never calls the real API, the auth project, or a third-party network.
stack: [nextjs, jest, playwright]
tags: [testing, jest, playwright, ci]
requires: []
related_skills: []
status: stable
updated: 2026-10-26
---

# Jest on pure client logic and an offline Playwright build

Two suites, one pull-request workflow. Jest runs in jsdom on hooks, client contexts, and pure modules. Playwright drives a production server whose API and auth hosts are local mocks. Lint and a separate production build sit beside that chain. None of the jobs talk to a real backend.

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

## Coverage gate

`coverageThreshold.global` is 70 for branches, functions, lines, and statements. That number is the aggregate of `collectCoverageFrom`, not a per-file floor. Reporters are `text`, `lcov`, and `html`.

Collect hooks, client contexts, config, shared types, and pure modules (mappers, transforms, entitlement math, stream parsers, consent, formatting). Collect only the top level of a utils folder so server clients that live in a nested utils path stay out.

Negate, inside those globs, every server-only file: session helpers, machine-to-machine signing, and loaders that fetch. A directory glob would otherwise count them. Feature pages and design-system primitives stay out of the denominator. Playwright exercises the pages. Component tests still run and can fail CI; they are outside the percentage. Including pages in the denominator would make the 70 percent gate a measure of untested UI.

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

Three web servers. Mock API and mock auth time out at 30s. The Next server times out at 300s.

| Process | Ready URL | Role |
| --- | --- | --- |
| Mock API | `http://127.0.0.1:<port>/health` | Answers server actions and Server Components. In-memory store, one copy per role. |
| Mock auth | its `/health` | Accepts the synthetic session. No real auth project. |
| Next | the app origin | `rm -rf .next/cache && pnpm run build && pnpm run start` |

Ports default to 3100 (Next), 8099 (API), and 8055 (auth). Override with `INTEGRATION_NEXT_PORT`, `MOCK_BACKEND_PORT`, and `MOCK_SUPABASE_PORT`.

Server actions and Server Components use `BASE_URL` (the mock API). Browser calls that hit this app's own route handlers are intercepted with `page.route()`: a send, a long-lived stream, an item create, and an upload PUT. The mock API is never the target of those browser routes.

The fake session is a `storageState` file. The cookie name is `sb-<host>-auth-token`, where `<host>` is the mock auth hostname, matching the SSR client's derivation. The value is `base64-` plus base64url JSON. Values longer than 3180 characters are split into `.0`, `.1`, … chunks, which is the size the SSR client uses. A non-httpOnly hint cookie is written beside it so a landing redirect that reads the hint still runs. The mock API reads the role from the bearer token the server action forwards. It does not check the signature.

`POST /__reset` on the mock API drops the in-memory stores. Specs that create, update, or delete call it in `beforeEach`. Specs that also cache in `sessionStorage` clear that store with an init script. Reset is per process, not per worker. That is why P27 is one worker, not a lock inside the spec.

## Web-server env

The harness sets these keys on the Next process. CI has no local env file. Names only:

| Name | What the placeholder must do |
| --- | --- |
| `PORT`, `BASE_URL` | Next port, and the mock API origin |
| `SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL` | The mock auth origin |
| `SUPABASE_ANON_KEY`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Any non-empty string. The mock does not verify it |
| `NEXT_PUBLIC_SITE_URL` | The Next origin |
| `NEXT_PUBLIC_BETA_TESTING` | The string `false`, so the invite gate stays off and matches a runner with no local env file |
| `NOTION_API_KEY`, `LOOPS_API_KEY` | Empty strings. Next keeps a variable that is already in the process environment, so an empty value beats a key in `.env.local` |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID` | Shapes that satisfy import-time clients. Never contacted |
| `M2M_JWT_PRIVATE_KEY` | A throwaway PKCS#8 key generated for the harness. The mock does not verify the signature. Not the production key |
| `NODE_OPTIONS` | `--max-old-space-size=4096` |

`NODE_OPTIONS` is set on this web server because the build is a child of Playwright (P26). The workflow env sets the same value so lint, Jest, the build job, and Playwright inherit it. The hosted runner heap is about 2 GB. `next build` dies there on a Next 16 typecheck without the 4096 MB cap.

The server command is `next build` then `next start`. `next dev` compiles on first hit and the suite times out (P26). `.next/cache` is removed first. The Data Cache from a previous run would otherwise replay for the production revalidate window and hide fresh mock data. `reuseExistingServer` is false when `CI` is set, so CI always boots a new stack.

## Pull-request workflow

Trigger: pull request to `main`. Node 20. `pnpm/action-setup` runs before `actions/setup-node` with `cache: pnpm`, and the pnpm version comes from the `packageManager` field. Install with `pnpm install --frozen-lockfile`.

```yaml
NODE_VERSION: "20"
NODE_OPTIONS: --max-old-space-size=4096
```

| Job | Needs | Command |
| --- | --- | --- |
| Unit | — | `pnpm exec jest --coverage --ci --reporters=default --reporters=github-actions` |
| Integration | Unit | `pnpm exec playwright install --with-deps chromium`, then `pnpm run test:integration` |
| Lint | — | `pnpm run lint` |
| Build | — | `pnpm run build` |

Chromium only. Firefox and WebKit are not installed. The integration script is `playwright test -c <integration config>`. A desktop-only script exists for local use and is not the CI command. Storybook is not a job.

The build job evaluates payment and auth clients at import. Set the same Stripe and Supabase names as the table above, plus `NEXT_PUBLIC_SITE_URL=http://localhost:3000`, to non-secret placeholders. Point Supabase URLs at a host that is not a real project. Do not copy a live project URL into the workflow.

Coverage upload uses `fail_ci_if_error: false` and reads `coverage/lcov.info`. The HTML report is uploaded with `if: always()` (30 days). Failure screenshots are uploaded with `if: failure()` and `if-no-files-found: ignore`. The build job uploads `.next` for one day. Those uploads are not gates.

`pnpm run test:ci` (coverage, then Playwright) is a local convenience. CI splits them so the integration job can `needs` the unit job.

## Failure prevention

- **P26.** Workflow env and the Playwright web-server env both set `NODE_OPTIONS=--max-old-space-size=4096`. Integration starts `pnpm run build && pnpm run start` after deleting `.next/cache`. The command does not contain `next dev`.
- **P27.** CI `workers` is 1 while `fullyParallel` is true. One mock process holds the rows. Specs that mutate call `POST /__reset` in `beforeEach`. Local parallel runs isolate that store per worker.
- The invite flag in the web-server env is the string `false`. `true` turns the gate on and the signed-in specs stop at the invite screen. `invite-code-gate` is the rule for that flag.
- The axe matcher is registered and unused. An accessible-name bug is not caught by Jest. `content-security-policy` and the page tests are where that shows up, and only if a spec asserts it.
- `coverageThreshold.global` can hide an untested pure function when the rest of the set is high. A new pure module still belongs in `collectCoverageFrom`. A new server loader belongs in the negation list.
- The throwaway signing key and the placeholder payment keys stay in the harness. They are not production secrets and they are not pasted into a second module.

## Tests of the harness

- With `CI=1`, the Playwright config reports `workers === 1`, and the web server command contains `pnpm run build && pnpm run start` and does not contain `next dev`.
- Jest's ignore list includes the Playwright directory, and a file under that directory is not collected.
- `collectCoverageFrom` includes the hooks directory and negates the session and machine-to-machine modules.
- A spec that mutates the mock, skips reset, and runs after another mutator in the same worker sees the previous spec's row.
- The unit job command includes `--coverage`. A collected set under 70 percent exits non-zero.

## Checklist

- [ ] CI Jest is `pnpm exec jest --coverage --ci` with no path filter, and `coverageThreshold.global` is 70 on the pure-logic set.
- [ ] Server-only files are negated in `collectCoverageFrom`. Feature pages are left out of that set and covered by Playwright.
- [ ] Playwright is offline: mock API, mock auth, and `page.route()` for browser calls. `NEXT_PUBLIC_BETA_TESTING` is `false`. Provider keys are placeholders.
- [ ] `NODE_OPTIONS=--max-old-space-size=4096` is on the workflow and on the Playwright web server. The server command deletes `.next/cache` and runs `pnpm run build && pnpm run start` (P26).
- [ ] With `CI=1`, `workers` is 1, `reuseExistingServer` is false, `forbidOnly` is true, and retries are 0 (P27).
- [ ] Mutating specs reset the mock in `beforeEach`. The paid role and the signed-out state are `test.use`, not the project default.
- [ ] Integration `needs` the unit job. Lint and the import-time build do not. Playwright installs Chromium only.
- [ ] Coverage upload does not fail the job. The signing key in the web-server env is throwaway and is not the production private key.
