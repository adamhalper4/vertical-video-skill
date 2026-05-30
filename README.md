# vertical-video-skill — Claude skill bundles

Install bundles produced by [`/av-vertical-video`](https://github.com/) Stage H. Each `.zip` is a self-contained Course-Creator-style Video Agent skill primitive:

- `skill.md` — system prompt
- `manifest.yaml` — input contract
- `exemplars.json` — anchor catalogue
- `README.md` — install instructions
- `looks/*.png` — Image-N look plates

## Install

```bash
curl -L https://raw.githubusercontent.com/adamhalper4/vertical-video-skill/main/automotive/claude_skill_bundles/automotive__employee-training-ld.zip \
  -o /tmp/bundle.zip
unzip /tmp/bundle.zip -d ~/.claude/skills/
```

Or in Claude Code: paste the raw URL into a chat with `/install-skill <url>`.
