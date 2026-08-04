---
name: account-operations
description: Plan and execute auditable social-account operations through the project-local OpenCLI browser bridge. Use for Today in History curation, ChatGPT-web content or image generation, and X or Reddit workflows initiated by the Video-Promotional account-operations scheduler.
---

# Account Operations

Use only the repository wrapper `scripts/opencli.sh`; never install or invoke a
global OpenCLI package. Request JSON output with `-f json` and use background,
persistent site sessions for scheduled work.

## Plan a Today in History post

1. Run the fixed planning wrapper. The scheduler supplies the content prompt in
   its environment:

   ```bash
   scripts/account-ops-opencli-plan.sh
   ```

2. Read the `response` field. When it is a usable JSON object containing the
   fields requested by the scheduler, preserve it without rewriting facts,
   dates, quotations, source notes, post text, or image prompt.
3. If the wrapper fails, times out, or returns unusable content, do not call
   another tool. Curate the requested JSON from the supplied prompt using only
   facts you can state with high confidence, and never claim web verification.
4. Return only that content JSON object in the final response.

When the task says it is the planning stage, stop after step 4. Do not generate
an image and do not create, edit, or delete any social post.

Run the wrapper once only. Never load the OpenCLI autofix or adapter-author
skills, inspect traces, or change files.

## Guard publishing

Perform a publishing action only when the task explicitly assigns publishing.
Before any X write, run `scripts/opencli.sh twitter whoami -f json` and compare
the returned username with the configured account handle. Refuse a mismatch.
Use `twitter post <text> --images <absolute-path> -f json` and preserve its
returned `id` and `url` for the run manifest. Never infer success from page
appearance when the adapter does not return a URL.

Keep each run idempotent: if the run manifest already contains a post URL, do
not post again. Do not expose cookies, browser profiles, credentials, or local
session storage in output.
