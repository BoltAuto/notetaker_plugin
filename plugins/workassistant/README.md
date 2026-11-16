# workassistant

Syncs Claude Meetings transcripts into workAssistant. Say "sync my meetings" or run `/workassistant:sync-meetings`.

- Needs `python` 3.10+ on your PATH.
- Connect once per computer: workAssistant web app → **Access tokens** → create a key with the `remember` scope →
  paste the **Claude plugin** line when the skill asks. The key is kept in `~/.workassistant/config.json`, never in
  the plugin folder or the repo.
- For hourly syncing, create a Cowork scheduled task with the prompt "sync my meetings".
- Meetings deleted in workAssistant are never uploaded again.

Install in Claude Code: `/plugin install workassistant --marketplace BoltAuto/notetaker_plugin` (2.1.275 or later).
Other ways and details: the README at the root of the marketplace repo, `BoltAuto/notetaker_plugin`.
