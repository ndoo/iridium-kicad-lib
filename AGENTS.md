# Agent guidelines

Read [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md) first and follow them.

## Repository

- Original source only. Do not add proprietary or third-party material (see Scope in CONTRIBUTING.md).
- Keep footprint and symbol generation deterministic.

## Verification

- Run the checks listed under "Running CI checks locally" in CONTRIBUTING.md before reporting a change as working.
- If a tool is unavailable (`kicad-cli`, `7z`, `rsvg-convert`, Docker), report that the step was not run. Do not imply it passed.

## Commits and pull requests

- Follow CONTRIBUTING.md: Conventional Commits, `Signed-off-by`, and `Assisted-by: <model> <noreply@anthropic.com>` before it. Do not use `Co-Authored-By`.
- Write the commit message from the staged `git diff`, not from the conversation. Use the imperative mood for the verbs that name the change.
- Commit messages describe the change only. Test and verification status goes in the pull request's Build-verified section.
- Do not add an AI attribution line to pull request or issue bodies.
- Do not hard-wrap pull request or issue body text; write each paragraph as one line.
- Do not post comments or reviews to GitHub or any other external service without showing the draft to the user and getting approval.

## Releases

- Every release gets written notes in its body, as described under Releases in CONTRIBUTING.md. Do not leave the body empty.
- Show the draft notes to the user and get approval before setting them.
- After the tag's workflow finishes, check that the zip and the render files are attached.

## Style

- Comment only a non-obvious reason or a caveat. Do not describe what the code does. Keep each comment to one line, and put longer rationale and references in the commit message.
- State facts and technical rationale directly. Avoid promotional or hedging words ("cleanly", "simply", "robust", "elegant", "just works"). If removing a word does not change the meaning, remove it.
- Prefer a plain description of the mechanism to a coined intensifier: "the branch is never taken", not "flat-out dead".
