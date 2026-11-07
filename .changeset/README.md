# Changesets

This directory holds pending version-bump instructions for the publishable
JavaScript packages. Each note names the affected packages and the size of the
change. `pnpm version-packages` consumes the notes and updates package versions;
the configuration disables generated changelogs.

Add a note for every consumer-visible change to a publishable package; tests,
stories and internal refactors need none. The bump is what the change asks of
the package's consumers, under 0.x semantics while the group is below 1.0:
`minor` when they must change code (a removed or renamed export, prop, key or
contract, or input that now fails) or gain public API, and `patch` for a fix
that keeps the contract. `major` is reserved for the 1.0 release.

The five publishable `@angee` packages are a fixed group because they form one layered release surface: leaf changes can affect every package above them, so their versions advance together.

On every push to `main` the [release workflow](../.github/workflows/release.yml)
opens or updates the Version Packages pull request from pending notes, once the
repository lets GitHub Actions create pull requests; it publishes only when
`NPM_TOKEN` is configured. Source workspaces do not need that automation to use
the packages. The pinned Changesets CLI is owned by
the root `changeset` script; the version and release scripts delegate to it.
