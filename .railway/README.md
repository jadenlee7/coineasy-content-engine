# Railway Infrastructure as Code

`.railway/railway.ts` is a named Railway IaC partial for the two services this
repository owns directly:

- `coineasy-content-engine`
- `coineasy-managed-inspect`

The program fails closed unless Railway supplies the exact production project
and environment IDs and names recorded in the file and evaluates it as a
`plan`. A similarly named or mislinked project cannot evaluate the service
graph, and a direct `railway config apply --file` is refused. Only a separately
approved pinned-plan apply can cross that boundary.

The `noble-illumination` project also contains resources owned by other
repositories and five services that remain bound to their existing named
Config-as-Code files. Do not remove or rename the exported `partial` value and
do not replace this file with a whole-project import. Omitted resources in a
whole-project definition can become delete intent.

The partial deliberately declares the complete GitHub source, variable-name
inventory, build settings, replicas, and relevant deploy/networking settings
for both owned services. Existing variable values use `preserve()`; secret
values must never be pulled, printed, or committed. `configFile` is omitted so
both services retain `railwayConfigFile=null` instead of opting into deprecated
Config as Code. Automatic deployment is not represented by the current IaC SDK
and must remain disabled through a separate owner-system readback.

## Review and rollout

Run the offline contract test first:

```sh
node --test tests_js/railway-iac.test.mts
```

After authenticating the Railway CLI to the exact production project and
environment, preview only. The checked-in gate consumes raw runner JSON from
stdin and emits only bounded allow-listed metadata:

```bash
set -o pipefail
test "$(railway --version)" = "railway 5.45.5"
railway config plan --file .railway/railway.ts --json \
  | node scripts/validate_railway_iac_plan.mjs
```

The plan must have no diagnostics, deletes, destructive changes, additions, or
changes outside the two named services. Never run raw `--json` without this
direct pipe, and never print or store that raw stream. Never use `--show-values`
or `--decrypt-variables`.

An apply-time review must create an owner-only pinned plan from a clean,
approved commit and bind the approval to its commit SHA, `.railway` source-tree
SHA, plan SHA-256, and returned `configEtag`. Keep the artifact outside the
repository. The review ceremony uses this sequence and records only the gate's
bounded output plus the three hashes:

```bash
set -euo pipefail
test "$(railway --version)" = "railway 5.45.5"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
test ! -e railway.json
test ! -e railway.toml
test "$(find .railway -maxdepth 1 -type f \
  \( -name 'railway.ts' -o -name 'railway.py' -o -name 'railway.go' \) \
  -print)" = ".railway/railway.ts"
PLAN_PATH="$(mktemp "${TMPDIR:-/tmp}/coineasy-railway-plan.XXXXXX")"
chmod 600 "$PLAN_PATH"
trap 'rm -f "$PLAN_PATH"' EXIT HUP INT TERM
COMMIT_SHA="$(git rev-parse HEAD)"
SOURCE_TREE="$(git rev-parse HEAD:.railway)"
if ! railway config plan --file .railway/railway.ts --json \
  --source-tree "$SOURCE_TREE" --out "$PLAN_PATH" \
  | node scripts/validate_railway_iac_plan.mjs; then
  exit 1
fi
test -z "$(git status --porcelain=v1 --untracked-files=all)"
test ! -e railway.json
test ! -e railway.toml
test "$(find .railway -maxdepth 1 -type f \
  \( -name 'railway.ts' -o -name 'railway.py' -o -name 'railway.go' \) \
  -print)" = ".railway/railway.ts"
test "$(git rev-parse HEAD)" = "$COMMIT_SHA"
test "$(git rev-parse HEAD:.railway)" = "$SOURCE_TREE"
PLAN_SHA256="$(shasum -a 256 "$PLAN_PATH" | awk '{print $1}')"
printf 'commit_sha=%s\nsource_tree_sha=%s\nplan_sha256=%s\n' \
  "$COMMIT_SHA" "$SOURCE_TREE" "$PLAN_SHA256"
trap - EXIT HUP INT TERM
```

Railway CLI `5.45.5` is the exact version used for this rollout contract; a CLI
change requires another read-only compatibility plan before use. Delete the
artifact if approval is denied or expires.

After a separate explicit production approval, apply that same artifact with
`railway config apply --plan "$PLAN_PATH"`; do not re-evaluate the authoring
file. Railway must reject the pinned plan if live state has drifted. Never use
`--confirm-destructive`. `railway config apply` is intentionally absent from
CI. Before the approved apply, fail closed unless the current checkout and
artifact still match the values named in that approval:

```bash
set -euo pipefail
test "$(railway --version)" = "railway 5.45.5"
test -n "${APPROVED_COMMIT_SHA:-}"
test -n "${APPROVED_SOURCE_TREE_SHA:-}"
test -n "${APPROVED_PLAN_SHA256:-}"
test -n "${PLAN_PATH:-}"
test -f "$PLAN_PATH"
test ! -L "$PLAN_PATH"
trap 'rm -f "$PLAN_PATH"' EXIT HUP INT TERM
test -z "$(git status --porcelain=v1 --untracked-files=all)"
test ! -e railway.json
test ! -e railway.toml
test "$(find .railway -maxdepth 1 -type f \
  \( -name 'railway.ts' -o -name 'railway.py' -o -name 'railway.go' \) \
  -print)" = ".railway/railway.ts"
test "$(git rev-parse HEAD)" = "$APPROVED_COMMIT_SHA"
test "$(git rev-parse HEAD:.railway)" = "$APPROVED_SOURCE_TREE_SHA"
test "$(shasum -a 256 "$PLAN_PATH" | awk '{print $1}')" \
  = "$APPROVED_PLAN_SHA256"
railway config apply --plan "$PLAN_PATH"
rm -f "$PLAN_PATH"
trap - EXIT HUP INT TERM
```

Merge, apply, deployment, enablement, and public/private delivery are
independent approval gates. Immediately before merge, read back
`autoDeploy=false` for both owned services and all five legacy-bound services
sourced from this repository; block if any source trigger is enabled. After a
merge removes root `railway.json`, do not manually redeploy either service
until the named partial has been applied and the resulting service settings
have been read back.
