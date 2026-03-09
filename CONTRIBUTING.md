# Contributing to PX Dictate

Welcome! PX Dictate is the first open-source project from [PX Innovative Solutions Inc.](https://github.com/pxinnovative), and we're excited to build it with the community.

Whether you're fixing a typo, squashing a bug, or proposing a new feature — every contribution matters.

---

## How to Contribute

1. **Fork** this repository
2. **Create a branch** for your change:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Read the Code Rules below** — PRs that don't follow them will not be merged
4. **Make your changes** and test them locally on macOS
5. **Commit** with a clear message describing what and why
6. **Push** your branch and open a **Pull Request**

Please open an issue first if your change is significant — it helps avoid duplicate work and align on direction.

---

## Code Rules

These rules are **non-negotiable**. Every PR is reviewed against them. Code that doesn't follow these rules will be rejected regardless of what it does.

### 1. Zero Hard-Coding

**No magic numbers. No hardcoded paths. No unexplained constants.**

Every value that could change, that has meaning, or that appears more than once **must** be a named constant with a clear name.

```python
# BAD — what is 8? what is 180?
cmd = [cli, "-t", "8"]
result = subprocess.run(cmd, timeout=180)

# GOOD — clear intent, easy to find and change
WHISPER_THREADS = 8       # CPU threads for transcription
WHISPER_TIMEOUT = 180     # Max seconds before transcription is killed

cmd = [cli, "-t", str(WHISPER_THREADS)]
result = subprocess.run(cmd, timeout=WHISPER_TIMEOUT)
```

```python
# BAD — hardcoded path
log_file = "/tmp/my-app.log"

# GOOD — derived dynamically
LOG_DIR = os.path.expanduser("~/Library/Logs/PX Dictate")
LOG_FILE = os.path.join(LOG_DIR, "app.log")
```

This applies to: delays, thresholds, dimensions, keycodes, file paths, buffer sizes, color values — **everything**.

### 2. No Secrets, No Personal Data

- Never commit API keys, tokens, passwords, or credentials
- Never hardcode usernames, email addresses, or internal paths
- Environment variables or config files for anything user-specific
- If you find a secret in the code, report it immediately (see [SECURITY.md](SECURITY.md))

### 3. Clear and Honest Code

PX Dictate is a privacy-focused app. Users trust us with their microphone. That trust is sacred.

- **No hidden behavior** — the code must do exactly what it says, nothing more
- **No telemetry, analytics, or tracking** — not even "anonymous" usage data
- **No network calls** — unless explicitly requested by the user (e.g., future API mode)
- **No obfuscation** — code should be readable by anyone. If a reviewer can't understand what a function does in 30 seconds, it needs better naming or comments
- **Comments explain "why", not "what"** — the code itself should explain what it does

```python
# BAD — comment restates the code
x = x + 1  # increment x by 1

# GOOD — comment explains WHY
x = x + 1  # compensate for PyAudio's off-by-one in frame count
```

### 4. Security First

- **No `shell=True`** in subprocess calls
- **No string interpolation** in shell commands — use `shlex.quote()` or list arguments
- **Escape all dynamic values** in XML/plist/HTML generation
- **Clean up temp files** with `try/finally` — never leave audio data on disk
- **Validate all file paths** before operations — especially anything from user input

### 5. Keep It Simple

- No unnecessary dependencies — PX Dictate stays lightweight
- Prefer standard library over third-party when possible
- One function, one purpose — if a function does two things, split it
- No premature abstraction — three similar lines are better than a clever helper nobody understands

### 6. Test on macOS

- PX Dictate is a macOS app. Test every change on real hardware
- Test in **both light and dark mode**
- Test with **both fn and Ctrl+Opt+V hotkeys**
- Test with the **.app bundle**, not just `python3 px_dictate_app.py`
- If your change affects audio, test with actual microphone input

### 7. Consistent Style

- Follow existing code patterns — don't introduce new conventions
- Constants at the top, in UPPER_SNAKE_CASE, grouped by section
- Classes and functions in logical order (helpers before callers)
- Docstrings on all public classes and functions
- Type hints encouraged but not required

---

## What Makes a Good PR

- **Small and focused** — one feature or one fix per PR
- **Clear description** — explain what, why, and how to test
- **No unrelated changes** — don't "clean up" code you didn't need to touch
- **Screenshots** if it changes UI (light mode AND dark mode)
- **Tested locally** on macOS with the .app bundle

---

## Bug Reports

Found a bug? Open a [GitHub Issue](../../issues) with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Your macOS version and hardware (Intel vs Apple Silicon)
- Console output or log file (`~/Library/Logs/PX Dictate/`)

## Feature Requests

Have an idea? Open a [GitHub Issue](../../issues) with the `enhancement` label:

- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

---

## Code of Conduct

Be respectful. Be constructive. Be kind. We're building something together, and a welcoming community is the foundation. Harassment, trolling, or disrespectful behavior will not be tolerated.

## Contributor License Agreement

By submitting a PR, you agree that your contributions are licensed under AGPL-3.0, consistent with the project's license.

## Trademark

"PX Dictate" is a trademark of PX Innovative Solutions Inc. If you fork this project, you must rename your version. See [TRADEMARK.md](TRADEMARK.md) for details.

## Contact

- **GitHub Issues** — preferred for bugs, features, and questions
- **Email** — github@pxinnovative.com

---

Thanks for contributing to PX Dictate. Let's make voice-to-text free and private for everyone.
