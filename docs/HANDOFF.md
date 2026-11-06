# Development handoff

Start by reading AGENTS.md and the decision register. Run the offline tests and demo. The package is a new repository root, not a patch to the ChatGPT project mirror.

Suggested initial development request:

> Implement M1 from docs/MILESTONES.md. Preserve all confirmed decisions. Add trusted authentication context and scoped application services, versioned migrations and backup/restore checks. Keep the offline demo runnable and do not add live trading, cloud dependencies or real market-data integrations. Test cross-user denial before opening any network interface. Explain implementation choices and remaining limitations.

Initialize version control after extracting the ZIP into a new directory:

```sh
git init
git add .
git commit -m "Initialize local-first portfolio agent foundation"
```

Before public release select a license and review CI action versions under the organization's dependency policy. No remote repository, credentials or deployment has been created by this package.
