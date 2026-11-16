# workAssistant plugin for Claude

Copies your finished Claude Meetings transcripts into workAssistant, word for word. Works in Claude Code and in
Cowork on your own computer (not in claude.ai chat or cloud-run tasks).

## Requirement

`python` (3.10 or newer) on your PATH. The plugin runs a small local script with it.

## Install

- **claude.ai / Cowork:** Customize > Plugins > Add marketplace `BoltAuto/notetaker_plugin`, turn on
  "Sync automatically", then install the `workassistant` plugin.
- **Claude Code:** `/plugin marketplace add BoltAuto/notetaker_plugin`, then
  `/plugin install workassistant@workassistant`. On Claude Code 2.1.275 or later, one step does both:
  `/plugin install workassistant --marketplace BoltAuto/notetaker_plugin`.

Updates arrive on their own.

## Connect (once per computer)

1. In the workAssistant web app, open **Access tokens** and create a key with the `remember` scope. Use a key only
   for this plugin.
2. Copy the **Claude plugin** line: `Connect workAssistant: URL … key …`.
3. In any Claude Code or Cowork session, say "sync my meetings" and paste the line when asked.

The key is saved in `~/.workassistant/config.json` on your computer, and every later session uses it. A pasted key
stays in that session's history. To change the key, paste a new line in any session.

Never put an API key in this repo or in the plugin folder.

## Sync

Say "sync my meetings" (or run `/workassistant:sync-meetings`). It uploads every finished meeting from the last 14
days that workAssistant doesn't have yet, and reports one line per meeting. Running it again uploads nothing twice.
Meetings still running are skipped until they end.

To sync every hour, create a Cowork scheduled task that runs hourly with the prompt "sync my meetings".

A meeting you delete in workAssistant stays deleted: later syncs never upload it again.

If the report ends with "workAssistant plugin: Claude Meetings format changed", forward that block to the plugin's
maintainer. It names only tools and fields, never what was said.
