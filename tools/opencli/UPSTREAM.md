# OpenCLI integration

- Upstream: `jackwener/opencli`
- Project-local package: `@jackwener/opencli@1.8.6`
- Runtime install: `npm install --prefix tools/opencli`
- Wrapper: `scripts/opencli.sh`
- Upstream skills copied project-locally to `.claude/skills/opencli-*`
- Reproducible postinstall patch: `patch-opencli.mjs` adds Gemini local-image
  upload support for rendered-frame A/V review. The patch is pinned to 1.8.6
  and fails installation if its upstream anchors change.

No global OpenCLI npm package or global Claude Code skill is required.
